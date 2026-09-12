"""书级台账测试（2026-09-12 书级质检）：主线契约 / 登场连续性 / 质量基线。"""

import json

import pytest

from agent.core.quality.book_ledger import (
    baseline_drift_text,
    check_debut_echo,
    load_theme_contract,
    record_debuts,
    record_quality,
    theme_contract_text,
)


@pytest.fixture
def proj(tmp_path):
    (tmp_path / "characters").mkdir()
    (tmp_path / "characters" / "林凡.md").write_text("# 林凡\n", encoding="utf-8")
    (tmp_path / ".state").mkdir()
    (tmp_path / ".state" / "plan.json").write_text(
        json.dumps(
            {"title": "测试书", "brief": "凡人以量产傀儡颠覆宗门等级", "genre": "xiuxian"},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return tmp_path


def test_contract_seeded_from_plan(proj):
    contract = load_theme_contract(proj)
    assert contract and "量产傀儡" in contract["promise"]
    text = theme_contract_text(proj, 7)
    assert "第 7 章" in text and "量产傀儡" in text


def test_contract_missing_plan_returns_empty(proj):
    (proj / ".state" / "plan.json").unlink()
    assert load_theme_contract(proj) is None
    assert theme_contract_text(proj, 1) == ""


def test_debut_echo_flagged_for_unregistered_entity(proj):
    issues = check_debut_echo(proj, "残魂的声音再次响起。", 2)
    # 「残魂」不在 characters/ 登记表内 → 窄口径不误报未知实体；
    # 但已登记实体「林凡」以再次口吻出现且无登场记录 → 必须命中
    assert issues == []  # 未出现登记实体名


def test_debut_echo_flagged_for_known_entity(proj):
    text = "林凡的出现再次打破了僵局，众人骇然。"
    issues = check_debut_echo(proj, text, 5)
    assert len(issues) == 1
    assert issues[0]["rule_id"] == "debut_continuity"
    assert "林凡" in issues[0]["description"]


def test_record_debuts_clears_future_echo(proj):
    record_debuts(proj, "林凡首次现身广场。", 1)
    assert check_debut_echo(proj, "林凡再次现身广场。", 3) == []


def test_baseline_drift_after_poor_window(proj):
    for ch in range(1, 11):
        record_quality(proj, ch, passed=(ch == 1), revisions=3)
    text = baseline_drift_text(proj, window=10, min_pass_rate=0.5)
    assert "通过率" in text and "10%" in text


def test_baseline_healthy_returns_empty(proj):
    for ch in range(1, 11):
        record_quality(proj, ch, passed=True, revisions=0)
    assert baseline_drift_text(proj, window=10) == ""


def test_baseline_window_not_full(proj):
    for ch in range(1, 6):
        record_quality(proj, ch, passed=False, revisions=2)
    assert baseline_drift_text(proj, window=10) == ""


def test_writes_avoid_state_json(proj):
    record_quality(proj, 1, passed=True, revisions=0)
    record_debuts(proj, "林凡登场。", 1)
    assert (proj / ".state" / "state.json").exists() is False
    assert (proj / ".state" / "memory" / "quality_baseline.json").exists()
    assert (proj / ".state" / "memory" / "debut_registry.json").exists()
