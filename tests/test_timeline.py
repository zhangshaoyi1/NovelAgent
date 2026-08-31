"""G15 P0-3 时序与叙事分层 Timeline：真实时间线 vs 阅读顺序/披露层级解耦"""

from __future__ import annotations

from agent.core.base.validation import validate_model
from agent.core.story.timeline import (
    find_event,
    placements_for_chapter,
    StoryEvent,
    NarrativePlacement,
    Timeline,
)


def _timeline() -> Timeline:
    # 真实时间线：哥哥入狱(e_in) → 被发现(e_found) → 释放(e_release)
    # 叙事呈现：第 1 章先 flashback「哥哥入狱」制造悬念，第 5 章 scene 本体
    return Timeline(
        events=[
            StoryEvent(event_id="e_in", title="入狱", time_mode="exact",
                       connections=[{"to": "e_found", "kind": "causes"}]),
            StoryEvent(event_id="e_found", title="被发现", time_mode="sequence"),
            StoryEvent(event_id="e_release", title="释放", time_mode="sequence"),
        ],
        placements=[
            NarrativePlacement(event_id="e_in", chapter=1, mode="flashback",
                               disclosure="hint"),
            NarrativePlacement(event_id="e_found", chapter=5, mode="scene",
                               disclosure="full"),
            NarrativePlacement(event_id="e_release", chapter=9, mode="scene",
                               disclosure="full"),
        ],
    )


def test_stratification_decouples_time_and_narrative() -> None:
    """同一事件可在叙事上多次、跨章呈现，时间线只有一条。"""
    tl = _timeline()
    # 真实时间线顺序（event 层面）与章节（narrative 层面）可以不一致：
    # e_in 真实最早，但在第 1 章以 flashback 出现（非本体场景）
    ev = tl.events[0]
    assert ev.event_id == "e_in"
    assert ev.story_order is None  # sequence 模式，不强制数值顺序
    assert tl.placements[0].chapter == 1
    assert tl.placements[0].mode == "flashback"  # 叙事呈现 ≠ 本体场景


def test_find_event() -> None:
    tl = _timeline()
    assert find_event(tl, "e_in").title == "入狱"
    assert find_event(tl, "ghost") is None


def test_placements_for_chapter_bounded() -> None:
    tl = _timeline()
    pl = placements_for_chapter(tl, 5)
    assert len(pl) == 1
    assert pl[0].event_id == "e_found"
    # 无义章节：返回空列表
    assert placements_for_chapter(tl, 99) == []


def test_connection_forward_ref_resolved() -> None:
    """时间线校验：connection/placement 引用的事件必须在 events 中存在。"""
    tl = _timeline()
    ok, msg, _ = validate_model(Timeline, tl)
    assert ok, msg


def test_prevents_flashback_invention_of_false_scene() -> None:
    """边界：本体场景(scene)披露不得为 false（区分呈现 vs 真实）。"""
    ok, _, _ = validate_model(
        NarrativePlacement,
        {"event_id": "e_in", "chapter": 2, "mode": "scene", "disclosure": "false"},
    )
    assert not ok