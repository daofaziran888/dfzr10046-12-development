"""代理键生成领域服务：键段分配与耗尽标记，校验回收策略，产出键段分配表数据集。"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.services.segment_delegation import segment_service
from app.store import store


class SurrogateKeyService(BaseModuleService):
    MODULE = "surrogate_key"
    ENTITY = "代理键段"
    LIST_FIELDS = ["键段编号", "所属维度", "键段起点", "键段终点", "已分配数量", "回收标记", "分配策略", "最近分配"]
    REQUIRED_FIELDS = ["键段编号", "所属维度", "键段起点"]
    STATUS_ORDER = ["待分配", "分配中", "已耗尽", "已回收", "代办已撤销"]
    ACTION_RULES = {"启动分配": "分配中", "标记耗尽": "已耗尽", "回收键段": "已回收", "撤销代办": "代办已撤销"}
    NEGATIVE_ACTIONS = []

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        """登记键段；若同时给出接班人，则同步建立分段代办授权和待办。"""
        values = dict(values)
        delegate = values.get("接班人") or values.get("delegate") or values.get("assignee")
        if delegate and not values.get("键段编号"):
            rows = store.rows(self.MODULE)
            next_id = max((int(row.get("id", 0)) for row in rows), default=0) + 1
            values["键段编号"] = f"SEG-DELEG-{next_id:03d}"
        entry, missing = super().create_entry(values)
        if entry is not None and delegate:
            ok, message, data = segment_service.create_delegation(
                dimension=values.get("所属维度"),
                start=values.get("键段起点"),
                end=values.get("键段终点") or values.get("键段起点"),
                delegate=delegate,
                operator=values.get("操作员") or values.get("建档人员") or values.get("委托人") or "甲",
                segment_no=entry.get("键段编号"),
            )
            entry["delegation_ok"] = ok
            entry["delegation_message"] = message
            if data.get("delegation"):
                entry["delegation_id"] = data["delegation"]["id"]
                entry["授权编号"] = data["delegation"]["delegation_no"]
        return entry, missing

    # 便于直接在领域服务层发现分段代办能力的语义别名。
    def delegate_segment(self, *args: Any, **kwargs: Any) -> tuple[bool, str, dict[str, Any]]:
        return segment_service.create_delegation(*args, **kwargs)

    def create_delegation(self, *args: Any, **kwargs: Any) -> tuple[bool, str, dict[str, Any]]:
        return segment_service.create_delegation(*args, **kwargs)

    create_segment_delegation = delegate_segment
    authorize_segment = delegate_segment
    delegate_range = delegate_segment
    create_segment_authorization = delegate_segment

    def list_delegations(self, *args: Any, **kwargs: Any) -> tuple[bool, str, dict[str, Any]]:
        return segment_service.list_delegations(*args, **kwargs)

    def get_delegation(self, *args: Any, **kwargs: Any) -> tuple[bool, str, dict[str, Any]]:
        return segment_service.get_delegation(*args, **kwargs)

    def save_draft(self, *args: Any, **kwargs: Any) -> tuple[bool, str, dict[str, Any]]:
        return segment_service.save_draft(*args, **kwargs)

    def confirm_number(self, *args: Any, **kwargs: Any) -> tuple[bool, str, dict[str, Any]]:
        return segment_service.confirm_number(*args, **kwargs)

    def confirm_delegated_number(self, *args: Any, **kwargs: Any) -> tuple[bool, str, dict[str, Any]]:
        return segment_service.confirm_number(*args, **kwargs)

    def allocate_delegated_number(self, *args: Any, **kwargs: Any) -> tuple[bool, str, dict[str, Any]]:
        return segment_service.confirm_number(*args, **kwargs)

    submit_segment = confirm_delegated_number
    fill_and_confirm_segment = confirm_delegated_number

    def revoke_delegation(self, *args: Any, **kwargs: Any) -> tuple[bool, str, dict[str, Any]]:
        return segment_service.revoke_delegation(*args, **kwargs)

    def revoke_segment_delegation(self, *args: Any, **kwargs: Any) -> tuple[bool, str, dict[str, Any]]:
        return segment_service.revoke_delegation(*args, **kwargs)

    def cancel_delegation(self, *args: Any, **kwargs: Any) -> tuple[bool, str, dict[str, Any]]:
        return segment_service.revoke_delegation(*args, **kwargs)

    cancel_segment_delegation = revoke_segment_delegation
    cancel_segment = cancel_delegation
    withdraw_delegation = cancel_delegation

    def inspect_number(self, *args: Any, **kwargs: Any) -> tuple[bool, str, dict[str, Any]]:
        return segment_service.inspect_number(*args, **kwargs)

    def segment_number_status(self, *args: Any, **kwargs: Any) -> tuple[bool, str, dict[str, Any]]:
        return segment_service.inspect_number(*args, **kwargs)

    def get_number_status(self, *args: Any, **kwargs: Any) -> tuple[bool, str, dict[str, Any]]:
        return segment_service.inspect_number(*args, **kwargs)

    def list_usage(self, *args: Any, **kwargs: Any) -> tuple[bool, str, dict[str, Any]]:
        return segment_service.list_usage(*args, **kwargs)

    def list_todos(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return segment_service.list_todos(*args, **kwargs)

    def handle_custom_action(
        self, entry_id: int, action: str, payload: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any] | None, str, int | None]:
        """兼容统一 /actions 路由中的中文代办动作。"""
        payload = dict(payload or {})
        values = dict(payload.get("values") or {})
        merged = {**values, **{k: v for k, v in payload.items() if k not in {"values"}}}
        row = store.find(self.MODULE, entry_id)
        if row is None:
            return None, f"{self.ENTITY} {entry_id} 不存在或已归档", 404

        action = action.strip()
        if action in {"新增分段代办授权", "分段代办授权", "委托分段", "授权分段", "新增授权"}:
            ok, message, data = segment_service.create_delegation(
                dimension=merged.get("所属维度") or row.get("所属维度"),
                start=merged.get("键段起点") or row.get("键段起点"),
                end=merged.get("键段终点") or row.get("键段终点"),
                delegate=merged.get("接班人") or merged.get("delegate"),
                operator=merged.get("operator") or merged.get("操作员") or merged.get("委托人") or "甲",
                segment_no=row.get("键段编号"),
            )
            return data.get("delegation") or row, message, None if ok else int(data.get("http_status", 200))

        delegation_ref = (
            merged.get("delegation_id") or merged.get("授权编号")
            or row.get("delegation_id") or row.get("授权编号")
        )
        if action in {"撤销代办", "取消代办", "撤销授权", "取消授权"}:
            ok, message, data = segment_service.revoke_delegation(
                delegation_ref,
                operator=merged.get("operator") or merged.get("操作员") or merged.get("委托人") or "甲",
                expected_version=merged.get("version") or merged.get("expected_version"),
                reason=merged.get("remark") or merged.get("撤销原因"),
            )
            return data.get("delegation") or row, message, None if ok else int(data.get("http_status", 200))

        if action in {"保存草稿", "暂存填写", "保存未完成表单"}:
            call_kwargs = {
                k: v for k, v in merged.items()
                if k not in {"number", "号码", "键值", "operator", "操作员", "办理人"}
            }
            ok, message, data = segment_service.save_draft(
                delegation_ref,
                number=merged.get("number") or merged.get("号码") or merged.get("键值"),
                operator=merged.get("operator") or merged.get("操作员") or merged.get("办理人") or "乙",
                values=values,
                **call_kwargs,
            )
            return data.get("draft") or row, message, None if ok else int(data.get("http_status", 200))

        if action in {"确认分段", "填写并确认分段", "确认号码分配", "确认分配", "提交分配"}:
            call_kwargs = {
                k: v for k, v in merged.items()
                if k not in {
                    "number", "号码", "键值", "operator", "操作员", "办理人",
                    "dimension", "所属维度", "expected_version", "version",
                }
            }
            ok, message, data = segment_service.confirm_number(
                delegation_ref,
                number=merged.get("number") or merged.get("号码") or merged.get("键值"),
                operator=merged.get("operator") or merged.get("操作员") or merged.get("办理人") or "乙",
                dimension=merged.get("所属维度") or row.get("所属维度"),
                expected_version=merged.get("version") or merged.get("expected_version"),
                values=values,
                **call_kwargs,
            )
            return data.get("usage") or data.get("delegation") or row, message, None if ok else int(data.get("http_status", 200))

        return None, f"动作「{action}」不属于{self.MODULE}可执行范围", None

    def allocate_key(self, dim_name: str, natural_key: str) -> tuple[int | None, str]:
        """为自然键分配代理键：从键段中取出下一个可用键值。"""
        rows = store.rows(self.MODULE)
        for r in rows:
            if r.get("所属维度") == dim_name and r.get("status") in ("待分配", "分配中"):
                allocated = int(r.get("已分配数量", 0)) + 1
                r["已分配数量"] = allocated
                start = int(r.get("键段起点", 1))
                new_key = start + allocated - 1
                r["最近分配"] = str(allocated)
                if allocated >= int(r.get("键段终点", start + 100)):
                    r["status"] = "已耗尽"
                    r["pending"] = False
                r["status"] = r["status"] or "分配中"
                return new_key, f"自然键 {natural_key} 分配代理键 {new_key}"
        return None, f"维度 {dim_name} 无可用键段"

    def query_key_range(self, dim_name: str) -> dict[str, Any]:
        """查询维度的当前键段状态。"""
        rows = store.rows(self.MODULE)
        for r in rows:
            if r.get("所属维度") == dim_name:
                return {
                    "dim_name": dim_name,
                    "key_range_start": r.get("键段起点"),
                    "key_range_end": r.get("键段终点"),
                    "allocated": r.get("已分配数量", 0),
                    "remaining": int(r.get("键段终点", 0)) - int(r.get("已分配数量", 0)),
                    "strategy": r.get("分配策略", ""),
                }
        return {"dim_name": dim_name, "status": "未分配"}

    def reuse_policy(self, dim_name: str, natural_key: str) -> tuple[int | None, str]:
        """复用策略：同一自然键多次变更时，是否复用旧代理键。"""
        existing = self.query_key_range(dim_name)
        policy = existing.get("strategy", "")
        if policy == "REUSE":
            return existing.get("key_range_start", 1), f"复用旧代理键"
        return self.allocate_key(dim_name, natural_key)