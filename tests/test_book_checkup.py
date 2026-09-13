"""book_checkup（全书体检）离线测试

纯规则指标，无需 LLM。tmp_path 构造最小小说项目。
对应登记：项目文档/优化/20260913_灵荒炉火差评复盘.md T4。
"""

from __future__ import annotations

from pathlib import Path

from agent.core.quality.book_checkup import run_book_checkup


def _make_project(tmp_path: Path, chapters: dict[int, str]) -> Path:
    """构造最小项目：chapters/ + characters/ + foreshadows.md。"""
    (tmp_path / "chapters").mkdir(parents=True)
    for num, body in chapters.items():
        (tmp_path / "chapters" / f"ch{num:03d}.md").write_text(
            f"---\nchapter: {num}\npressure_stage: 铺垫\nroute_node: N01\n"
            f"word_count: {len(body)}\n---\n\n# 第 {num} 章\n\n{body}\n",
            encoding="utf-8",
        )
    (tmp_path / "characters").mkdir()
    (tmp_path / "characters" / "林凡.md").write_text(
        "---\nname: \"林凡\"\nrole: \"protagonist\"\n---\n\n# 林凡\n", encoding="utf-8"
    )
    (tmp_path / "characters" / "石莽.md").write_text(
        "---\nname: \"石莽\"\nrole: \"support\"\n---\n\n# 石莽\n", encoding="utf-8"
    )
    return tmp_path


def test_stage_streak_flags_long_setup_run(tmp_path: Path) -> None:
    body = "林凡刻符。他知道。他不急。\n" * 50
    _make_project(tmp_path, {i: body for i in range(1, 13)})
    report = run_book_checkup(tmp_path)
    assert report["success"]
    stage = next(m for m in report["metrics"] if m["metric"] == "stage_streak")
    assert stage["violations"], "12 章连续铺垫必须命中"
    assert stage["violations"][0]["length"] == 12
    assert report["passed"] is False


def test_short_stage_streak_passes(tmp_path: Path) -> None:
    body = "林凡刻符。" * 300
    _make_project(tmp_path, {1: body, 2: body, 3: body})
    report = run_book_checkup(tmp_path)
    stage = next(m for m in report["metrics"] if m["metric"] == "stage_streak")
    assert stage["violations"] == []


def test_foreshadow_overdue_and_unburied(tmp_path: Path) -> None:
    body = "林凡刻符。" * 300
    _make_project(tmp_path, {i: body for i in range(1, 31)})
    (tmp_path / "foreshadows.md").write_text(
        "| ID | 伏笔内容 | 埋设位置 | 预期回收点 | 状态 | 关联角色 |\n"
        "|---|---|---|---|---|---|\n"
        "| F-01 | 苍狼血脉 | S01/ch003 | S01/ch010 | 已埋 | 林凡 |\n"
        "| F-02 | 灵脉枯竭 | S01/ch010 | S02/ch500 | 未埋 | 沈长风 |\n",
        encoding="utf-8",
    )
    report = run_book_checkup(tmp_path, foreshadow_grace=5)
    fo = next(m for m in report["metrics"] if m["metric"] == "foreshadow_aging")
    assert [v["id"] for v in fo["overdue"]] == ["F-01"], "ch030 > ch010+宽限5 必须逾期"
    assert [v["id"] for v in fo["unburied"]] == ["F-02"], "ch030 ≥ ch010 仍未埋必须命中"


def test_character_stagnation_detects_tool_man(tmp_path: Path) -> None:
    shi_body = "林凡刻符。石莽问为什么。\n" * 200
    _make_project(tmp_path, {i: shi_body for i in range(1, 14)})
    report = run_book_checkup(tmp_path, char_streak_limit=10)
    cs = next(m for m in report["metrics"] if m["metric"] == "character_stagnation")
    names = [v["character"] for v in cs["violations"]]
    assert "石莽" in names, "连续 13 章出场的石莽必须命中"
    assert "林凡" not in names, "主角（role: protagonist）不在配角停滞指标范围"


def test_ending_hook_repetition_clustered(tmp_path: Path) -> None:
    body = "林凡刻符。" * 300 + "\n真正的风暴，才刚刚开始。\n"
    _make_project(tmp_path, {i: body for i in range(1, 8)})
    report = run_book_checkup(tmp_path)
    hooks = next(m for m in report["metrics"] if m["metric"] == "ending_hooks")
    assert hooks["clusters"], "7 个完全相同的章末必须聚成一簇"
    assert hooks["clusters"][0]["count"] == 7


def test_ending_hooks_distinct_endings_no_cluster(tmp_path: Path) -> None:
    bodies = {
        1: "林凡刻符。" * 300 + "\n他吹灭了灯。",
        2: "林凡刻符。" * 300 + "\n雨落进山谷。",
        3: "林凡刻符。" * 300 + "\n石莽打了个喷嚏。",
    }
    _make_project(tmp_path, bodies)
    report = run_book_checkup(tmp_path)
    hooks = next(m for m in report["metrics"] if m["metric"] == "ending_hooks")
    assert hooks["clusters"] == []


def test_realm_progression_reports_gap(tmp_path: Path) -> None:
    body = "林凡刻符。" * 300
    chapters = {i: body for i in range(1, 26)}
    chapters[2] = "林凡终于突破到栖气期。\n" + body
    _make_project(tmp_path, chapters)
    report = run_book_checkup(tmp_path)
    rp = next(m for m in report["metrics"] if m["metric"] == "realm_progression")
    assert rp["advance_count"] == 1
    assert rp["chapters_since_last_advance"] == 23
    assert rp["chapters_per_advance"] == 25.0


def test_word_count_undersized_flagged(tmp_path: Path) -> None:
    _make_project(
        tmp_path,
        {1: "林凡刻符。" * 300, 2: "太短了。", 3: "林凡刻符。" * 300},
    )
    report = run_book_checkup(tmp_path, min_chapter_chars=100)
    wc = next(m for m in report["metrics"] if m["metric"] == "word_count")
    assert [u["chapter"] for u in wc["undersized"]] == [2]


def test_missing_chapters_dir_fails_explicitly(tmp_path: Path) -> None:
    report = run_book_checkup(tmp_path)
    assert report["success"] is False
    assert report["error"]["code"] == "no_chapters"


def test_meta_error_degrades_not_passes(tmp_path: Path) -> None:
    (tmp_path / "chapters").mkdir(parents=True)
    (tmp_path / "chapters" / "ch001.md").write_text("没有 frontmatter 的正文", encoding="utf-8")
    report = run_book_checkup(tmp_path)
    assert report["success"] is True
    assert report["degraded"], "缺 frontmatter 必须显性降级"
    assert report["passed"] is False, "降级不能被解读为通过"
