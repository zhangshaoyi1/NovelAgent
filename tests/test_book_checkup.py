"""book_checkup（全书体检）离线测试

纯规则指标，无需 LLM。tmp_path 构造最小小说项目。
对应登记：项目文档/优化/20260913_灵荒炉火差评复盘.md T4。
"""

from __future__ import annotations

from pathlib import Path

from agent.core.quality.book_checkup import run_book_checkup, write_time_entity_check


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


def test_ending_cliche_catches_variant_phrases(tmp_path: Path) -> None:
    # 相似度聚类抓不到的措辞变体，套话清单必须兜住（点评实证：三本 101 个章尾命中）
    bodies = {
        1: "林凡刻符。" * 300 + "\n真正的风暴，才刚刚开始。",
        2: "林凡刻符。" * 300 + "\n距离下一个十五，还有二十九天。",
        3: "林凡刻符。" * 300 + "\n他握紧了拳头，风暴正在酝酿。",
    }
    _make_project(tmp_path, bodies)
    report = run_book_checkup(tmp_path)
    cl = next(m for m in report["metrics"] if m["metric"] == "ending_cliche")
    assert cl["hit_count"] == 3, "三个变体章尾必须全部命中"
    issues = [i for i in report["issues"] if i["metric"] == "ending_cliche"]
    assert issues, "章尾套话必须进入问题清单"


def test_ending_cliche_clean_endings_pass(tmp_path: Path) -> None:
    bodies = {
        1: "林凡刻符。" * 300 + "\n他吹灭了灯。",
        2: "林凡刻符。" * 300 + "\n老者约他三日后一叙。",
    }
    _make_project(tmp_path, bodies)
    report = run_book_checkup(tmp_path)
    cl = next(m for m in report["metrics"] if m["metric"] == "ending_cliche")
    assert cl["hit_count"] == 0


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


# ---------------------------------------------------------------- T1 子集：实体漂移
def _make_canon(tmp_path: Path) -> None:
    (tmp_path / "world.md").write_text(
        "---\nfrozen_fields: []\n---\n\n# 世界观\n\n天剑宗是正道魁首，万魔殿为暗敌。\n",
        encoding="utf-8",
    )


def test_entity_drift_flags_out_of_canon_org(tmp_path: Path) -> None:
    # 回归样本：灵荒炉火 ch031/032 设定外宗门「灵渊宗」
    _make_project(tmp_path, {i: "灵渊宗的弟子又出现了。" * 100 for i in range(1, 4)})
    _make_canon(tmp_path)
    report = run_book_checkup(tmp_path)
    ed = next(m for m in report["metrics"] if m["metric"] == "entity_drift")
    entities = [u["entity"] for u in ed["unknown"]]
    assert "灵渊宗" in entities, "设定外宗门必须命中"


def test_entity_drift_canon_org_not_flagged(tmp_path: Path) -> None:
    _make_project(tmp_path, {i: "天剑宗的钟声响起，万魔殿在暗处窥伺。" * 100 for i in range(1, 4)})
    _make_canon(tmp_path)
    report = run_book_checkup(tmp_path)
    ed = next(m for m in report["metrics"] if m["metric"] == "entity_drift")
    assert ed["unknown"] == [], "正典实体不得误报"


def test_entity_drift_noise_filtered(tmp_path: Path) -> None:
    # 动宾污染（今夜入宗）/低频切句噪声/后缀字重叠（藏经阁偏殿）均不得命中
    bodies = {
        1: "众人今夜入宗。藏经阁偏殿着火了。一份卷宗摆在案上。" * 100,
        2: "他们今夜入宗。藏经阁偏殿烧毁。简和卷宗散落一地。" * 100,
        3: "约好今夜入宗。偏殿在烧。卷宗没有了。" * 100,
    }
    _make_project(tmp_path, bodies)
    _make_canon(tmp_path)
    report = run_book_checkup(tmp_path)
    ed = next(m for m in report["metrics"] if m["metric"] == "entity_drift")
    assert ed["unknown"] == [], f"噪声误报：{ed['unknown']}"


def test_entity_drift_low_frequency_below_threshold(tmp_path: Path) -> None:
    _make_project(tmp_path, {1: "灵渊宗出现了一次。", 2: "天剑宗日常。", 3: "天剑宗日常。"})
    _make_canon(tmp_path)
    report = run_book_checkup(tmp_path)
    ed = next(m for m in report["metrics"] if m["metric"] == "entity_drift")
    assert ed["unknown"] == [], "低于 min_count 的候选视为噪声"


def test_entity_drift_verb_contamination_suppressed_by_tail(tmp_path: Path) -> None:
    # "告诉万魔殿/冲向万魔殿"这类动宾污染由尾式抑制（万魔殿在正典），
    # 这是 2026-09-13 五灵/无灵实测调参的关键回归
    _make_project(
        tmp_path,
        {i: "他告诉万魔殿，又冲向万魔殿，最后归顺万魔殿。" * 100 for i in range(1, 4)},
    )
    _make_canon(tmp_path)
    report = run_book_checkup(tmp_path)
    ed = next(m for m in report["metrics"] if m["metric"] == "entity_drift")
    assert ed["unknown"] == [], f"动宾污染误报：{ed['unknown']}"


def _make_character(tmp_path: Path, name: str) -> None:
    (tmp_path / "characters" / f"{name}.md").write_text(
        f"---\nname: \"{name}\"\nrole: \"support\"\n---\n\n# {name}\n", encoding="utf-8"
    )


