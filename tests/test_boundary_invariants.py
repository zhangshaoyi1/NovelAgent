"""G15 P0-5 边界不变式：LLMClient/领域层入口统一校验（core/base/validation）

设计：结构化数据进入领域层之前，统一经 ``validate_model`` 拒绝脏数据，绝不透传。
本文件用 core/continuity 与 core/story 各模型验证不变式被入口强制。
"""

from __future__ import annotations

from agent.core.base.validation import validate_model, validate_many
from agent.core.continuity.models import (
    ContinuityFact,
    ContinuityHandoff,
    ContinuityKnowledge,
    ContinuityLedger,
    ContinuityOpenLoop,
)
from agent.core.story.foresight import ForesightBeat, ForesightThread
from agent.core.story.timeline import NarrativePlacement, StoryEvent, Timeline


# ------------------------------------------------------------
# validate_model：唯一性 / 引用一致性 / 状态机合法性 的边界拒绝
# ------------------------------------------------------------
def test_valid_model_passes() -> None:
    ok, msg, m = validate_model(
        ContinuityFact,
        {"domain": "character", "subject_id": "林寻", "field": "alive",
         "value": "true", "source_commit_id": "ch001", "evidence": "原文"},
    )
    assert ok and m is not None and msg == ""
    assert m.domain == "character"


def test_empty_field_rejected() -> None:
    ok, msg, _ = validate_model(
        ContinuityFact,
        {"domain": "character", "subject_id": " ", "field": "alive",
         "value": "true", "source_commit_id": "ch001", "evidence": "原文"},
    )
    assert not ok
    assert "不可为空" in msg


def test_bad_enum_rejected() -> None:
    ok, msg, _ = validate_model(
        ContinuityFact,
        {"domain": "not_a_domain", "subject_id": "林寻", "field": "alive",
         "value": "true", "source_commit_id": "ch001", "evidence": "原文"},
    )
    assert not ok


def test_knowledge_reader_must_be_wildcard() -> None:
    # reader 知识必须 audience_id="*"
    ok, msg, _ = validate_model(
        ContinuityKnowledge,
        {"subject_id": "林寻", "audience": "reader", "audience_id": "林寻",
         "level": "knows", "source_commit_id": "ch001"},
    )
    assert not ok
    assert "reader" in msg


def test_handoff_chapter_positive() -> None:
    ok, msg, _ = validate_model(
        ContinuityHandoff,
        {"chapter": 0, "summary": "s", "source_commit_id": "ch001"},
    )
    assert not ok
    assert "正整数" in msg


def test_committed_beat_requires_commit_id() -> None:
    # 状态机合法性：exec_status=committed 必须有 commit_id；反之亦然
    ok, msg, _ = validate_model(
        ForesightBeat,
        {"beat_id": "F-01-plant", "type": "plant", "exec_status": "committed"},
    )
    assert not ok
    assert "commit_id" in msg
    ok2, _, _ = validate_model(
        ForesightBeat,
        {"beat_id": "F-01-plant", "type": "plant", "exec_status": "planned",
         "commit_id": "ch001"},
    )
    assert not ok2  # 未 committed 却带 commit_id 也拒绝


def test_ledger_fact_uniqueness() -> None:
    # 账本唯一性：同 (domain, subject_id, field) 重复 → 拒绝
    base = {
        "schema_version": 1,
        "facts": [
            {"domain": "character", "subject_id": "林寻", "field": "alive",
             "value": "true", "source_commit_id": "ch001", "evidence": "e"},
            {"domain": "character", "subject_id": "林寻", "field": "alive",
             "value": "false", "source_commit_id": "ch002", "evidence": "e"},
        ],
    }
    ok, msg, _ = validate_model(ContinuityLedger, base)
    assert not ok


def test_timeline_event_self_loop_rejected() -> None:
    ok, msg, _ = validate_model(
        StoryEvent,
        {"event_id": "e1", "title": "觉醒", "connections": [
            {"to": "e1", "kind": "causes"}]},
    )
    assert not ok
    assert "自引用" in msg


def test_timeline_placement_dup_rejected() -> None:
    tl = {
        "events": [{"event_id": "e1", "title": "觉醒"}],
        "placements": [
            {"event_id": "e1", "chapter": 3, "mode": "flashback", "disclosure": "partial"},
            {"event_id": "e1", "chapter": 3, "mode": "scene", "disclosure": "full"},
        ],
    }
    ok, msg, _ = validate_model(Timeline, tl)
    assert not ok
    assert "placement 重复" in msg


def test_timeline_undefined_event_ref_rejected() -> None:
    tl = {
        "events": [{"event_id": "e1", "title": "觉醒"}],
        "placements": [
            {"event_id": "ghost", "chapter": 1, "mode": "scene", "disclosure": "full"},
        ],
    }
    ok, _, _ = validate_model(Timeline, tl)
    assert not ok


def test_scene_false_disclosure_rejected() -> None:
    # 本体场景不得用 false 披露
    ok, _, _ = validate_model(
        NarrativePlacement,
        {"event_id": "e1", "chapter": 1, "mode": "scene", "disclosure": "false"},
    )
    assert not ok


def test_validate_many_all_or_nothing() -> None:
    goods = [
        {"domain": "character", "subject_id": "a", "field": "alive",
         "value": "true", "source_commit_id": "c1", "evidence": "e"},
        {"domain": "character", "subject_id": "b", "field": "alive",
         "value": "false", "source_commit_id": "c1", "evidence": "e"},
    ]
    ok, msg, models = validate_many(ContinuityFact, goods)
    assert ok and len(models) == 2
    ok2, msg2, _ = validate_many(ContinuityFact, goods + [{"bad": 1}])
    assert not ok2