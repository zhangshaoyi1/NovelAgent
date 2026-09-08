"""评估维度作用域声明 + G14 泄漏边界加固测试（2026-09-08）。

背景：
- G8 两维的结局窗口前置此前硬编码在 _collect_dims，未形成声明机制——
  新增全书级维度会重蹈"中途窗口被完结标准审判"假失败（2026-09-07 事故）。
- G14 元指令泄漏正则不含「修订说明」类交付尾注，五灵破归档 ch4 整块
  修订笔记混入正文漏网。
"""

from __future__ import annotations

from agent.agents.evaluator_dims import _DIM_SCOPE, _scope_allows
from agent.agents.evaluator_types import DimensionResult
from agent.core.quality.guardrails.guardrails import Guardrails, _META_LEAK_RE


# ---------------------------------------------------------------- 作用域声明
def test_g8_dims_declared_book_ending() -> None:
    assert _DIM_SCOPE["mainline_progress"] == "book_ending"
    assert _DIM_SCOPE["ending_convergence"] == "book_ending"


def test_scope_allows_window_always() -> None:
    # 未登记维度默认 window：中途窗口、结局窗口均启用
    assert _scope_allows("foreshadow_recycle_rate", in_book_ending_window=False)
    assert _scope_allows("foreshadow_recycle_rate", in_book_ending_window=True)


def test_scope_allows_book_ending_only_in_final_window() -> None:
    assert not _scope_allows("mainline_progress", in_book_ending_window=False)
    assert _scope_allows("mainline_progress", in_book_ending_window=True)


def test_dimension_result_default_scope_and_dict() -> None:
    d = DimensionResult("x", "测试维", 1.0, 0.5, ">=", False, "computed")
    assert d.scope == "window"
    assert d.to_dict()["scope"] == "window"


def test_g8_dims_carry_scope_in_result() -> None:
    # 构造器把声明写进结果（报告可观测）
    from agent.agents.evaluator_dims import _EvaluatorDimensionsMixin
    import inspect

    src = inspect.getsource(_EvaluatorDimensionsMixin._dim_mainline_progress)
    assert 'scope=_DIM_SCOPE["mainline_progress"]' in src


# ---------------------------------------------------------------- G14 泄漏边界
def test_meta_leak_detects_revision_notes() -> None:
    # 2026-09-08 五灵破归档 ch4 漏网模式：LLM 修订尾注整块混入章末
    g = Guardrails()
    text = "正文内容。\n\n---\n\n**修订说明：**\n1. 开篇钩子强化\n2. 章末悬念升级"
    assert g._check_meta_leak(text) is not None
    assert "修订说明" in _META_LEAK_RE.pattern


def test_meta_leak_detects_revision_note_variants() -> None:
    for marker in ("修订笔记", "改稿说明"):
        assert _META_LEAK_RE.search(f"正文。{marker}：xxxx"), marker


def test_meta_leak_clean_text_passes() -> None:
    g = Guardrails()
    # 正文正常讨论"修改"不含标记词组 → 不误报
    assert g._check_meta_leak("他说完便修改了剑谱的第三式，不再多言。") is None
