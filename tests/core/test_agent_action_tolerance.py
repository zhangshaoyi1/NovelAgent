# -*- coding: utf-8 -*-
"""AgentAction 结构化输出容错回归（2026-09-07 Writer 校验失败事故）。

事故：弱 schema 遵从度 provider 输出合法 JSON 但漏掉必填 action 字段
（``1 validation error for AgentAction action Field required``），
重试提示词错误归因为「未能解析为 JSON」→ 第二次照样翻车。

修复：三层防御——
1. AgentAction 容错推断（before validator）：缺 action 时按 draft/tool 语义反推；
2. action 值变体归一（after validator）：done/submit/tool 等别名归一；
3. 重试提示词动态附带真实校验错误 + 补漏字段说明。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agent.core.engine.agent_loop import AgentAction


class TestInferMissingAction:
    """缺 action 字段时的语义反推。"""

    def test_missing_action_with_tool_infers_tool_call(self) -> None:
        """典型事故形态：{think, tool, args} 缺 action → 推断为 tool_call。"""
        act = AgentAction.model_validate(
            {"think": "先看大纲", "tool": "read_outline", "args": {"path": "outline.md"}}
        )
        assert act.action == "tool_call"
        assert act.tool == "read_outline"

    def test_missing_action_with_draft_infers_finish(self) -> None:
        """典型事故形态：{think, draft} 缺 action → 推断为 finish。"""
        act = AgentAction.model_validate({"think": "写完了", "draft": "第一章正文……"})
        assert act.action == "finish"
        assert act.draft == "第一章正文……"

    def test_missing_action_and_no_semantic_hints_still_fails(self) -> None:
        """既无 action 又无 draft/tool 的无意义输出仍应校验失败（不静默放行）。"""
        with pytest.raises(ValidationError):
            AgentAction.model_validate({"think": "..."})

    def test_explicit_action_not_overridden(self) -> None:
        """显式给了 action 时不得被推断覆盖。"""
        act = AgentAction.model_validate(
            {"action": "tool_call", "tool": "x", "draft": "不应影响判断"}
        )
        assert act.action == "tool_call"


class TestActionAliasNormalization:
    """action 值变体归一。"""

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("done", "finish"),
            ("submit", "finish"),
            ("final", "finish"),
            ("tool", "tool_call"),
            ("toolcall", "tool_call"),
            ("call_tool", "tool_call"),
            ("Finish", "finish"),
            (" TOOL_CALL ", "tool_call"),
        ],
    )
    def test_alias_normalization(self, raw: str, expected: str) -> None:
        act = AgentAction.model_validate({"action": raw})
        assert act.action == expected

    def test_unknown_action_value_fails(self) -> None:
        """未知 action 值仍校验失败，交由上层重试/报错（不静默放行）。"""
        with pytest.raises(ValidationError):
            AgentAction.model_validate({"action": "explode"})


class TestRetryPromptCoversFieldMissing:
    """重试提示词必须覆盖「JSON 合法但缺字段」场景。"""

    def test_md_prompt_mentions_missing_action(self) -> None:
        from pathlib import Path

        md = (
            Path(__file__).resolve().parents[2]
            / "src"
            / "agent"
            / "prompts"
            / "agents"
            / "writer_retry.md"
        )
        text = md.read_text(encoding="utf-8")
        assert "必填字段" in text
        assert "action" in text

    def test_fallback_constant_mentions_missing_action(self) -> None:
        from agent.agents.writer_agent import _RETRY_JSON_PROMPT

        assert "必填字段" in _RETRY_JSON_PROMPT
        assert "action" in _RETRY_JSON_PROMPT

    def test_prompt_manager_serves_updated_retry(self) -> None:
        from agent.core.infra.prompt_manager import pm

        system = pm.get("agents.writer_retry").system
        assert "必填字段" in system
