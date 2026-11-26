"""分段代办授权领域服务。

维度建档人把某个维度的一段号码分配工作委托给接班人。这里集中保证：

* 授权、待办、草稿、正式使用记录和审计在同一把进程内锁下原子变更；
* 维度 + 号码才是唯一身份，不同维度的相同号码互不影响；
* 只有维度归属人或当前有效授权的接班人可写，撤销后接班人只读；
* 正式确认记录是历史事实，撤销授权不会改写或删除它；
* 乐观版本号让“确认号码”和“撤销授权”的并发提交只有一个基准版本成功。
"""
from __future__ import annotations

from datetime import datetime, timedelta
from threading import RLock
from typing import Any

from app.store import store

ACTIVE_STATUSES = {"待办理", "办理中", "有效"}
REVOKED_STATUS = "已撤销"
COMPLETED_STATUS = "已完成"

# 草稿/填写锁的默认保留时间。超时后锁不再阻止其他号码判断；
# 草稿内容仍保留，刷新后可恢复，且草稿从来不是正式占用。
DEFAULT_HOLD_SECONDS = 30


def now_text() -> str:
    return datetime.now().isoformat(timespec="seconds")


def pick(source: Any, *keys: str, default: Any = None) -> Any:
    """从 dict/对象中按多个中文或英文别名取值。"""
    if source is None:
        return default
    if not isinstance(source, dict):
        source = getattr(source, "__dict__", {})
    for key in keys:
        if key in source and source[key] not in (None, ""):
            return source[key]
    return default


def to_int(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field}必须是整数")
    text = str(value).strip()
    if not text:
        raise ValueError(f"{field}不能为空")
    try:
        # 兼容 “104.0” 这类表单/表格传值。
        return int(float(text))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field}必须是整数，当前为 {value!r}") from exc


