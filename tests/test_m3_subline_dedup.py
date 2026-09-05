"""M3 支线 ID 归一化查重（2026-09-05 修复）

LLM 两次生成的支线名常有细微出入（「极道武夫推演」vs「极道武夫推演线」），
此前按名字直接建目录会为同一支线留下重复目录，污染 subline_share 与
主线推进门禁。修复后：先按归一化名匹配已有目录，命中则原地更新。
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from agent.workflows.planning.m3_outline import M3OutlineWorkflow


@pytest.fixture
def workflow(tmp_path: Path) -> M3OutlineWorkflow:
    return M3OutlineWorkflow(
        project_dir=tmp_path,
        llm_client=MagicMock(),
    )


SUBLINE = {
    "subline_name": "极道武夫推演线",
    "goal": "推演凡武至极道巅峰",
    "characters": "陆渊",
    "conflicts": "- 矿渊首杀",
    "constraints": "无灵脉",
    "mainline_relation": "主线核心驱动力",
    "pressure_curve": {"setup": "1-150", "conflict": "151-500", "climax": "501-900", "relief": "901-1200"},
}


class TestNormalizeSublineName:
    def test_strips_trailing_xian(self):
        assert (
            M3OutlineWorkflow._normalize_subline_name("极道武夫推演线")
            == M3OutlineWorkflow._normalize_subline_name("极道武夫推演")
        )

    def test_strips_whitespace(self):
        assert (
            M3OutlineWorkflow._normalize_subline_name(" 凡骨殿 势力建设 ")
            == "凡骨殿势力建设"
        )

    def test_keeps_xian_inside_name(self):
        """只剥**末尾**的「线」，名字中间的「线」不受影响"""
        assert M3OutlineWorkflow._normalize_subline_name("暗线交锋") == "暗线交锋"


class TestMatchExistingSubline:
    def test_matches_existing_dir_ignoring_xian_suffix(
        self, workflow: M3OutlineWorkflow, tmp_path: Path
    ):
        subline_dir = tmp_path / "sublines"
        (subline_dir / "S01_极道武夫推演").mkdir(parents=True)
        assert (
            workflow._match_existing_subline_id(subline_dir, "极道武夫推演线", set())
            == "S01_极道武夫推演"
        )

    def test_no_match_returns_none(self, workflow: M3OutlineWorkflow, tmp_path: Path):
        subline_dir = tmp_path / "sublines"
        (subline_dir / "S01_极道武夫推演").mkdir(parents=True)
        assert (
            workflow._match_existing_subline_id(subline_dir, "凡骨殿势力建设线", set())
            is None
        )

    def test_skips_used_ids(self, workflow: M3OutlineWorkflow, tmp_path: Path):
        """同一轮已分配的目录不再命中（避免两条 LLM 支线挤进同一目录）"""
        subline_dir = tmp_path / "sublines"
        (subline_dir / "S01_极道武夫推演").mkdir(parents=True)
        assert (
            workflow._match_existing_subline_id(
                subline_dir, "极道武夫推演线", {"S01_极道武夫推演"}
            )
            is None
        )


class TestRenderAndSaveDedup:
    def test_rerun_reuses_existing_dir(
        self, workflow: M3OutlineWorkflow, tmp_path: Path
    ):
        """重跑大纲：第二次支线名多了「线」字，仍应复用旧目录而非新建"""
        old_dir = tmp_path / "sublines" / "S01_极道武夫推演"
        old_dir.mkdir(parents=True)
        (old_dir / "subline.md").write_text("旧内容", encoding="utf-8")

        workflow._render_and_save_sublines([SUBLINE])

        dirs = [d.name for d in (tmp_path / "sublines").iterdir() if d.is_dir()]
        assert dirs == ["S01_极道武夫推演"]
        assert "旧内容" not in (old_dir / "subline.md").read_text(encoding="utf-8")

    def test_fresh_run_creates_sequential_ids(
        self, workflow: M3OutlineWorkflow, tmp_path: Path
    ):
        workflow._render_and_save_sublines([SUBLINE, {**SUBLINE, "subline_name": "凡骨殿势力建设线"}])
        dirs = sorted(d.name for d in (tmp_path / "sublines").iterdir() if d.is_dir())
        assert dirs == ["S01_极道武夫推演线", "S02_凡骨殿势力建设线"]
