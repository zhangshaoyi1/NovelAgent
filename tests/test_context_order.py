"""P0-1 KV 缓存感知上下文段排序（core/infra/context_order.py）。

覆盖：
- 三层排序：stable 段全部位于 semi 之前、semi 位于 volatile 之前；
- 同层保序（稳定排序，结果确定性）；
- 空段跳过；
- 与 m5_write_chapter._generate_chapter 的 system prompt 装配契约：
  稳定段（base/style）先于易变段（payoff/continuity），且各段内容不因排序改变。
"""

from __future__ import annotations

from pathlib import Path

from agent.core.infra.context_order import (
    SEMI,
    STABLE,
    VOLATILE,
    PromptSection,
    order_sections,
)


def test_order_stable_before_volatile() -> None:
    out = order_sections(
        [
            PromptSection("a", "易变A", VOLATILE),
            PromptSection("b", "稳定B", STABLE),
            PromptSection("c", "易变C", VOLATILE),
            PromptSection("d", "缓变D", SEMI),
        ]
    )
    assert out.index("稳定B") < out.index("缓变D") < out.index("易变A")
    assert out.index("易变A") < out.index("易变C")


def test_same_tier_keeps_insertion_order() -> None:
    out = order_sections(
        [
            PromptSection("v1", "V1", VOLATILE),
            PromptSection("v2", "V2", VOLATILE),
            PromptSection("v3", "V3", VOLATILE),
        ]
    )
    assert out == "V1\n\nV2\n\nV3"


def test_empty_sections_skipped() -> None:
    out = order_sections(
        [
            PromptSection("empty", "", STABLE),
            PromptSection("x", "内容", VOLATILE),
        ]
    )
    assert out == "内容"


def test_all_empty_returns_empty_string() -> None:
    assert order_sections([PromptSection("e", "", STABLE)]) == ""


# ---------------------------------------------------------------- 装配契约
def _min_ctx(**overrides: object) -> dict:
    """最小 ctx（与 test_g11_style._min_ctx 同构，覆盖 _generate_chapter 的键）。"""
    wi = {
        "title": "书",
        "tone": "热血",
        "pov": "第三",
        "rhythm": "快",
        "chapter_length": 3000,
        "info_density": "中",
        "banned_elements": [],
        "genre_label": "都市",
        "synopsis": "简介",
        "realm_system": "",
        "golden_finger_info": "",
    }
    ctx: dict = {
        "world_info": wi,
        "chapter_num": 3,
        "subline_id": "S01",
        "subline_name": "支线",
        "subline_goal": "目标",
        "pressure_stage": "冲突",
        "tension_level": "中",
        "route_node_id": "N01",
        "route_milestone": "",
        "route_main_title": "",
        "route_main_result": "",
        "route_main_growth": "",
        "characters_info": "- 主角",
        "relations_info": "（关系网未生成）",
        "foreshadow_task": "",
        "prev_chapter_summary": "前情",
        "rag_context": "",
        "open_debts": "",
        "continuity_projection": "",
        "learnings_text": "（暂无已沉淀的写法记忆）",
        "reuse_guard_text": "",
        "ending_mode": False,
        "ending": "",
        "mainline": [],
        "style_guide": "",
        "payoff_task": "",
        "emotion_target": "",
        "reader_signals": [],
        "character_constraints": "",
    }
    ctx.update(overrides)
    return ctx


class _CaptureLLM:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    def chat(self, req, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        self.messages = req.messages if hasattr(req, "messages") else req
        from types import SimpleNamespace

        return SimpleNamespace(text="正文。")


def test_generate_chapter_stable_sections_precede_volatile() -> None:
    """system prompt 内：稳定段（base/style）必须出现在易变段（payoff/continuity）之前。"""
    from agent.workflows.writing.m5_write_chapter import M5WriteChapterWorkflow

    llm = _CaptureLLM()
    wf = M5WriteChapterWorkflow(Path("."), llm_client=llm, pre_validate=False)
    wf._generate_chapter(
        _min_ctx(
            style_guide="冷峻白描",
            payoff_task="# 爽点剧本\n本章爽点：打脸",
            continuity_projection="【连续性账本投影】已定事实…",
        )
    )
    system = llm.messages[0]["content"]
    assert system.index("冷峻白描") < system.index("打脸")
    assert system.index("冷峻白描") < system.index("连续性账本投影")


def test_generate_chapter_sections_content_preserved() -> None:
    """排序不得改变任何段内容（逐段子串仍在）。"""
    from agent.workflows.writing.m5_write_chapter import M5WriteChapterWorkflow

    llm = _CaptureLLM()
    wf = M5WriteChapterWorkflow(Path("."), llm_client=llm, pre_validate=False)
    guide = "冷峻白描、短句"
    projection = "【连续性账本投影】F-001 已埋"
    payoff = "# 爽点剧本\n本章爽点：揭密"
    wf._generate_chapter(
        _min_ctx(
            style_guide=guide,
            payoff_task=payoff,
            continuity_projection=projection,
        )
    )
    system = llm.messages[0]["content"]
    for fragment in (guide, projection.strip(), "揭密"):
        assert fragment in system
