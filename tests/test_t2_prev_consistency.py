"""T2 跨章复述一致性：质检 prompt 注入上一章原文（规则 13 prev_consistency）

回归背景：无灵纸条归属式矛盾（ch001 纸条是执事塞的，ch002 复述成周长老说的）
——writer 看得到上一章全文，审稿 gate 看不到，复述错了没人拦。
对应登记：项目文档/优化/20260913_三本毒蛇点评复盘.md。
"""

from __future__ import annotations

import inspect

from agent.core.infra.prompt_manager import PromptManager


def _render(**overrides):
    pm = PromptManager()
    kwargs = dict(
        tone="克制",
        chapter_length=3000,
        characters_fingerprint="fp",
        is_climax="否",
        stage_calibration="x",
        recheck_focus="y",
        hard_constraints="",
        plot_points="",
        fact_card="FC",
        prev_chapter_excerpt="上一章结尾原文……",
        chapter_text="正文",
    )
    kwargs.update(overrides)
    return pm.get("m5.quality_check").render_user(**kwargs)


def test_prompt_contains_rule13_and_excerpt_block() -> None:
    pm = PromptManager()
    tpl = pm.get("m5.quality_check")
    assert "prev_consistency" in tpl.system, "system 段必须含规则 13"
    assert "13 项规则" in tpl.system
    user = _render()
    assert "【上一章原文摘录】" in user
    assert "上一章结尾原文……" in user


def test_prompt_without_excerpt_still_renders() -> None:
    # 旧调用点/缺省场景：摘录为空不炸模板，且不留未替换变量
    user = _render(prev_chapter_excerpt="")
    assert "【上一章原文摘录】" in user
    assert "{{" not in user


def test_gate_path_passes_prev_excerpt() -> None:
    # 2026-09-16：写章入口收敛为唯一 AgenticWriteWorkflow（m5_quality_gate 的
    # 质检主体已随废弃入口删除）。规则 13 的透传能力必须在该唯一入口保留：
    # 既要从 ctx 取上一章原文（prev_chapter_summary），又要透传给 render_user。
    import agent.workflows.writing.agentic_write as aw

    src = inspect.getsource(aw)
    assert "prev_chapter_excerpt=" in src, "agentic_write 未透传 prev_chapter_excerpt"
    assert "prev_chapter_summary" in src, "agentic_write 未从 ctx 取上一章原文"
