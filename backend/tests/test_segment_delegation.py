"""分段代办授权的领域不变量测试。"""
from __future__ import annotations

from app.services.segment_delegation import segment_service


def create_standard_delegation():
    ok, message, data = segment_service.create_delegation(
        dimension="客户维度", start=100, end=109,
        delegate="乙", operator="甲",
    )
    assert ok, message
    return data["delegation"]


def test_delegation_syncs_todo_and_exposes_available_numbers():
    d = create_standard_delegation()
    assert d["available_numbers"] == list(range(100, 110))
    todo = segment_service.list_todos(operator="乙")["items"][0]
    assert todo["todo_type"] == "分段代办授权"
    assert todo["授权结论"]
    assert todo["available_numbers"] == list(range(100, 110))


def test_dimension_and_number_together_identify_usage():
    d = create_standard_delegation()
    ok, _, _ = segment_service.confirm_number(d["id"], 104, "乙", expected_version=1)
    assert ok

    # 同号但不同维度，不能使用客户维度的授权。
    ok, message, data = segment_service.confirm_number(
        d["id"], 104, "乙", dimension="产品维度", expected_version=2,
    )
    assert not ok
    assert data["code"] == "cross_dimension_denied"
    assert "维度隔离" in message

    # 产品维度自己的 104 仍可由其归属人分配，形成同号对照。
    ok, _, control = segment_service.allocate_number_direct("产品维度", 104, "甲")
    assert ok
    assert control["usage"]["dimension_key"] != "DIM-001"


def test_revocation_preserves_history_and_makes_delegate_read_only():
    d = create_standard_delegation()
    ok, _, usage_data = segment_service.confirm_number(
        d["id"], 104, "乙", expected_version=1, natural_key="NK-104",
    )
    assert ok

    ok, _, revoke_data = segment_service.revoke_delegation(d["id"], "甲", expected_version=2)
    assert ok
    assert revoke_data["delegation"]["status"] == "已撤销"

    # 历史记录没有被撤销动作改写。
    ok, _, records = segment_service.list_usage(operator="乙", dimension="客户维度", number=104)
    assert ok
    assert records["total"] == 1
    record = records["items"][0]
    assert record["status"] == "已确认"
    assert record["natural_key"] == "NK-104"

    # 接班人不能再写，但仍能读。
    ok, message, data = segment_service.confirm_number(d["id"], 105, "乙")
    assert not ok
    assert data["code"] == "read_only"
    assert "只读" in message
    ok, _, detail = segment_service.get_delegation(d["id"], "乙")
    assert ok
    assert detail["delegation"]["permissions"]["read_only"] is True

    # 维度归属人仍可继续填写；104 已占用，不能再次分配。
    ok, message, status = segment_service.inspect_number("客户维度", 104, "甲", d["id"])
    assert ok
    assert status["officially_used"] is True
    assert status["can_allocate_again"] is False
    assert status["continuable_operators"] == ["甲"]
    assert "已正式使用" in message


def test_outsider_is_denied_for_dimension():
    d = create_standard_delegation()
    ok, _, data = segment_service.get_delegation(d["id"], "丙")
    assert not ok
    assert data["http_status"] == 403

    ok, _, data = segment_service.revoke_delegation(d["id"], "丙")
    assert not ok
    assert data["code"] == "forbidden"


def test_draft_is_recoverable_but_does_not_consume_number():
    d = create_standard_delegation()
    ok, _, data = segment_service.save_draft(
        d["id"], 104, "乙", {"natural_key": "NK-draft", "content": "未完成"},
    )
    assert ok
    assert 104 in data["available_numbers"]
    assert 104 in data["delegation"]["draft_numbers"]

    draft = segment_service.get_draft(d["id"], 104, "乙")
    assert draft["natural_key"] == "NK-draft"


def test_concurrent_confirm_and_revoke_have_one_winner_and_consistent_final_state():
    d = create_standard_delegation()
    result = segment_service.race_confirm_and_revoke(
        d["id"], 104, "甲", "乙", settle=True,
    )
    assert result["success_count"] == 1
    assert result["winner"] in {"confirm", "revoke"}
    assert result["inspection"]["officially_used"] is True
    assert result["inspection"]["can_allocate_again"] is False
    assert result["inspection"]["continuable_operators"] == ["甲"]

    # 再并发确认同一个 104，也只能有一条正式使用记录。
    latest = segment_service.get_delegation(d["id"], "甲")[2]["delegation"]
    first = segment_service.confirm_number(d["id"], 104, "甲", expected_version=latest["version"])
    assert not first[0]
    assert first[2]["code"] == "number_already_used"