def test_rename_drift_handoff_flagged(tmp_path: Path) -> None:
    # 回归样本：灵荒炉火 沈长风(ch1-30)→沈清舟(ch31起) 无交代改名
    _make_project(tmp_path, {i: "沈长风在授业。" * 100 for i in range(1, 6)})
    _make_character(tmp_path, "沈长风")
    # ch5 末注册名绝迹，ch6 起新名接棒
    (tmp_path / "chapters" / "ch005.md").write_text(
        "---\nchapter: 5\npressure_stage: 铺垫\n---\n\n" + "沈长风在授业。" * 100 + "结尾。",
        encoding="utf-8",
    )
    for i in (6, 7, 8):
        (tmp_path / "chapters" / f"ch00{i}.md").write_text(
            f"---\nchapter: {i}\npressure_stage: 铺垫\n---\n\n" + "沈清舟没有说话。" * 100,
            encoding="utf-8",
        )
    report = run_book_checkup(tmp_path)
    rd = next(m for m in report["metrics"] if m["metric"] == "rename_drift")
    assert any(s["registered"] == "沈长风" and s["alias"] == "沈清舟" for s in rd["suspects"])


def test_rename_drift_appellation_not_flagged(tmp_path: Path) -> None:
    # "沈师父/沈执事"是称谓不是改名
    _make_project(tmp_path, {i: "沈长风在授业。沈师父点头。" * 100 for i in range(1, 6)})
    _make_character(tmp_path, "沈长风")
    for i in (6, 7, 8):
        (tmp_path / "chapters" / f"ch00{i}.md").write_text(
            f"---\nchapter: {i}\npressure_stage: 铺垫\n---\n\n" + "沈执事来了。" * 100,
            encoding="utf-8",
        )
    report = run_book_checkup(tmp_path)
    rd = next(m for m in report["metrics"] if m["metric"] == "rename_drift")
    assert rd["suspects"] == []


def test_rename_drift_alias_in_canon_still_flagged(tmp_path: Path) -> None:
    # 灵荒炉火实证：world.md 已被漂移污染（沈清舟写入正典），不得据此抑制
    _make_project(tmp_path, {i: "沈长风在授业。" * 100 for i in range(1, 6)})
    _make_character(tmp_path, "沈长风")
    (tmp_path / "world.md").write_text(
        "---\nfrozen_fields: []\n---\n\n# 世界观\n\n沈清舟为执事堂七号执事。\n",
        encoding="utf-8",
    )
    for i in (6, 7, 8):
        (tmp_path / "chapters" / f"ch00{i}.md").write_text(
            f"---\nchapter: {i}\npressure_stage: 铺垫\n---\n\n" + "沈清舟没有说话。" * 100,
            encoding="utf-8",
        )
    report = run_book_checkup(tmp_path)
    rd = next(m for m in report["metrics"] if m["metric"] == "rename_drift")
    assert any(s["alias"] == "沈清舟" and s["alias_in_canon"] for s in rd["suspects"])


def test_speaker_registry_flags_recurring_unregistered(tmp_path: Path) -> None:
    # 回归样本：周长老/王执事 recurring 却从未注册
    _make_project(tmp_path, {i: "「来了。」周长老说道。王执事喝道：\"站住！\"" * 60 for i in range(1, 5)})
    report = run_book_checkup(tmp_path)
    sr = next(m for m in report["metrics"] if m["metric"] == "speaker_registry")
    names = {u["speaker"] for u in sr["unregistered"]}
    assert {"周长老", "王执事"} <= names


def test_speaker_registry_registered_passes(tmp_path: Path) -> None:
    _make_project(tmp_path, {i: "「来了。」石莽说道。" * 100 for i in range(1, 4)})
    report = run_book_checkup(tmp_path)
    sr = next(m for m in report["metrics"] if m["metric"] == "speaker_registry")
    assert sr["unregistered"] == []


# ---------------------------------------------------------------- T1 写时化：write_time_entity_check
def test_write_time_entity_check_blocks_new_org(tmp_path: Path) -> None:
    # 回归场景：灵荒炉火 ch031「灵渊宗」首现 → 出章门禁必须拦下
    _make_project(tmp_path, {i: "天剑宗日常。" * 200 for i in range(1, 4)})
    _make_canon(tmp_path)
    result = write_time_entity_check(tmp_path, 4, "灵渊宗的弟子围了上来。" * 50)
    assert result["blocking"], "设定外组织名首现必须 blocking"
    assert any("灵渊宗" in d for d in result["blocking"])


def test_write_time_entity_check_known_org_passes(tmp_path: Path) -> None:
    _make_project(tmp_path, {i: "天剑宗日常。" * 200 for i in range(1, 4)})
    _make_canon(tmp_path)
    result = write_time_entity_check(tmp_path, 4, "天剑宗的钟声响起。" * 50)
    assert result["blocking"] == []


def test_write_time_entity_check_renames_block(tmp_path: Path) -> None:
    # 接棒发生时：注册名绝迹 + 本章起同姓新名 → blocking
    _make_project(tmp_path, {i: "沈长风在授业。" * 100 for i in range(1, 6)})
    _make_character(tmp_path, "沈长风")
    result = write_time_entity_check(tmp_path, 6, "沈清舟没有说话。" * 100)
    assert any("改名" in d or "接棒" in d for d in result["blocking"]), result


def test_write_time_entity_check_speaker_warn_only(tmp_path: Path) -> None:
    # 配角漏登记是 warning 不是 blocking（配角可合法暂缓注册）
    _make_project(tmp_path, {i: "天剑宗日常。" * 200 for i in range(1, 4)})
    _make_canon(tmp_path)
    result = write_time_entity_check(tmp_path, 4, "「来了。」周长老说道。" * 50)
    assert result["blocking"] == []
    assert any("周长老" in d for d in result["warnings"])
