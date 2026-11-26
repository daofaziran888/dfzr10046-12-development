"""分段代办授权 HTTP 接口。

UI 使用 /api/surrogate_key/... 下的语义路径；同时保留 /api/segment-delegations
前缀别名，方便不同测试/调用习惯。业务判断全部在 SegmentDelegationService。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.services.segment_delegation import segment_service
from app.store import store

router = APIRouter(tags=["分段代办授权"])


@router.post("/api/test/reset")
@router.post("/api/reset")
def reset_workspace() -> JSONResponse:
    """测试/演示隔离接口：恢复种子数据并清空分段代办内存状态。"""
    store.reset_seed_data()
    segment_service.reset()
    return JSONResponse(status_code=200, content={"ok": True, "message": "workspace reset"})


async def _json(request: Request) -> dict[str, Any]:
    try:
        data = await request.json()
    except Exception:  # noqa: BLE001 - 空请求体在部分动作中合法
        return {}
    return data if isinstance(data, dict) else {}


def _operator(request: Request, data: dict[str, Any] | None = None) -> str:
    data = data or {}
    query = request.query_params
    headers = request.headers

    def clean(value: Any) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        # HTTP 头按 latin-1 解码；中文操作员经常以 UTF-8 字节放入 X-Operator，
        # 这里还原一次，避免浏览器/curl 传入“乙”后变成 mojibake。
        try:
            repaired = text.encode("latin-1").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            return text
        return repaired or text

    auth = headers.get("authorization", "")
    bearer = auth[7:].strip() if auth.lower().startswith("bearer ") else ""

    return clean(
        data.get("operator") or data.get("current_operator") or data.get("currentUser")
        or data.get("user") or data.get("username") or data.get("actor")
        or data.get("delegator") or data.get("owner") or data.get("委托人")
        or data.get("操作员") or data.get("办理人")
        or query.get("operator") or query.get("current_operator") or query.get("user")
        or query.get("username") or query.get("actor")
        or query.get("操作员")
        or headers.get("x-operator") or headers.get("x-user")
        or headers.get("x-username") or headers.get("x-actor")
        or bearer
        or "甲"
    )


def _response(result: tuple[bool, str, dict[str, Any]]) -> JSONResponse:
    ok, message, data = result
    data = dict(data or {})
    status_code = 200 if ok else int(data.pop("http_status", 400) or 400)
    body: dict[str, Any] = {"ok": ok, "message": message}
    body.update(data)
    # 通用 ActionResult 形状兼容。
    if "entry" not in body:
        for key in ("delegation", "draft", "usage"):
            if key in body:
                body["entry"] = body[key]
                break
    return JSONResponse(status_code=status_code, content=body)


def _ok_payload(payload: dict[str, Any]) -> JSONResponse:
    return JSONResponse(status_code=200, content={"ok": True, **payload})


@router.post("/api/surrogate_key/actions")
async def surrogate_collection_actions(request: Request) -> JSONResponse:
    """兼容把动作直接提交到模块集合，而不是 /{id}/actions 的调用方式。"""
    data = await _json(request)
    action = str(data.get("action") or "").strip()
    operator = _operator(request, data)

    if action in {"新增分段代办授权", "分段代办授权", "委托分段", "授权分段", "新增授权", "delegate"}:
        payload = {k: v for k, v in data.items() if k not in {"action", "operator"}}
        return _response(segment_service.create_delegation(operator=operator, **payload))

    ref = data.get("delegation_id") or data.get("授权编号") or data.get("ref") or data.get("id")
    if action in {"撤销代办", "取消代办", "撤销授权", "取消授权", "revoke", "cancel"}:
        return _response(segment_service.revoke_delegation(
            ref, operator=operator,
            expected_version=data.get("version") or data.get("expected_version"),
            reason=data.get("reason") or data.get("撤销原因"),
        ))
    if action in {"保存草稿", "暂存填写", "保存未完成表单", "draft"}:
        return _response(segment_service.save_draft(
            ref,
            number=data.get("number") or data.get("号码"),
            operator=operator,
            values=data.get("values") if isinstance(data.get("values"), dict) else data,
        ))
    if action in {"确认分段", "填写并确认分段", "确认号码分配", "确认分配", "提交分配", "confirm"}:
        return _response(segment_service.confirm_number(
            ref,
            number=data.get("number") or data.get("号码"),
            operator=operator,
            dimension=data.get("dimension") or data.get("所属维度"),
            expected_version=data.get("version") or data.get("expected_version"),
            values=data.get("values") if isinstance(data.get("values"), dict) else None,
            natural_key=data.get("natural_key") or data.get("自然键"),
            content=data.get("content") or data.get("办理内容"),
        ))
    return JSONResponse(status_code=400, content={"ok": False, "message": f"不支持的分段代办动作：{action}"})


# ---------------------------------------------------------------- delegations
@router.post("/api/surrogate_key/delegations")
@router.post("/api/surrogate_key/segment-delegations")
@router.post("/api/surrogate_key/grants")
@router.post("/api/surrogate_key/authorizations")
@router.post("/api/surrogate_key/delegate")
@router.post("/api/segment-delegations")
@router.post("/api/delegations")
async def create_delegation(request: Request) -> JSONResponse:
    data = await _json(request)
    operator = _operator(request, data)
    payload = {k: v for k, v in data.items() if k not in {"operator", "current_operator", "user"}}
    result = segment_service.create_delegation(operator=operator, **payload)
    return _response(result)


@router.get("/api/surrogate_key/delegations")
@router.get("/api/surrogate_key/segment-delegations")
@router.get("/api/surrogate_key/grants")
@router.get("/api/surrogate_key/authorizations")
@router.get("/api/segment-delegations")
@router.get("/api/delegations")
def list_delegations(request: Request) -> JSONResponse:
    q = request.query_params
    result = segment_service.list_delegations(
        operator=_operator(request),
        dimension=q.get("dimension") or q.get("所属维度"),
        status=q.get("status"),
        all_rows=q.get("all") in {"1", "true", "yes"},
    )
    return _response(result)


@router.get("/api/surrogate_key/delegations/{ref}")
@router.get("/api/surrogate_key/segment-delegations/{ref}")
@router.get("/api/surrogate_key/grants/{ref}")
@router.get("/api/surrogate_key/authorizations/{ref}")
@router.get("/api/segment-delegations/{ref}")
@router.get("/api/delegations/{ref}")
def get_delegation(ref: str, request: Request) -> JSONResponse:
    return _response(segment_service.get_delegation(ref, _operator(request)))


@router.post("/api/surrogate_key/delegations/{ref}/revoke")
@router.post("/api/surrogate_key/delegations/{ref}/cancel")
@router.post("/api/surrogate_key/segment-delegations/{ref}/revoke")
@router.post("/api/surrogate_key/grants/{ref}/revoke")
@router.post("/api/surrogate_key/grants/{ref}/cancel")
@router.post("/api/segment-delegations/{ref}/revoke")
@router.post("/api/delegations/{ref}/revoke")
async def revoke_delegation(ref: str, request: Request) -> JSONResponse:
    data = await _json(request)
    result = segment_service.revoke_delegation(
        ref,
        operator=_operator(request, data),
        expected_version=data.get("version") or data.get("expected_version"),
        reason=data.get("reason") or data.get("撤销原因"),
    )
    return _response(result)


# ---------------------------------------------------------------------- drafts
@router.post("/api/surrogate_key/delegations/{ref}/draft")
@router.post("/api/surrogate_key/delegations/{ref}/numbers/{number}/draft")
async def save_draft(ref: str, number: str | None = None, request: Request = None) -> JSONResponse:  # type: ignore[assignment]
    data = await _json(request)
    result = segment_service.save_draft(
        ref,
        number=number or data.get("number") or data.get("号码"),
        operator=_operator(request, data),
        values=data.get("values") if isinstance(data.get("values"), dict) else data,
        **{
            k: v for k, v in data.items()
            if k not in {"values", "operator", "number", "号码"}
        },
    )
    return _response(result)


@router.get("/api/surrogate_key/delegations/{ref}/draft/{number}")
def get_draft(ref: str, number: str, request: Request) -> JSONResponse:
    operator = _operator(request)
    draft = segment_service.get_draft(ref, number, operator)
    if draft is None:
        return JSONResponse(status_code=404, content={"ok": False, "message": "未找到可恢复草稿"})
    return JSONResponse(status_code=200, content={"ok": True, "draft": draft})


# --------------------------------------------------------------------- confirm
@router.post("/api/surrogate_key/delegations/{ref}/confirm")
@router.post("/api/surrogate_key/delegations/{ref}/submit")
@router.post("/api/surrogate_key/delegations/{ref}/numbers/{number}/confirm")
@router.post("/api/surrogate_key/delegations/{ref}/numbers/{number}/allocate")
async def confirm_delegated_number(
    ref: str,
    request: Request,
    number: str | None = None,
) -> JSONResponse:
    data = await _json(request)
    result = segment_service.confirm_number(
        ref,
        number=number or data.get("number") or data.get("号码"),
        operator=_operator(request, data),
        dimension=data.get("dimension") or data.get("所属维度"),
        expected_version=data.get("version") or data.get("expected_version"),
        values=data.get("values") if isinstance(data.get("values"), dict) else None,
        natural_key=data.get("natural_key") or data.get("自然键"),
        content=data.get("content") or data.get("办理内容"),
        **{
            k: v for k, v in data.items()
            if k not in {
                "number", "号码", "dimension", "所属维度", "version", "expected_version",
                "values", "natural_key", "自然键", "content", "办理内容", "operator",
            }
        },
    )
    return _response(result)


@router.post("/api/surrogate_key/numbers/{number}/allocate")
async def allocate_number(number: str, request: Request) -> JSONResponse:
    data = await _json(request)
    if data.get("delegation_id") or data.get("授权编号"):
        result = segment_service.confirm_number(
            data.get("delegation_id") or data.get("授权编号"),
            number=number,
            operator=_operator(request, data),
            dimension=data.get("dimension") or data.get("所属维度"),
            expected_version=data.get("version"),
            values=data.get("values") if isinstance(data.get("values"), dict) else None,
            natural_key=data.get("natural_key") or data.get("自然键"),
            content=data.get("content") or data.get("办理内容"),
        )
    else:
        result = segment_service.allocate_number_direct(
            dimension=data.get("dimension") or data.get("所属维度"),
            number=number,
            operator=_operator(request, data),
            natural_key=data.get("natural_key") or data.get("自然键"),
            content=data.get("content") or data.get("办理内容"),
            values=data.get("values") if isinstance(data.get("values"), dict) else None,
        )
    return _response(result)


# ----------------------------------------------------------------------- query
@router.get("/api/surrogate_key/todos")
def list_todos(request: Request) -> JSONResponse:
    q = request.query_params
    payload = segment_service.list_todos(
        operator=_operator(request),
        assignee=q.get("assignee") or q.get("接班人"),
    )
    return _ok_payload(payload)


@router.get("/api/surrogate_key/usage")
@router.get("/api/surrogate_key/records")
def list_usage(request: Request) -> JSONResponse:
    q = request.query_params
    result = segment_service.list_usage(
        operator=_operator(request),
        dimension=q.get("dimension") or q.get("所属维度"),
        number=q.get("number") or q.get("号码"),
        delegation_id=q.get("delegation_id") or q.get("授权编号"),
    )
    return _response(result)


@router.get("/api/surrogate_key/availability")
def availability(request: Request) -> JSONResponse:
    q = request.query_params
    d = segment_service._find_delegation(q.get("delegation_id") or q.get("授权编号"))
    if d is None:
        return JSONResponse(status_code=404, content={"ok": False, "message": "授权不存在"})
    operator = _operator(request)
    if operator:
        denial = segment_service._assert_view(d, operator)
        if denial:
            return JSONResponse(status_code=int(denial["http_status"]), content={"ok": False, **denial})
    public = segment_service._public_delegation(d, operator)
    return _ok_payload({
        "delegation": public,
        "available_numbers": public["available_numbers"],
        "usable_numbers": public["usable_numbers"],
        "used_numbers": public["used_numbers"],
        "draft_numbers": public["draft_numbers"],
    })


@router.get("/api/surrogate_key/numbers/{number}")
def inspect_number(number: str, request: Request) -> JSONResponse:
    q = request.query_params
    result = segment_service.inspect_number(
        dimension=q.get("dimension") or q.get("所属维度"),
        number=number,
        operator=_operator(request),
        delegation_ref=q.get("delegation_id") or q.get("授权编号"),
    )
    return _response(result)


@router.get("/api/surrogate_key/reconcile")
def reconcile(number: str, request: Request) -> JSONResponse:
    q = request.query_params
    result = segment_service.inspect_number(
        dimension=q.get("dimension") or q.get("所属维度"),
        number=q.get("number") or number,
        operator=_operator(request),
        delegation_ref=q.get("delegation_id") or q.get("授权编号"),
    )
    return _response(result)


@router.post("/api/surrogate_key/reconcile")
async def reconcile_post(request: Request) -> JSONResponse:
    data = await _json(request)
    result = segment_service.inspect_number(
        dimension=data.get("dimension") or data.get("所属维度"),
        number=data.get("number") or data.get("号码"),
        operator=_operator(request, data),
        delegation_ref=data.get("delegation_id") or data.get("授权编号"),
    )
    return _response(result)


@router.get("/api/surrogate_key/same-number-control")
def same_number_control(request: Request) -> JSONResponse:
    q = request.query_params
    payload = segment_service.same_number_control(
        number=q.get("number") or "104",
        dimension_a=q.get("dimension_a") or q.get("主维度"),
        dimension_b=q.get("dimension_b") or q.get("对照维度"),
        operator=_operator(request),
    )
    return _ok_payload(payload)


# --------------------------------------------------------------------- demo/QA
@router.post("/api/surrogate_key/scenarios/delegation-conflict")
async def setup_conflict_scenario(request: Request) -> JSONResponse:
    data = await _json(request)
    payload = segment_service.setup_conflict_scenario(
        operator=_operator(request, data),
        **{k: v for k, v in data.items() if k != "operator"},
    )
    return _ok_payload(payload)


@router.post("/api/surrogate_key/delegations/{ref}/race-conflict")
async def race_conflict(ref: str, request: Request) -> JSONResponse:
    data = await _json(request)
    payload = segment_service.race_confirm_and_revoke(
        ref,
        number=data.get("number") or data.get("号码") or 104,
        operator=data.get("operator") or data.get("owner") or "甲",
        delegate=data.get("delegate") or data.get("接班人") or "乙",
        settle=bool(data.get("settle", False)),
        natural_key=data.get("natural_key"),
        content=data.get("content"),
    )
    return _ok_payload(payload)
