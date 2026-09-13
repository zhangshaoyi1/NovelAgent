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


def test_meta_leak_detects_wuling_leak_samples() -> None:
    # 2026-09-13 无灵 ch166/176/206/260 实测泄漏样本回归（点评复核发现，此前全部漏网）
    samples = (
        '1. 开场钩子：以"灵动的阴影被晨光拉得很长"作为开场',       # ch166
        "5. 英文污染：将原文中的''改为中文'存在'，符合叙事化要求",  # ch166
        "3. 场景占比：增加了晨风、松涛、石阶血迹等环境描写",        # ch166
        "正文从约1619字扩展至约2450字，符合目标字数要求",           # ch176
        "本章原文字数约1990字，现扩写至约2500字，达到目标字数要求。",  # ch260
        "- 扩写了铁山率队冲击敌阵的场景，增加具体战斗画面",          # ch206
    )
    for s in samples:
        assert _META_LEAK_RE.search(s), f"泄漏样本漏检：{s[:30]}"


def test_meta_leak_clean_text_passes() -> None:
    g = Guardrails()
    # 正文正常讨论"修改"不含标记词组 → 不误报
    assert g._check_meta_leak("他说完便修改了剑谱的第三式，不再多言。") is None
    # 新增模式不得误伤正常叙事（约/扩展/目标等词的日常用法）
    for s in (
        "这场风暴席卷了整座城。",
        "老者约他三日后一叙。",
        "他的目标很远大。",
        "他在风中站了很久，直到天黑。",
    ):
        assert _META_LEAK_RE.search(s) is None, f"误报：{s}"
