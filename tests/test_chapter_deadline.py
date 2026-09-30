"""T3 章级期限 SSOT（长线一致性二期，20260930 登记）单测。"""

from agent.core.story.chapter_contract import (
    DEADLINES_SECTION,
    chapter_deadline,
    deadlines_of_window,
)

_SUBLINE = """## 章节钩子设计

第7章：主角反查账册
第8章：对方上门

## 章节强度档位

第7章：推进
第8章：垫片

## 章节期限与时间线

第7章：三天后执事会验数（剩余3天）
第8章：执事会验数当日（剩余2天）

## 情节点序列

第7章：夜探；记账
"""


def test_chapter_deadline_extracts_current():
    assert "执事会验数" in chapter_deadline(_SUBLINE, 7)
    assert chapter_deadline(_SUBLINE, 8) != ""


def test_chapter_deadline_missing_section():
    assert chapter_deadline("## 章节钩子设计\n\n第7章：x", 7) == ""


def test_chapter_deadline_out_of_range():
    assert chapter_deadline(_SUBLINE, 99) == ""


def test_deadlines_of_window():
    rows = deadlines_of_window(_SUBLINE, (7, 8))
    assert [n for n, _ in rows] == [7, 8]
    assert all("章" not in t[:2] for _, t in rows)


def test_design_brief_injects_deadline(tmp_path):
    import shutil

    from agent.core.story.design_brief import build_design_brief

    sub_dir = tmp_path / "sublines" / "S01_x"
    sub_dir.mkdir(parents=True)
    (sub_dir / "subline.md").write_text(_SUBLINE, encoding="utf-8")
    brief = build_design_brief(tmp_path, chapter_num=7)
    w = brief.render_for_writer()
    assert "期限与时间线" in w and "执事会验数" in w
    assert "规划端唯一授权" in w
    j = brief.render_for_judge()
    assert "期限与时间线参照系" in j


def test_deadlines_section_constant_matches_template():
    """判据词表与模板语言锚同源（纪律 #19：改名即双向破裂）。"""
    from pathlib import Path

    tpl = (
        Path(__file__).resolve().parents[1]
        / "src/agent/templates/subline.md.j2"
    )
    assert DEADLINES_SECTION in tpl.read_text(encoding="utf-8")
