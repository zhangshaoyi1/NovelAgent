"""P2/P3 测试：进度推导唯一化 + 路线渲染视图（复盘根治 2026-09-07，纯离线）。

覆盖：
- next_chapter / ending_trigger / book_total 三个唯一答案函数（含边界）；
- plan_consistency.load_plan_total 委托行为零改动；
- protagonist_route.md.j2 含「生成视图」横幅（P3），渲染后
  check_route_ranges 的正则解析不受横幅影响。
"""

from __future__ import annotations

import json
from pathlib import Path

from agent.core.progress import book_total, ending_trigger, next_chapter
from agent.workflows.pipeline.plan_consistency import (
    check_route_ranges,
    load_plan_total,
)


# ============================================================
# P2：next_chapter（原 12 处 total_written+1 的唯一实现）
# ============================================================
def test_next_chapter_basic() -> None:
    assert next_chapter({"total_written": 12}) == 13
    assert next_chapter({"total_written": 0}) == 1


def test_next_chapter_edge_cases() -> None:
    assert next_chapter(None) == 1
    assert next_chapter({}) == 1
    assert next_chapter({"total_written": None}) == 1
    assert next_chapter({"total_written": "not-a-number"}) == 1
    assert next_chapter({"total_written": -5}) == 1


# ============================================================
# P2：ending_trigger（与历史公式逐值等价）
# ============================================================
def test_ending_trigger_matches_historical_formula() -> None:
    # 五灵破归档三组实证值：12 章书 / 18 章书 / 1200 章书（ratio=0.25）
    assert ending_trigger(12, 0.25) == 10
    assert ending_trigger(18, 0.25) == 14
    assert ending_trigger(1200, 0.25) == 901
    # 与旧公式逐值等价（抽样）
    for total in (8, 30, 100, 350, 1000):
        assert ending_trigger(total, 0.25) == int(total * (1 - 0.25)) + 1


# ============================================================
# P2：book_total（plan.json 唯一权威读取）
# ============================================================
def test_book_total_reads_plan(tmp_path: Path) -> None:
    (tmp_path / ".state").mkdir(parents=True)
    (tmp_path / ".state" / "plan.json").write_text(
        json.dumps({"total_chapters": 1200}), encoding="utf-8"
    )
    assert book_total(tmp_path) == 1200
    assert load_plan_total(tmp_path) == 1200, "load_plan_total 必须委托同一实现"


def test_book_total_missing_or_corrupt(tmp_path: Path) -> None:
    assert book_total(tmp_path) is None  # 无 plan.json
    (tmp_path / ".state").mkdir(parents=True)
    (tmp_path / ".state" / "plan.json").write_text("{broken", encoding="utf-8")
    assert book_total(tmp_path) is None
    (tmp_path / ".state" / "plan.json").write_text(
        json.dumps({"total_chapters": 0}), encoding="utf-8"
    )
    assert book_total(tmp_path) is None


# ============================================================
# P3：路线 md 是渲染视图（横幅 + 解析兼容）
# ============================================================
def test_route_template_has_generated_banner() -> None:
    from agent import templates as pkg_templates  # noqa: F401 - 确认包存在

    template_path = Path(__file__).resolve().parents[1] / "src" / "agent" / "templates" / "protagonist_route.md.j2"
    src = template_path.read_text(encoding="utf-8")
    assert "单一真源" in src and "plan.json" in src, "模板必须标注生成视图与真源"


def test_route_range_check_ignores_banner(tmp_path: Path) -> None:
    from jinja2 import Environment, DictLoader

    template_path = Path(__file__).resolve().parents[1] / "src" / "agent" / "templates" / "protagonist_route.md.j2"
    env = Environment(loader=DictLoader({"r": template_path.read_text(encoding="utf-8")}))
    md = env.get_template("r").render(
        root_node="起点",
        nodes=[
            {"id": "N01", "milestone": "M1", "chapter_range": "1-200",
             "main_branch": {"title": "t"}, "alt_branches": []},
            {"id": "N02", "milestone": "M2", "chapter_range": "201-400",
             "main_branch": {"title": "t"}, "alt_branches": []},
        ],
    )
    (tmp_path / "protagonist_route.md").write_text(md, encoding="utf-8")
    (tmp_path / ".state").mkdir()
    (tmp_path / ".state" / "plan.json").write_text(
        json.dumps({"total_chapters": 1200}), encoding="utf-8"
    )
    assert check_route_ranges(tmp_path) == [], "横幅不应影响范围解析；1-200/201-400 对 1200 章书均可达"

    (tmp_path / ".state" / "plan.json").write_text(
        json.dumps({"total_chapters": 150}), encoding="utf-8"
    )
    warnings = check_route_ranges(tmp_path)
    assert len(warnings) == 1 and "201-400" in warnings[0]
