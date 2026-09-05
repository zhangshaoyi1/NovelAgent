"""P1-6 评审/反馈意见结构化 + 确认闸口（core/quality/rewrite/instruction.py + FeedbackRewriter）。

覆盖：
- structure_feedback：子句拆分、类型/位置归类、子句数上限；
- render_instruction：标准"修改指令"块（问题清单 + 必须保留）；
- 确认闸口：confirm_fn=False 时不发起 LLM 调用、原章不动（confirm_rejected）；
- 确认通过：LLM 收到的 user prompt 是渲染后的结构化指令（原始反馈不再直入 prompt）；
- 不传 confirm_fn：向后兼容，仍走结构化指令链路。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from agent.core.quality.rewrite.feedback_rewriter import FeedbackRewriter
from agent.core.quality.rewrite.instruction import (
    render_instruction,
    structure_feedback,
)
from agent.core.quality.guardrails import Guardrails

REWRITTEN = "（重写版）节奏收紧后的正文。"


def _fake_llm() -> MagicMock:
    llm = MagicMock()
    llm.chat.return_value = SimpleNamespace(text=REWRITTEN)
    return llm


def _make_rewriter(tmp_path: Path, llm) -> FeedbackRewriter:
    d = __import__("tests.conftest", fromlist=["make_project"]).make_project(
        tmp_path, n_chapters=3
    )
    return FeedbackRewriter(
        d, llm_client=llm, guardrails=Guardrails(check_title=False, check_meta_leak=False)
    ), d


# ---------------------------------------------------------------- 结构化
def test_structure_feedback_classifies_type_and_position() -> None:
    inst = structure_feedback("开头太拖了。主角动机不合理，前后矛盾。", 12)
    assert inst.chapter == 12
    assert len(inst.issues) == 2
    assert inst.issues[0].type == "节奏" and inst.issues[0].position == "章首"
    assert inst.issues[1].type == "逻辑"
    assert inst.goal.startswith("开头太拖")


def test_structure_feedback_caps_and_unclassifiable() -> None:
    fb = "\n".join(f"问题{i}随便说说" for i in range(20))
    inst = structure_feedback(fb, 3)
    assert len(inst.issues) == 10  # 上限 10 条
    assert inst.issues[0].type == "其他" and inst.issues[0].position == "整章"


def test_render_instruction_block() -> None:
    inst = structure_feedback("太拖了", 7)
    inst.keep.append("章末钩子")
    block = render_instruction(inst)
    assert "【结构化修改指令（第 7 章）】" in block
    assert "[节奏|" in block
    assert "必须保留：章末钩子" in block
    assert "未提及的内容一律保留原样" in block


# ---------------------------------------------------------------- 确认闸口
def test_confirm_rejected_skips_llm_and_keeps_chapter(tmp_path: Path) -> None:
    rewriter, d = _make_rewriter(tmp_path, _fake_llm())
    ch_file = d / "chapters" / "ch002.md"
    before_body = rewriter._strip_frontmatter(ch_file)

    res = rewriter.rewrite(2, "节奏太慢", confirm_fn=lambda inst: False)

    assert res.llm_used is False and res.error == "confirm_rejected"
    assert res.new_text == before_body  # 原章正文保留
    rewriter.llm.chat.assert_not_called()  # 确认前零 LLM 调用


def test_confirm_accepted_sends_structured_instruction(tmp_path: Path) -> None:
    llm = _fake_llm()
    rewriter, _d = _make_rewriter(tmp_path, llm)

    res = rewriter.rewrite(
        2, "开头节奏太慢。主角动机不合理。",
        confirm_fn=lambda inst: True,
    )

    assert res.llm_used is True
    req = llm.chat.call_args[0][0]  # ChatRequest
    user_prompt = req.messages[-1]["content"]
    assert "【结构化修改指令（第 2 章）】" in user_prompt
    assert "[节奏|章首]" in user_prompt
    assert "[逻辑|整章]" in user_prompt


def test_no_confirm_fn_still_structures(tmp_path: Path) -> None:
    """向后兼容：不传 confirm_fn 也能正常改写（走结构化链路）。"""
    rewriter, _d = _make_rewriter(tmp_path, _fake_llm())
    res = rewriter.rewrite(2, "太拖了，删水")
    assert res.llm_used is True and res.changed_summary.startswith("按反馈重写")