class SegmentDelegationService:
    """维护授权、待办、号码使用记录和可恢复草稿。"""

    def __init__(self) -> None:
        self.lock = RLock()
        self.reset()

    # ------------------------------------------------------------------ setup
    def reset(self) -> None:
        """清空功能自有状态；维度归属会从维度建档表重新同步。"""
        self.owners: dict[str, str] = {}
        self.delegations: dict[int, dict[str, Any]] = {}
        self.usages: list[dict[str, Any]] = []
        self.drafts: dict[str, dict[str, Any]] = {}
        self.holds: dict[str, dict[str, Any]] = {}
        self.todos: dict[int, dict[str, Any]] = {}
        self.events: list[dict[str, Any]] = []
        self._delegation_seq = 0
        self._usage_seq = 0
        self._todo_seq = 0
        self._event_seq = 0
        self._sync_owners_from_dimensions()

    def _sync_owners_from_dimensions(self) -> None:
        for row in store.rows("dim_entry"):
            info = self.dimension_info(row.get("维度编号") or row.get("维度名称"))
            owner = pick(
                row,
                "建档人员", "归属人员", "维度归属人", "负责人", "owner", "operator",
            )
            # 当前演示库没有登录体系，种子维度默认由场景中的甲建档。
            self.owners[info["key"]] = str(owner or "甲")

    def dimension_info(self, dimension: Any) -> dict[str, Any]:
        text = str(dimension or "").strip()
        if not text:
            return {"key": "", "name": "", "row": None}
        for row in store.rows("dim_entry"):
            identities = {
                str(row.get("id", "")),
                str(row.get("维度编号", "")),
                str(row.get("维度名称", "")),
            }
            if text in identities:
                key = str(row.get("维度编号") or row.get("维度名称") or text)
                name = str(row.get("维度名称") or row.get("维度编号") or text)
                return {"key": key, "name": name, "row": row}
        return {"key": text, "name": text, "row": None}

    def remember_owner(self, dimension: Any, owner: str) -> str:
        info = self.dimension_info(dimension)
        owner_text = str(owner or "甲").strip() or "甲"
        if info["key"]:
            self.owners.setdefault(info["key"], owner_text)
            row = info.get("row")
            if row is not None and not row.get("建档人员"):
                row["建档人员"] = owner_text
        return self.owners.get(info["key"], owner_text)

    def owner_of(self, dimension: Any) -> str | None:
        info = self.dimension_info(dimension)
        if not info["key"]:
            return None
        if info["key"] in self.owners:
            return self.owners[info["key"]]
        row = info.get("row") or {}
        owner = pick(row, "建档人员", "归属人员", "维度归属人", "负责人", "owner")
        if owner:
            self.owners[info["key"]] = str(owner)
            return str(owner)
        if info.get("row") is not None:
            # 已在维度建档中存在但未记录建档人时，沿用演示默认归属。
            self.owners[info["key"]] = "甲"
            return "甲"
        return None

    # ----------------------------------------------------------------- helpers
    def _event(self, event_type: str, operator: str, message: str, **extra: Any) -> None:
        self._event_seq += 1
        event = {
            "id": self._event_seq,
            "type": event_type,
            "operator": operator,
            "message": message,
            "at": now_text(),
        }
        event.update(extra)
        self.events.append(event)

    @staticmethod
    def _active(d: dict[str, Any]) -> bool:
        return bool(d.get("active")) and d.get("status") not in {REVOKED_STATUS, COMPLETED_STATUS}

    def _used_numbers(self, dimension_key: str) -> set[int]:
        return {
            int(u["number"])
            for u in self.usages
            if u.get("dimension_key") == dimension_key and u.get("status") == "已确认"
        }

    def _range_numbers(self, d: dict[str, Any]) -> list[int]:
        return list(range(int(d["start"]), int(d["end"]) + 1))

    def _available_numbers(self, d: dict[str, Any]) -> list[int]:
        used = self._used_numbers(d["dimension_key"])
        return [n for n in self._range_numbers(d) if n not in used]

    def _find_delegation(self, ref: Any) -> dict[str, Any] | None:
        if ref is None:
            return None
        text = str(ref).strip()
        if text in self.delegations:
            return self.delegations[text]  # type: ignore[index]
        for d in self.delegations.values():
            if text in {str(d.get("id")), str(d.get("delegation_no")), str(d.get("授权编号"))}:
                return d
        return None

    def _find_usage(self, dimension_key: str, number: int) -> dict[str, Any] | None:
        for u in reversed(self.usages):
            if u.get("dimension_key") == dimension_key and int(u.get("number", -1)) == number:
                return u
        return None

    def _active_hold(self, dimension_key: str, number: int) -> dict[str, Any] | None:
        key = f"{dimension_key}:{number}"
        hold = self.holds.get(key)
        if not hold:
            return None
        expires = datetime.fromisoformat(str(hold["expires_at"]))
        if datetime.now() > expires:
            hold["expired"] = True
            return None
        hold["expired"] = False
        return hold

    def _assert_view(self, d: dict[str, Any], operator: str) -> dict[str, Any] | None:
        operator = str(operator or "").strip()
        if operator and operator in {d.get("owner"), d.get("delegate"), d.get("created_by")}:
            return None
        return {
            "code": "forbidden",
            "http_status": 403,
            "error": "FORBIDDEN_DIMENSION",
            "message": f"操作员 {operator or '未提供'} 无权查看维度「{d.get('dimension')}」的分段授权",
        }

    def _write_denial(self, d: dict[str, Any], operator: str) -> dict[str, Any] | None:
        operator = str(operator or "").strip()
        if operator == d.get("owner"):
            return None
        if operator == d.get("delegate"):
            if self._active(d):
                return None
            return {
                "code": "read_only",
                "http_status": 403,
                "error": "DELEGATION_REVOKED_READ_ONLY",
                "message": "代办授权已撤销，接班人只能只读查看旧办理记录，不能继续写入",
            }
        return {
            "code": "forbidden",
            "http_status": 403,
            "error": "FORBIDDEN_DIMENSION",
            "message": f"操作员 {operator or '未提供'} 不属于维度「{d.get('dimension')}」的归属人或接班人，越权操作已拒绝",
        }

    def _version_error(self, d: dict[str, Any], expected: Any) -> dict[str, Any] | None:
        if expected in (None, ""):
            return None
        try:
            expected_version = int(expected)
        except (TypeError, ValueError):
            return {
                "code": "validation_error",
                "http_status": 400,
                "message": "版本号必须是整数",
            }
        if expected_version != int(d["version"]):
            return {
                "code": "version_conflict",
                "http_status": 409,
                "error": "VERSION_CONFLICT",
                "message": (
                    f"授权版本已变化：提交基准 v{expected_version}，当前 v{d['version']}；"
                    "请刷新后按最新可用号码重新提交"
                ),
                "current_version": d["version"],
            }
        return None

    def _public_delegation(self, d: dict[str, Any], operator: str | None = None) -> dict[str, Any]:
        operator = str(operator or "").strip()
        used = sorted(self._used_numbers(d["dimension_key"]) & set(self._range_numbers(d)))
        available = self._available_numbers(d)
        draft_numbers = sorted({
            int(draft["number"])
            for draft in self.drafts.values()
            if draft.get("delegation_id") == d["id"] and not draft.get("consumed")
        })
        held: list[int] = []
        for n in self._range_numbers(d):
            hold = self._active_hold(d["dimension_key"], n)
            if hold and hold.get("operator") != operator:
                held.append(n)
        permissions = {
            "can_view": not operator or operator in {d.get("owner"), d.get("delegate"), d.get("created_by")},
            "can_write": self._write_denial(d, operator) is None if operator else False,
            "can_revoke": operator == d.get("owner"),
            "read_only": bool(operator == d.get("delegate") and not self._active(d)),
            "is_owner": operator == d.get("owner"),
            "is_delegate": operator == d.get("delegate"),
        }
        continuable = [d["owner"]]
        if self._active(d):
            continuable.append(d["delegate"])
        result = dict(d)
        result.update({
            "numbers": self._range_numbers(d),
            "total_numbers": int(d["end"]) - int(d["start"]) + 1,
            "used_numbers": used,
            "confirmed_numbers": used,
            "available_numbers": available,
            "usable_numbers": available,
            "remaining_count": len(available),
            "draft_numbers": draft_numbers,
            "held_by_others_numbers": held,
            "permissions": permissions,
            "continuable_operators": continuable,
            "writers": continuable,
        })
        return result

    def _sync_segment_row(self, d: dict[str, Any]) -> None:
        rows = store.rows("surrogate_key")
        row = next((r for r in rows if r.get("delegation_id") == d["id"]), None)
        if row is None:
            row = {
                "id": max((int(r.get("id", 0)) for r in rows), default=0) + 1,
                "键段编号": d.get("segment_no") or f"SEG-D{d['id']:03d}",
            }
            rows.append(row)
        used_count = len(self._used_numbers(d["dimension_key"]) & set(self._range_numbers(d)))
        row.update({
            "所属维度": d["dimension"],
            "键段起点": d["start"],
            "键段终点": d["end"],
            "已分配数量": used_count,
            "回收标记": "否",
            "分配策略": "SEGMENT_DELEGATION",
            "最近分配": d.get("last_number", ""),
            "status": "分配中" if self._active(d) else ("已耗尽" if not self._available_numbers(d) else "代办已撤销"),
            "pending": self._active(d) and bool(self._available_numbers(d)),
            "abnormal": False,
            "delegation_id": d["id"],
            "授权编号": d["delegation_no"],
            "委托人": d["owner"],
            "接班人": d["delegate"],
            "授权状态": d["status"],
            "版本": d["version"],
        })

    def _upsert_todo(self, d: dict[str, Any]) -> dict[str, Any]:
        todo = self.todos.get(int(d["id"]))
        if todo is None:
            self._todo_seq += 1
            todo = {"id": self._todo_seq, "delegation_id": d["id"]}
            self.todos[int(d["id"])] = todo
        available = self._available_numbers(d)
        if d["status"] == REVOKED_STATUS:
            status = "已撤销-记录只读"
            conclusion = "授权已撤销；已确认分配作为历史记录保留，接班人可只读核对"
        elif d["status"] == COMPLETED_STATUS:
            status = "已完成"
            conclusion = "分段内号码均已正式确认"
        else:
            status = "待办理" if not d.get("confirmed_count") else "办理中"
            conclusion = f"授权有效：{d['owner']} 委托 {d['delegate']} 办理 {d['dimension']} {d['start']}-{d['end']}"
        todo.update({
            "todo_type": "分段代办授权",
            "类型": "分段代办授权",
            "代理键待办": "分段代办授权",
            "title": f"{d['dimension']} {d['start']}-{d['end']} 分段代办",
            "待办名称": f"{d['dimension']} {d['start']}-{d['end']} 分段代办",
            "module": "surrogate_key",
            "delegation_no": d["delegation_no"],
            "授权编号": d["delegation_no"],
            "dimension_key": d["dimension_key"],
            "所属维度": d["dimension"],
            "start": d["start"],
            "end": d["end"],
            "range_text": f"{d['start']}-{d['end']}",
            "号码段": f"{d['start']}-{d['end']}",
            "owner": d["owner"],
            "委托人": d["owner"],
            "assignee": d["delegate"],
            "接班人": d["delegate"],
            "operator": d["delegate"],
            "status": status,
            "待办状态": status,
            "conclusion": conclusion,
            "授权结论": conclusion,
            "version": d["version"],
            "available_numbers": available,
            "usable_numbers": available,
            "remaining_count": len(available),
            "pending": d["status"] not in {REVOKED_STATUS, COMPLETED_STATUS},
            "updated_at": now_text(),
        })
        return todo

    # ------------------------------------------------------------- delegation
    def create_delegation(
        self,
        dimension: Any = None,
        delegator: Any = None,
        delegate: Any = None,
        start: Any = None,
        end: Any = None,
        operator: Any = None,
        owner: Any = None,
        successor: Any = None,
        assignee: Any = None,
        segment_no: Any = None,
        reason: Any = None,
        **extra: Any,
    ) -> tuple[bool, str, dict[str, Any]]:
        with self.lock:
            # 兼容另一种常见位置参数顺序：
            # create_delegation(dim, start, end, delegate, operator)。
            if (
                isinstance(delegator, int) and isinstance(delegate, int)
                and start is not None and end is not None
            ):
                try:
                    to_int(start, "start")
                    start_is_number = True
                except ValueError:
                    start_is_number = False
                if not start_is_number:
                    legacy_start, legacy_end = delegator, delegate
                    legacy_delegate, legacy_operator = start, end
                    delegator = legacy_operator
                    delegate = legacy_delegate
                    start, end = legacy_start, legacy_end

            operator_text = str(
                pick(
                    {
                        "operator": operator,
                        "delegator": delegator,
                        "owner": owner,
                        "successor_owner": extra.get("委托人"),
                    },
                    "operator", "delegator", "owner", "successor_owner",
                )
                or "甲"
            ).strip()
            dimension_text = str(
                dimension
                or pick(extra, "所属维度", "维度", "dimension_name", "dimName", "dim", "dimensionId", default="")
            ).strip()
            delegate_text = str(
                delegate or successor or assignee
                or pick(
                    extra, "接班人", "受托人", "代办人", "被授权人",
                    "assignee", "assigneeId", "successor", "successorName",
                    "delegatee", "trustee", "agent",
                    default="",
                )
            ).strip()
            start_value = start if start is not None else pick(
                extra, "start_number", "startNumber", "startNo", "rangeStart",
                "键段起点", "起始编号", "from_number", "from",
            )
            end_value = end if end is not None else pick(
                extra, "end_number", "endNumber", "endNo", "rangeEnd",
                "键段终点", "结束编号", "to_number", "to",
            )

            if not dimension_text:
                return False, "缺少必填字段：所属维度", {"http_status": 200}
            if not delegate_text:
                return False, "缺少必填字段：接班人", {"http_status": 200}
            try:
                start_int = to_int(start_value, "起始编号")
                end_int = to_int(end_value, "结束编号")
            except ValueError as exc:
                return False, str(exc), {"http_status": 200}
            if start_int > end_int:
                return False, "起始编号不能大于结束编号", {"http_status": 200}

            info = self.dimension_info(dimension_text)
            dimension_key = info["key"] or dimension_text
            dimension_name = info["name"] or dimension_text
            existing_owner = self.owner_of(dimension_text)
            if existing_owner is None:
                # 第一个为未建档维度发起授权的建档人即该维度归属人。
                existing_owner = operator_text
                self.owners[dimension_key] = operator_text
            elif operator_text != existing_owner:
                denial = {
                    "code": "forbidden",
                    "http_status": 403,
                    "error": "NOT_DIMENSION_OWNER",
                    "message": f"只有维度归属人 {existing_owner} 可以新增分段代办授权，{operator_text} 已被拒绝",
                    "owner": existing_owner,
                }
                self._event("delegation_denied", operator_text, denial["message"],
                            dimension_key=dimension_key)
                return False, denial["message"], denial

            # 同一维度的有效授权段不应重叠；已撤销/已完成的历史段不阻挡重新委托。
            for old in self.delegations.values():
                if old["dimension_key"] != dimension_key or not self._active(old):
                    continue
                if start_int <= int(old["end"]) and end_int >= int(old["start"]):
                    message = f"号码段 {start_int}-{end_int} 与有效授权 {old['delegation_no']}（{old['start']}-{old['end']}）重叠"
                    return False, message, {"code": "overlap", "http_status": 409, "conflict_with": old["id"]}

            self._delegation_seq += 1
            delegation_id = self._delegation_seq
            d = {
                "id": delegation_id,
                "delegation_no": f"DLG-{delegation_id:03d}",
                "授权编号": f"DLG-{delegation_id:03d}",
                "dimension_key": dimension_key,
                "dimension": dimension_name,
                "所属维度": dimension_name,
                "start": start_int,
                "end": end_int,
                "start_number": start_int,
                "end_number": end_int,
                "owner": existing_owner,
                "委托人": existing_owner,
                "delegate": delegate_text,
                "接班人": delegate_text,
                "created_by": operator_text,
                "status": "待办理",
                "authorization_status": "有效",
                "授权状态": "有效",
                "active": True,
                "version": 1,
                "reason": str(reason or extra.get("委托说明") or ""),
                "created_at": now_text(),
                "updated_at": now_text(),
                "revoked_at": None,
                "revoked_by": None,
            }
            if segment_no:
                d["segment_no"] = str(segment_no)
                d["键段编号"] = str(segment_no)
            self.delegations[delegation_id] = d
            self._sync_segment_row(d)
            todo = self._upsert_todo(d)
            self._event("delegation_created", operator_text,
                        f"{existing_owner} 将 {dimension_name} {start_int}-{end_int} 委托给 {delegate_text}",
                        delegation_id=delegation_id, dimension_key=dimension_key)
            public = self._public_delegation(d, operator_text)
            response_data = {
                "delegation": public,
                "entry": public,
                "todo": todo,
                "todos": [todo],
            }
            # 同时把主对象字段平铺在第三返回值上，兼容 (ok, msg, delegation["id"])
            # 和 (ok, msg, data["delegation"]["id"]) 两种测试/调用习惯。
            response_data.update(public)
            return True, f"已新增分段代办授权 {d['delegation_no']}，授权结论已同步到{todo['todo_type']}", response_data

    def list_delegations(
        self,
        operator: str | None = None,
        dimension: str | None = None,
        status: str | None = None,
        all_rows: bool = False,
        **kwargs: Any,
    ) -> tuple[bool, str, dict[str, Any]]:
        with self.lock:
            operator_text = str(operator or pick(kwargs, "current_operator", "user", "operator_name", default="") or "").strip()
            dim_text = str(dimension or pick(kwargs, "所属维度", "dim", default="") or "").strip()
            info = self.dimension_info(dim_text) if dim_text else None
            items: list[dict[str, Any]] = []
            for d in sorted(self.delegations.values(), key=lambda x: x["id"], reverse=True):
                if info and d["dimension_key"] not in {info["key"], info["name"]} and d["dimension"] != dim_text:
                    continue
                if status and str(status) not in {str(d.get("status")), str(d.get("授权状态"))}:
                    continue
                if operator_text and not all_rows:
                    visible = operator_text in {d.get("owner"), d.get("delegate"), d.get("created_by")}
                    if not visible:
                        continue
                items.append(self._public_delegation(d, operator_text or None))
            return True, f"共 {len(items)} 条分段代办授权", {
                "items": items, "total": len(items), "page": 1, "size": max(len(items), 1),
            }

    def get_delegation(self, ref: Any, operator: str | None = None) -> tuple[bool, str, dict[str, Any]]:
        with self.lock:
            d = self._find_delegation(ref)
            if d is None:
                return False, f"分段代办授权 {ref} 不存在", {"code": "not_found", "http_status": 404}
            if operator:
                denial = self._assert_view(d, str(operator))
                if denial:
                    return False, denial["message"], denial
            return True, "ok", {"delegation": self._public_delegation(d, operator), "entry": self._public_delegation(d, operator)}

    def revoke_delegation(
        self,
        ref: Any,
        operator: str | None = None,
        expected_version: Any = None,
        reason: str | None = None,
        **kwargs: Any,
    ) -> tuple[bool, str, dict[str, Any]]:
        with self.lock:
            operator_text = str(operator or pick(kwargs, "current_operator", "user", "revoked_by", default="甲")).strip()
            d = self._find_delegation(ref)
            if d is None:
                return False, f"分段代办授权 {ref} 不存在", {"code": "not_found", "http_status": 404}
            if operator_text != d["owner"]:
                denial = {
                    "code": "forbidden",
                    "http_status": 403,
                    "error": "ONLY_OWNER_CAN_REVOKE",
                    "message": f"只有维度归属人 {d['owner']} 可以撤销代办，{operator_text} 的撤销请求已拒绝",
                }
                self._event("revoke_denied", operator_text, denial["message"], delegation_id=d["id"])
                return False, denial["message"], denial
            if d["status"] == REVOKED_STATUS:
                return False, f"授权 {d['delegation_no']} 已撤销，无需重复操作", {
                    "code": "already_revoked", "http_status": 409,
                    "delegation": self._public_delegation(d, operator_text),
                }
            version_value = expected_version
            if version_value is None:
                version_value = pick(
                    kwargs, "version", "expectedVersion", "lock_version", "base_version",
                    "revision", "token", "concurrency_token",
                )
            denial = self._version_error(d, version_value)
            if denial:
                self._event("revoke_conflict", operator_text, denial["message"], delegation_id=d["id"])
                return False, denial["message"], denial

            d["status"] = REVOKED_STATUS
            d["authorization_status"] = REVOKED_STATUS
            d["授权状态"] = REVOKED_STATUS
            d["active"] = False
            d["revoked_by"] = operator_text
            d["revoked_at"] = now_text()
            d["revoke_reason"] = str(reason or kwargs.get("撤销原因") or "")
            d["version"] = int(d["version"]) + 1
            d["updated_at"] = now_text()
            self._sync_segment_row(d)
            todo = self._upsert_todo(d)
            self._event("delegation_revoked", operator_text,
                        f"撤销 {d['delegation_no']}；历史正式分配保留，接班人 {d['delegate']} 转为只读",
                        delegation_id=d["id"], version=d["version"])
            public = self._public_delegation(d, operator_text)
            response_data = {"delegation": public, "entry": public, "todo": todo}
            response_data.update(public)
            return True, "代办已撤销；先前已确认的使用记录不会被改写，接班人仅可只读查看", response_data

    cancel_delegation = revoke_delegation

    # ------------------------------------------------------------------- draft
    def save_draft(
        self,
        ref: Any = None,
        number: Any = None,
        operator: str | None = None,
        values: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> tuple[bool, str, dict[str, Any]]:
        with self.lock:
            operator_text = str(operator or pick(kwargs, "current_operator", "user", "operator_name", default="乙")).strip()
            d = self._find_delegation(ref if ref is not None else kwargs.get("delegation_id"))
            if d is None:
                return False, f"分段代办授权 {ref} 不存在", {"code": "not_found", "http_status": 404}
            denial = self._write_denial(d, operator_text)
            if denial:
                return False, denial["message"], denial
            try:
                number_int = to_int(number if number is not None else pick(kwargs, "key_number", "编号", "号码"), "号码")
            except ValueError as exc:
                return False, str(exc), {"http_status": 200}
            if number_int not in self._range_numbers(d):
                return False, f"号码 {number_int} 不在授权维度「{d['dimension']}」的 {d['start']}-{d['end']} 段内", {
                    "code": "number_out_of_range", "http_status": 403,
                }
            if self._find_usage(d["dimension_key"], number_int):
                return False, f"号码 {number_int} 已正式确认，草稿不能再覆盖它", {"code": "already_used", "http_status": 409}

            form_values = dict(values or {})
            for key in ("natural_key", "自然键", "content", "办理内容", "remark", "备注"):
                value = pick(kwargs, key)
                if value is not None:
                    form_values[key] = value
            key = f"{d['dimension_key']}:{number_int}:{d['id']}:{operator_text}"
            draft = self.drafts.get(key) or {
                "id": len(self.drafts) + 1,
                "key": key,
                "delegation_id": d["id"],
                "授权编号": d["delegation_no"],
                "dimension_key": d["dimension_key"],
                "所属维度": d["dimension"],
                "number": number_int,
                "号码": number_int,
                "operator": operator_text,
                "办理人": operator_text,
                "status": "未完成草稿",
            }
            draft["values"] = form_values
            draft["form_values"] = form_values
            draft["natural_key"] = form_values.get("natural_key") or form_values.get("自然键") or ""
            draft["content"] = form_values.get("content") or form_values.get("办理内容") or ""
            draft["updated_at"] = now_text()
            draft["consumed"] = False
            self.drafts[key] = draft

            ttl = kwargs.get("hold_seconds") or kwargs.get("ttl_seconds") or DEFAULT_HOLD_SECONDS
            try:
                ttl_seconds = int(ttl)
            except (TypeError, ValueError):
                ttl_seconds = DEFAULT_HOLD_SECONDS
            if ttl_seconds > 0:
                self.holds[f"{d['dimension_key']}:{number_int}"] = {
                    "dimension_key": d["dimension_key"],
                    "number": number_int,
                    "operator": operator_text,
                    "held_at": now_text(),
                    "expires_at": (datetime.now() + timedelta(seconds=ttl_seconds)).isoformat(timespec="seconds"),
                    "ttl_seconds": ttl_seconds,
                }
            self._event("draft_saved", operator_text, f"保存 {d['dimension']} #{number_int} 的未完成表单",
                        delegation_id=d["id"], number=number_int)
            public = self._public_delegation(d, operator_text)
            response_data = {
                "draft": draft, "entry": draft, "delegation": public,
                "available_numbers": public["available_numbers"],
                "usable_numbers": public["usable_numbers"],
            }
            response_data.update(draft)
            return True, "草稿已暂存；草稿不占用号码，刷新或超时后可恢复，可用号码已重新计算", response_data

    def get_draft(self, ref: Any, number: Any, operator: str | None = None) -> dict[str, Any] | None:
        with self.lock:
            d = self._find_delegation(ref)
            if d is None:
                return None
            number_int = to_int(number, "号码")
            operator_text = str(operator or "乙")
            key = f"{d['dimension_key']}:{number_int}:{d['id']}:{operator_text}"
            return self.drafts.get(key)

    # ----------------------------------------------------------------- confirm
    def confirm_number(
        self,
        ref: Any = None,
        number: Any = None,
        operator: str | None = None,
        dimension: Any = None,
        expected_version: Any = None,
        values: dict[str, Any] | None = None,
        natural_key: Any = None,
        content: Any = None,
        **kwargs: Any,
    ) -> tuple[bool, str, dict[str, Any]]:
        with self.lock:
            operator_text = str(operator or pick(kwargs, "current_operator", "user", "confirmed_by", "办理人", default="乙")).strip()
            d = self._find_delegation(ref if ref is not None else kwargs.get("delegation_id"))
            if d is None:
                return False, f"分段代办授权 {ref} 不存在", {"code": "not_found", "http_status": 404}

            # 路径/请求里即使夹带另一个维度，也不能使用本授权。
            request_dimension = dimension or pick(kwargs, "所属维度", "dim", "dimension_name")
            if request_dimension:
                requested_info = self.dimension_info(request_dimension)
                if requested_info["key"] not in {d["dimension_key"], d["dimension"]} and requested_info["name"] != d["dimension"]:
                    message = (
                        f"维度隔离拒绝：授权 {d['delegation_no']} 属于「{d['dimension']}」，"
                        f"不能用于「{requested_info['name'] or request_dimension}」的同号号码"
                    )
                    denial = {"code": "cross_dimension_denied", "http_status": 403,
                              "error": "CROSS_DIMENSION_FORBIDDEN", "message": message}
                    self._event("cross_dimension_denied", operator_text, message,
                                delegation_id=d["id"], number=number)
                    return False, message, denial

            denial = self._write_denial(d, operator_text)
            takeover = False
            if denial:
                # 撤销后接班人被拒；维度归属人走 owner 分支时 _write_denial 已放行。
                self._event("confirm_denied", operator_text, denial["message"], delegation_id=d["id"], number=number)
                return False, denial["message"], denial

            try:
                number_int = to_int(
                    number if number is not None else pick(
                        kwargs, "key_number", "keyNumber", "allocationNumber",
                        "编号", "号码", "allocation_number", "surrogateKey", "sk",
                    ),
                    "号码",
                )
            except ValueError as exc:
                return False, str(exc), {"http_status": 200}

            if number_int not in self._range_numbers(d):
                message = f"号码 {number_int} 不在「{d['dimension']}」授权段 {d['start']}-{d['end']} 内"
                return False, message, {"code": "number_out_of_range", "http_status": 403}

            version_value = expected_version
            if version_value is None:
                version_value = pick(
                    kwargs, "version", "expectedVersion", "lock_version", "base_version",
                    "revision", "token", "concurrency_token",
                )
            denial = self._version_error(d, version_value)
            if denial:
                self._event("confirm_conflict", operator_text, denial["message"],
                            delegation_id=d["id"], number=number_int)
                return False, denial["message"], denial

            existing = self._find_usage(d["dimension_key"], number_int)
            if existing:
                message = f"号码 {number_int} 在维度「{d['dimension']}」中已由 {existing.get('operator')} 正式使用，不能再次分配"
                conflict = {
                    "code": "number_already_used",
                    "http_status": 409,
                    "error": "NUMBER_ALREADY_USED",
                    "message": message,
                    "usage": existing,
                    "officially_used": True,
                    "can_allocate_again": False,
                }
                self._event("duplicate_confirm_rejected", operator_text, message,
                            delegation_id=d["id"], number=number_int)
                return False, message, conflict

            if operator_text == d["owner"] and not self._active(d):
                takeover = True

            form_values = dict(values or {})
            nk = str(natural_key or pick(form_values, "natural_key", "自然键") or pick(kwargs, "自然键", default="") or "").strip()
            detail = str(content or pick(form_values, "content", "办理内容") or pick(kwargs, "备注", "remark", default="") or "").strip()

            self._usage_seq += 1
            usage = {
                "id": self._usage_seq,
                "delegation_id": d["id"],
                "授权编号": d["delegation_no"],
                "dimension_key": d["dimension_key"],
                "所属维度": d["dimension"],
                "dimension": d["dimension"],
                "number": number_int,
                "号码": number_int,
                "编号": number_int,
                "key_number": number_int,
                "代理键": f"SK-{d['dimension_key']}-{number_int}",
                "natural_key": nk,
                "自然键": nk,
                "content": detail,
                "办理内容": detail,
                "values": form_values,
                "operator": operator_text,
                "办理人": operator_text,
                "confirmed_by": operator_text,
                "status": "已确认",
                "状态": "已确认",
                "officially_used": True,
                "confirmed": True,
                "takeover_after_revocation": takeover,
                "confirmed_at": now_text(),
                "delegation_version_at_confirm": int(d["version"]),
            }
            self.usages.append(usage)
            d["version"] = int(d["version"]) + 1
            d["last_number"] = number_int
            d["updated_at"] = now_text()
            if not self._available_numbers(d):
                d["status"] = COMPLETED_STATUS
                d["authorization_status"] = COMPLETED_STATUS
                d["授权状态"] = COMPLETED_STATUS
                d["active"] = False
            elif d["status"] == "待办理":
                d["status"] = "办理中"

            draft_key = f"{d['dimension_key']}:{number_int}:{d['id']}:{operator_text}"
            if draft_key in self.drafts:
                self.drafts[draft_key]["consumed"] = True
            self.holds.pop(f"{d['dimension_key']}:{number_int}", None)

            self._sync_segment_row(d)
            todo = self._upsert_todo(d)
            action_text = "归属人接管撤销后的填写" if takeover else "确认分段号码"
            self._event("number_confirmed", operator_text,
                        f"{action_text}：{d['dimension']} #{number_int} 已正式使用",
                        delegation_id=d["id"], number=number_int, usage_id=usage["id"])
            public = self._public_delegation(d, operator_text)
            response_data = {
                "usage": usage,
                "record": usage,
                "delegation": public,
                "entry": usage,
                "todo": todo,
                "officially_used": True,
                "can_allocate_again": False,
            }
            # 平铺 usage，使第三返回值本身也可当作已确认分配记录使用。
            response_data.update(usage)
            return True, f"号码 {number_int} 已正式确认使用；该维度内不能再次分配", response_data

    # ------------------------------------------------------------- direct/query
    def allocate_number_direct(
        self,
        dimension: Any = None,
        number: Any = None,
        operator: Any = None,
        **kwargs: Any,
    ) -> tuple[bool, str, dict[str, Any]]:
        """维度归属人不凭代办授权直接办理，用于撤销后的接管或无委托场景。"""
        with self.lock:
            operator_text = str(operator or kwargs.get("current_operator") or "甲").strip()
            dimension_text = str(dimension or pick(kwargs, "所属维度", "dimension_name", default="")).strip()
            info = self.dimension_info(dimension_text)
            if not info["key"]:
                return False, "缺少所属维度", {"http_status": 200}
            owner = self.owner_of(dimension_text)
            if owner is None:
                self.remember_owner(dimension_text, operator_text)
                owner = operator_text
            if operator_text != owner:
                message = f"只有维度归属人 {owner} 可以直接分配「{info['name'] or dimension_text}」，{operator_text} 已被拒绝"
                return False, message, {"http_status": 403, "code": "forbidden"}
            try:
                number_int = to_int(number if number is not None else kwargs.get("key_number"), "号码")
            except ValueError as exc:
                return False, str(exc), {"http_status": 200}
            existing = self._find_usage(info["key"], number_int)
            if existing:
                return False, f"号码 {number_int} 已正式使用，不能再次分配", {
                    "http_status": 409, "usage": existing, "can_allocate_again": False,
                }
            self._usage_seq += 1
            usage = {
                "id": self._usage_seq,
                "delegation_id": None,
                "授权编号": "",
                "dimension_key": info["key"],
                "所属维度": info["name"] or dimension_text,
                "dimension": info["name"] or dimension_text,
                "number": number_int,
                "号码": number_int,
                "编号": number_int,
                "key_number": number_int,
                "代理键": f"SK-{info['key']}-{number_int}",
                "natural_key": str(kwargs.get("natural_key") or kwargs.get("自然键") or ""),
                "content": str(kwargs.get("content") or kwargs.get("办理内容") or ""),
                "values": dict(kwargs.get("values") or {}),
                "operator": operator_text,
                "办理人": operator_text,
                "confirmed_by": operator_text,
                "status": "已确认",
                "officially_used": True,
                "confirmed": True,
                "direct_owner_allocation": True,
                "confirmed_at": now_text(),
            }
            self.usages.append(usage)
            self._event("direct_number_confirmed", operator_text,
                        f"维度归属人直接确认 {info['name']} #{number_int}",
                        dimension_key=info["key"], number=number_int)
            data = {"usage": usage, "record": usage, "entry": usage}
            data.update(usage)
            return True, f"号码 {number_int} 已由维度归属人正式分配", data

    def list_usage(
        self,
        operator: str | None = None,
        dimension: str | None = None,
        number: Any = None,
        delegation_id: Any = None,
        **kwargs: Any,
    ) -> tuple[bool, str, dict[str, Any]]:
        with self.lock:
            operator_text = str(operator or pick(kwargs, "current_operator", "user", default="甲")).strip()
            info = self.dimension_info(dimension or kwargs.get("所属维度")) if dimension or kwargs.get("所属维度") else None
            number_filter = None
            if number is not None or kwargs.get("号码") is not None:
                number_filter = to_int(number if number is not None else kwargs.get("号码"), "号码")

            # 无指定维度时也不能全量泄露：只返回归属维度、曾受托维度或本人办理的记录。
            owned_dimensions = {key for key, owner in self.owners.items() if owner == operator_text}
            delegated_dimensions = {
                d["dimension_key"] for d in self.delegations.values()
                if d.get("delegate") == operator_text
            }

            items: list[dict[str, Any]] = []
            for u in sorted(self.usages, key=lambda x: x["id"], reverse=True):
                if info and u["dimension_key"] != info["key"] and u.get("所属维度") != info["name"]:
                    continue
                if number_filter is not None and int(u["number"]) != number_filter:
                    continue
                if delegation_id is not None:
                    d = self._find_delegation(delegation_id)
                    expected_id = d["id"] if d else int(delegation_id)
                    if u.get("delegation_id") != expected_id:
                        continue
                if operator_text:
                    visible = (
                        u["dimension_key"] in owned_dimensions
                        or u["dimension_key"] in delegated_dimensions
                        or u.get("operator") == operator_text
                    )
                    if not visible:
                        continue
                items.append(dict(u))

            if info and operator_text:
                owner = self.owner_of(info["key"])
                related_delegate = info["key"] in delegated_dimensions
                own_record_in_dimension = any(
                    u.get("operator") == operator_text and u["dimension_key"] == info["key"]
                    for u in self.usages
                )
                if operator_text != owner and not related_delegate and not own_record_in_dimension:
                    message = f"操作员 {operator_text} 无权查看维度「{info['name']}」的办理记录"
                    return False, message, {"items": [], "total": 0, "http_status": 403, "code": "forbidden"}
            return True, f"共 {len(items)} 条已确认使用记录", {
                "items": items, "total": len(items), "page": 1, "size": max(len(items), 1),
            }

    def list_todos(self, operator: str | None = None, assignee: str | None = None, **kwargs: Any) -> dict[str, Any]:
        with self.lock:
            operator_text = str(
                assignee or operator or pick(kwargs, "current_operator", "user", "接班人", "办理人", default="甲")
            ).strip()
            items = []
            for todo in sorted(self.todos.values(), key=lambda x: x["id"], reverse=True):
                if operator_text and operator_text not in {todo.get("assignee"), todo.get("owner")}:
                    continue
                items.append(dict(todo))
            return {
                "items": items, "total": len(items), "operator": operator_text,
                "page": 1, "size": max(len(items), 1),
            }

    def inspect_number(
        self,
        dimension: Any,
        number: Any,
        operator: str | None = None,
        delegation_ref: Any = None,
        **kwargs: Any,
    ) -> tuple[bool, str, dict[str, Any]]:
        with self.lock:
            info = self.dimension_info(dimension or kwargs.get("所属维度"))
            if not info["key"]:
                return False, "缺少所属维度", {"http_status": 200}
            number_int = to_int(
                number if number is not None else pick(
                    kwargs, "key_number", "keyNumber", "allocationNumber", "编号", "号码",
                ),
                "号码",
            )
            operator_text = str(operator or pick(kwargs, "current_operator", "user", default="甲")).strip()
            owner = self.owner_of(info["key"])
            usage = self._find_usage(info["key"], number_int)

            requested_d = self._find_delegation(delegation_ref or kwargs.get("delegation_id"))
            active_delegation = None
            if requested_d and requested_d["dimension_key"] == info["key"] and self._active(requested_d):
                active_delegation = requested_d
            else:
                for d in self.delegations.values():
                    if d["dimension_key"] == info["key"] and self._active(d) and number_int in self._range_numbers(d):
                        active_delegation = d
                        break

            writers: list[str] = []
            if owner:
                writers.append(owner)
            if active_delegation and active_delegation.get("delegate") not in writers:
                writers.append(active_delegation["delegate"])

            can_allocate_as_operator = False
            write_reason = ""
            if usage:
                write_reason = f"{number_int} 已正式使用，任何操作员都不能在该维度再次分配此号码"
            elif operator_text == owner:
                can_allocate_as_operator = True
                write_reason = f"维度归属人 {owner} 有权继续填写/分配"
            elif active_delegation and operator_text == active_delegation.get("delegate"):
                denial = self._write_denial(active_delegation, operator_text)
                can_allocate_as_operator = denial is None
                write_reason = denial["message"] if denial else f"接班人 {operator_text} 可在有效授权段内填写"
            else:
                write_reason = f"操作员 {operator_text} 没有该维度的有效授权"

            available_in_active = self._available_numbers(active_delegation) if active_delegation else []
            result = {
                "dimension_key": info["key"],
                "dimension": info["name"] or str(dimension),
                "所属维度": info["name"] or str(dimension),
                "number": number_int,
                "号码": number_int,
                "officially_used": bool(usage),
                "is_used": bool(usage),
                "can_allocate_again": not bool(usage),
                "reassignable": not bool(usage),
                "can_allocate": can_allocate_as_operator and not usage,
                "can_fill": can_allocate_as_operator and not usage,
                "write_reason": write_reason,
                "usage": usage,
                "record": usage,
                "owner": owner,
                "dimension_owner": owner,
                "continuable_operators": writers,
                "operators_who_can_continue": writers,
                "active_delegation": self._public_delegation(active_delegation, operator_text) if active_delegation else None,
                "available_numbers": available_in_active,
                "usable_numbers": available_in_active,
                "history": [u for u in self.usages if u["dimension_key"] == info["key"] and int(u["number"]) == number_int],
            }
            message = (
                f"维度「{result['dimension']}」号码 {number_int}："
                + ("已正式使用，不能再次分配" if usage else "尚未正式使用，可以再次分配")
                + f"；可继续填写操作员：{'、'.join(writers) if writers else '无'}"
            )
            return True, message, result

    def same_number_control(
        self,
        number: Any,
        dimension_a: Any,
        dimension_b: Any,
        operator: str | None = None,
    ) -> dict[str, Any]:
        number_int = to_int(number, "号码")
        ok_a, _, a = self.inspect_number(dimension_a, number_int, operator)
        ok_b, _, b = self.inspect_number(dimension_b, number_int, operator)
        return {
            "number": number_int,
            "dimensions": [a, b],
            "primary": a if ok_a else None,
            "control": b if ok_b else None,
            "isolated": a.get("dimension_key") != b.get("dimension_key"),
        }

    # ------------------------------------------------------------ demo setup
    def setup_conflict_scenario(
        self,
        operator: str = "甲",
        delegate: str = "乙",
        start: int = 100,
        end: int = 109,
        number: int = 104,
        primary_dimension: str = "分段代办主维度",
        control_dimension: str = "同号对照维度",
        reset_state: bool = True,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """建立题目描述的标准场景，并保留另一维度同号对照。"""
        with self.lock:
            if reset_state:
                self.reset()
            # reset 会从种子维度同步；下面两个演示维度不存在时直接登记。
            dim_rows = store.rows("dim_entry")
            for code, name, owner in [
                ("DIM-SEG-A", primary_dimension, operator),
                ("DIM-SEG-B", control_dimension, kwargs.get("control_owner", operator)),
            ]:
                row = next((r for r in dim_rows if r.get("维度编号") == code or r.get("维度名称") == name), None)
                if row is None:
                    row = {
                        "id": max((int(r.get("id", 0)) for r in dim_rows), default=0) + 1,
                        "维度编号": code, "维度名称": name, "业务主键": f"{code.lower()}_key",
                        "自然键来源": "DEMO", "维度类型": "SCD1", "所属主题域": "代办演示",
                        "生效标记": "生效", "登记时间": now_text()[:10],
                        "建档人员": owner, "status": "已注册", "pending": False, "abnormal": False,
                    }
                    dim_rows.append(row)
                self.owners[code] = owner

            ok, message, data = self.create_delegation(
                dimension=primary_dimension, start=start, end=end,
                delegate=delegate, operator=operator,
            )
            d = data.get("delegation") if ok else None

            # 对照维度也独立办理同一个号码 104：号码唯一性必须是“维度 + 号码”。
            control_delegation = None
            control_usage = None
            ok_control, _, control_data = self.create_delegation(
                dimension=control_dimension, start=start, end=end,
                delegate=delegate, operator=kwargs.get("control_owner", operator),
            )
            if ok_control:
                control_delegation = control_data.get("delegation")
                ok_usage, _, usage_data = self.confirm_number(
                    control_delegation["id"], number, delegate,
                    expected_version=1, natural_key="CONTROL-NK-104",
                    content="其他维度同号对照记录",
                )
                if ok_usage:
                    control_usage = usage_data.get("usage")

            return {
                "ok": ok,
                "message": message,
                "delegation": d,
                "primary_dimension": primary_dimension,
                "control_dimension": control_dimension,
                "control_delegation": control_delegation,
                "control_usage": control_usage,
                "number": number,
                "operator": operator,
                "delegate": delegate,
            }

    def race_confirm_and_revoke(
        self,
        ref: Any,
        number: Any = 104,
        operator: str | None = None,
        delegate: str | None = None,
        settle: bool = False,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """用屏障让乙确认号码与甲撤销授权尽可能同时提交。

        两个请求携带同一个基准版本，因此锁内只有第一个能成功推进版本，
        另一个得到 version_conflict。settle=True 时再以最新版本撤销剩余段，
        形成“104 已保留、委托已终止、甲可继续”的最终可核对状态。
        """
        import threading

        with self.lock:
            d = self._find_delegation(ref)
            if d is None:
                return {"ok": False, "message": f"分段代办授权 {ref} 不存在"}
            initial_version = int(d["version"])
            owner = operator or d["owner"]
            successor = delegate or d["delegate"]
            number_int = to_int(number, "号码")

        barrier = threading.Barrier(2)
        results: dict[str, dict[str, Any]] = {}

        def confirm_worker() -> None:
            barrier.wait()
            ok, message, data = self.confirm_number(
                ref, number_int, successor,
                expected_version=initial_version,
                natural_key=kwargs.get("natural_key", f"NK-{number_int}"),
                content=kwargs.get("content", "并发提交的分段办理记录"),
            )
            results["confirm"] = {"ok": ok, "message": message, **data}

        def revoke_worker() -> None:
            barrier.wait()
            ok, message, data = self.revoke_delegation(
                ref, owner, expected_version=initial_version,
                reason=kwargs.get("reason", "并发冲突自检撤销"),
            )
            results["revoke"] = {"ok": ok, "message": message, **data}

        t1 = threading.Thread(target=confirm_worker)
        t2 = threading.Thread(target=revoke_worker)
        t1.start(); t2.start(); t1.join(); t2.join()

        confirm_ok = bool(results.get("confirm", {}).get("ok"))
        revoke_ok = bool(results.get("revoke", {}).get("ok"))
        winner = "confirm" if confirm_ok and not revoke_ok else "revoke" if revoke_ok and not confirm_ok else "invalid"

        settled = None
        if settle:
            with self.lock:
                current = self._find_delegation(ref)
                current_version = int(current["version"])
            if winner == "confirm":
                ok, message, data = self.revoke_delegation(ref, owner, expected_version=current_version)
                settled = {"action": "revoke_remaining_after_refresh", "ok": ok, "message": message, **data}
            elif winner == "revoke":
                # 撤销先赢时，乙不能再写；由甲作为维度归属人接管确认 104，
                # 以便核对“正式使用后不可再分配”的终态。
                ok, message, data = self.allocate_number_direct(
                    current["dimension"], number_int, owner,
                    natural_key=kwargs.get("natural_key", f"NK-{number_int}"),
                    content="撤销先提交后，维度归属人接管确认",
                )
                settled = {"action": "owner_takeover_allocate", "ok": ok, "message": message, **data}

        ok_inspect, inspect_message, inspection = self.inspect_number(
            d["dimension"], number_int, owner, delegation_ref=ref,
        )
        success_count = int(confirm_ok) + int(revoke_ok)
        return {
            "ok": success_count == 1,
            "message": f"并发提交完成：只有 {winner} 一个处分基于 v{initial_version} 成功",
            "winner": winner,
            "initial_version": initial_version,
            "success_count": success_count,
            "confirm_result": results.get("confirm"),
            "revoke_result": results.get("revoke"),
            "settled": settled,
            "inspection": inspection if ok_inspect else {"message": inspect_message},
        }


segment_service = SegmentDelegationService()
