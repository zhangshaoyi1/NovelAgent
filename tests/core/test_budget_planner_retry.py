# -*- coding: utf-8 -*-
"""budget_planner._ask_llm 校验失败重试回归（2026-09-07 同类问题排查）。

chat_structured 仅以提示词嵌入 Schema，弱遵从度 provider 输出合法 JSON
但漏必填字段（horizon_chapters/subline_budget）→ 首败即 G3 降级为均衡分账。
修复：附真实错误详情重试一次后再降级。
"""

from __future__ import annotations

from agent.workflows.pipeline.budget_planner import BudgetPlanner, _BudgetSchema


class _FakeLLM:
    """依次回放预设响应/异常（模拟 chat_structured 行为）。"""

    def __init__(self, outcomes: list) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[list[dict[str, str]]] = []

    def chat(self, req, **kw):  # pragma: no cover - 不应走到
        raise AssertionError("应走 chat_structured 的 gateway.chat 路径")


def _make_planner(tmp_path, outcomes):
    pl = BudgetPlanner(project_dir=tmp_path, llm_client=None)
    pl._llm = _FakeLLM(outcomes)

    # _ask_llm 内部是延迟导入（from agent.client.gateway_adapter import chat_structured），
    # 必须 patch 源模块属性，否则 fake 不生效、测试会走真网络
    import agent.client.gateway_adapter as gw

    calls: list[list[dict[str, str]]] = []

    def fake_chat_structured(llm, messages, schema, **kw):
        calls.append(messages)
        outcome = llm.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return schema.model_validate(outcome)

    orig = gw.chat_structured
    gw.chat_structured = fake_chat_structured  # type: ignore[attr-defined]
    return pl, calls, gw, orig


def test_retry_once_on_missing_required_field(tmp_path):
    """首败（缺必填字段）→ 带错误详情重试 → 成功返回。"""
    pl, calls, gw, orig = _make_planner(
        tmp_path,
        [
            {"notes": "漏了必填字段"},  # 第一次：合法 JSON 但缺必填
            {"horizon_chapters": 100, "subline_budget": [{"subline_id": "S01", "chapters": 60}]},
        ],
    )
    try:
        budget = pl._ask_llm(["S01"], 100, {})
        assert budget.horizon_chapters == 100
    finally:
        gw.chat_structured = orig  # type: ignore[attr-defined]
    assert len(calls) == 2
    # 重试消息必须附带真实错误详情
    assert "【上一次的具体错误】" in calls[1][-1]["content"]
    assert "必填字段" in calls[1][-1]["content"]


def test_raise_after_second_failure(tmp_path):
    """两次均失败 → 抛出异常，由 plan() 的 G3 降级兜底。"""
    import pytest
    from pydantic import ValidationError

    bad = {"notes": "两次都漏字段"}
    pl, calls, gw, orig = _make_planner(tmp_path, [bad, bad])
    try:
        with pytest.raises(ValidationError):
            pl._ask_llm(["S01"], 100, {})
    finally:
        gw.chat_structured = orig  # type: ignore[attr-defined]
    assert len(calls) == 2
