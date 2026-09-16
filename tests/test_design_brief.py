"""设计产出供给单源测试（2026-09-16）。

命题（作者 2026-09-16）：
> 「除了性格之外，别的都不是一成不变的，设计好的内容全部都要传递给写手
> 评委最终落盘，不要只设计不通知，这样设计没有任何意义。」

本文件钉住 ``core/story/design_brief`` 的三端渲染能力与「只增不改」的边界。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.core.story.design_brief import (
    DESIGN_EXEMPTION,
    build_design_brief,
    render_quality_rubric,
)


# ============================================================
# fixture：一个最小但结构完整的项目
# ============================================================
_WORLD_MD = """# 灵荒薪传

## 故事简介

林凡穿越到灵荒大陆，以生产力革命冲击修仙界的资源掠夺模式。

## 修炼境界体系

杂役 → 引灵 → 栖气 → 淳真 → 开玄 → 铸府 → 凝曜 → 归一

## 金手指登记

五行吞噬诀：吞噬灵力转化为催化能源，用于流水线生产。
"""

_PLAN = {
    "quality_targets": {
        "character_stability_high": 0,
        "setting_consistency_high": 0,
        "coherence": 85.0,
        "readability": 80.0,
    },
    "route": {
        "nodes": [
            {
                "id": "N01",
                "chapter_range": "1-150",
                "milestone": "杂役觉醒",
                "main_branch": {
                    "title": "五行吞噬诀解封",
                    "result": "灵田产量翻倍",
                    "growth": "境界：杂役→引灵→栖气；心性：隐忍→果敢；能力：五行吞噬诀Lv.1",
                },
            },
            {
                "id": "N02",
                "chapter_range": "151-300",
                "milestone": "外门立足",
                "main_branch": {
                    "title": "工坊品牌建立",
                    "result": "达成规模化生产",
                    "growth": "境界：开玄初期→开玄后期；心性：务实→自信",
                },
            },
        ]
    },
}

_SUBLINE_MD = """---
subline_id: "S01_工坊崛起"
---

# 支线设定 · 工坊崛起

## 支线目标

从灵植杂役晋升外门弟子，建立第一条人工作坊流水线。

## 章节钩子设计

铺垫章：章首=日常铺垫，章尾=危机触发；冲突章：章尾=两难抉择

## 情节点序列

首次触发五行吞噬诀；打造第一具粗劣傀儡；建立人工作坊。
"""

_CHARACTER_MD = """---
name: "林凡"
role: "protagonist"
realm: "杂役→归一期"
---

# 角色档案 · 林凡

## 基础

- 身份：穿越者，天灵宗灵植杂役
- 境界：杂役→归一期

## 内核

- **核心动机**：改变修仙界资源掠夺模式
- **弧光**：
  - 起始状态：隐忍求存的底层杂役
  - 终结状态：退居幕后的文明守护者

## 关系

['- 与沈长风：合作→导师', '- 与楚寒烟：从排斥到认可']

## 语言指纹

- **口头禅**：这不是命，是算式。
"""


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    """最小可用项目：world/plan/state/characters/sublines/chapters 齐备。"""
    (tmp_path / "world.md").write_text(_WORLD_MD, encoding="utf-8")
    (tmp_path / "characters").mkdir()
    (tmp_path / "characters" / "林凡.md").write_text(_CHARACTER_MD, encoding="utf-8")
    (tmp_path / "sublines" / "S01_工坊崛起").mkdir(parents=True)
    (tmp_path / "sublines" / "S01_工坊崛起" / "subline.md").write_text(
        _SUBLINE_MD, encoding="utf-8"
    )
    (tmp_path / ".state").mkdir()
    (tmp_path / ".state" / "plan.json").write_text(
        json.dumps(_PLAN, ensure_ascii=False), encoding="utf-8"
    )
    (tmp_path / "chapters").mkdir()
    (tmp_path / "chapters" / "ch027.md").write_text("第二十七章 试炼\n\n林凡睁开眼。", encoding="utf-8")
    return tmp_path


# ============================================================
# 装配
# ============================================================
class TestBuild:
    def test_chapter_num_derived_from_chapter_files(self, project: Path) -> None:
        """章号从 ``chapters/chNNN.md`` 派生（不读受监管的 state.json）。"""
        brief = build_design_brief(project)
        assert brief.chapter_num == 27
        assert brief.window == (23, 27), "窗口 = 末 eval_window 章"

    def test_does_not_read_regulated_state(self, project: Path) -> None:
        """★ state.json 是受监管状态（业主 = 状态机），本模块**不得**依赖它。

        写一个明显不同的 current_chapter，若被采信说明越权读了受监管状态。
        """
        (project / ".state" / "state.json").write_text(
            json.dumps({"progress": {"current_chapter": 999, "current_subline": "S99_假的"}}),
            encoding="utf-8",
        )
        brief = build_design_brief(project)
        assert brief.chapter_num == 27, "不得从 state.json 取章号（红线 test_state_ownership）"

    def test_not_empty_for_complete_project(self, project: Path) -> None:
        brief = build_design_brief(project)
        assert not brief.empty

    def test_eval_window_is_configurable(self, project: Path) -> None:
        brief = build_design_brief(project, eval_window=3)
        assert brief.window == (25, 27)

    def test_missing_sources_degrade_to_empty_without_raising(self, tmp_path: Path) -> None:
        """空项目：所有真源缺失 → 各块为空串，绝不抛异常（只增不改）。"""
        brief = build_design_brief(tmp_path)
        assert brief.empty
        assert brief.render_for_judge() == ""
        assert brief.render_for_writer() == ""
        assert brief.render_for_persist() == ""

    def test_missing_plan_is_reported_not_silent(self, tmp_path: Path, caplog) -> None:
        """★ plan.json 缺失必须显性上报：轨迹/判据/落盘期望会静默变空。

        「只设计不通知」的反面同样有害——「没设计也不通知」会让调用方
        误以为三端可达，实则评委只拿到半份真源（世界观+角色，无轨迹/判据）。
        """
        import logging

        with caplog.at_level(logging.WARNING, logger="agent.degrade.design_brief.plan"):
            build_design_brief(tmp_path)
        assert any(
            "plan.json 不存在" in r.getMessage() for r in caplog.records
        ), "缺 plan.json 必须发 design_brief.plan 降级告警，不得静默"

    def test_is_read_only(self, project: Path) -> None:
        """装配只读真源，不得产生任何文件（台账/档案都不许被写）。"""
        before = {p for p in project.rglob("*")}
        build_design_brief(project)
        assert {p for p in project.rglob("*")} == before


# ============================================================
# 判据（阈值渲染）
# ============================================================
class TestRubric:
    def test_renders_thresholds_with_dimension_semantics(self) -> None:
        text = render_quality_rubric(
            {"character_stability_high": 0, "coherence": 85.0, "readability": 80.0}
        )
        assert "人设稳定" in text
        assert "85" in text and "80" in text
        assert "硬指标" in text, "required 维必须标出，写手才知道哪条会触发回滚"

    def test_empty_targets_returns_empty(self) -> None:
        assert render_quality_rubric({}) == ""
        assert render_quality_rubric(None) == ""

    def test_brief_carries_rubric(self, project: Path) -> None:
        brief = build_design_brief(project)
        assert "连贯性" in brief.rubric
        assert "85" in brief.rubric


# ============================================================
# 弧线轨迹
# ============================================================
class TestRouteTrack:
    def test_marks_window_node(self, project: Path) -> None:
        brief = build_design_brief(project)
        assert "N01" in brief.route_track
        assert "本窗口在此档" in brief.route_track
        assert "N02" in brief.route_track, "后续档位必须一并给出（否则临到节点才转换）"

    def test_carries_growth_intent(self, project: Path) -> None:
        brief = build_design_brief(project)
        assert "隐忍→果敢" in brief.route_track, "弧光意图是本模块存在的理由"

    def test_window_shifts_marking(self, project: Path) -> None:
        brief = build_design_brief(project, 200, eval_window=5)
        assert "N02" in brief.route_track
        assert "本窗口在此档" in brief.route_track


# ============================================================
# 章级设计意图
# ============================================================
class TestChapterIntent:
    def test_carries_goal_hooks_and_plot_points(self, project: Path) -> None:
        brief = build_design_brief(project)
        assert "建立第一条人工作坊流水线" in brief.chapter_intent  # 支线目标
        assert "危机触发" in brief.chapter_intent                  # 钩子设计
        assert "五行吞噬诀" in brief.chapter_intent                # 情节点序列


# ============================================================
# 设定 / 角色真源
# ============================================================
class TestFacts:
    def test_setting_facts_include_frozen_sections(self, project: Path) -> None:
        brief = build_design_brief(project)
        assert "境界体系" in brief.setting_facts
        assert "金手指" in brief.setting_facts

    def test_character_facts_include_design_intent(self, project: Path) -> None:
        """★ 本模块的核心修复：弧光/关系演进此前**从未**到达评委。"""
        brief = build_design_brief(project)
        assert "弧光" in brief.character_facts
        assert "隐忍求存的底层杂役" in brief.character_facts
        assert "从排斥到认可" in brief.character_facts, "关系演变方向也是设计意图"

    def test_blank_line_bloat_is_squeezed(self, tmp_path: Path) -> None:
        """真源里的成片空行必须被压缩。

        否则空行白吃固定字符预算，把真内容挤到截断线之外
        （"注入了但被截断"＝没注入）。
        """
        (tmp_path / "world.md").write_text(
            "## 修炼境界体系\n\n\n\n\n"
            + "\n\n\n".join(f"{i}. 境界{i}" for i in range(1, 8)),
            encoding="utf-8",
        )
        brief = build_design_brief(tmp_path, 1)
        assert "境界7" in brief.setting_facts, "压缩后第 7 条应仍在预算内"
        assert "\n\n\n" not in brief.setting_facts, "不得残留 3+ 连续换行"


# ============================================================
# 三端渲染
# ============================================================
class TestRenderers:
    def test_writer_block_has_three_parts(self, project: Path) -> None:
        text = build_design_brief(project).render_for_writer()
        assert "【本章设计意图" in text
        assert "【角色弧线轨迹" in text
        assert "【达标判据" in text

    def test_judge_block_carries_design_exemption(self, project: Path) -> None:
        """评委必须拿到「设计内转变 ≠ 崩坏」这条判定前提——本次事故的直接解药。"""
        text = build_design_brief(project).render_for_judge()
        assert DESIGN_EXEMPTION in text
        assert "设计内变化" in text
        assert "弧线轨迹" in text
        assert "达标判据" in text

    def test_persist_block_tells_what_to_ledger(self, project: Path) -> None:
        text = build_design_brief(project).render_for_persist()
        assert "落盘为新状态" in text
        assert "心性" in text and "境界" in text

    def test_blocks_are_bounded(self, project: Path) -> None:
        """每块都有字符预算：不得把小节无限注入而挤掉正文。"""
        brief = build_design_brief(project)
        assert len(brief.setting_facts) <= 2800 + 32
        assert len(brief.character_facts) <= 2000 + 32
        assert len(brief.route_track) <= 900 + 32


# ============================================================
# 评委端行为验证（事故真缺口的直接回归）
# ============================================================
class TestJudgeInjection:
    @pytest.mark.parametrize(
        "dimension",
        [
            "character_stability_high",   # 一致性维（旧实现唯一给真源的三个）
            "setting_consistency_high",
            "logic_holes",
            "coherence",                  # 评分维（旧实现**零**真源）
            "readability",
            "pacing_abnormal",            # 计算维（旧实现零真源）
        ],
    )
    def test_every_dimension_sees_the_design_track(
        self, project: Path, dimension: str
    ) -> None:
        """**所有**维度都必须看到设计轨与判定前提。

        旧实现只对 ``_CANON_DIMS`` 三个维注入，且内容仅「状态/时间线/基础」——
        写手拿得到「心性：隐忍→果敢」、评委拿不到 ⇒ 判据互斥 ⇒ 回退不收敛。
        """
        from agent.core.quality.scoring.reader_appeal import ReaderAppealScorer

        scorer = ReaderAppealScorer(llm_client=None)
        text = scorer._gather_for_eval(dimension, str(project))
        assert "隐忍→果敢" in text, f"{dimension} 未拿到弧线轨迹"
        assert "设计内变化" in text, f"{dimension} 未拿到「设计内转变 ≠ 崩坏」判定前提"
        assert "达标判据" in text, f"{dimension} 未拿到判据（写手/评委尺子必须同一把）"

    def test_design_brief_is_assembled_once_per_project(self, project: Path) -> None:
        """同一轮体检按维度多次取值 → 装配只做一次（否则重复读盘 5 遍）。"""
        from agent.core.quality.scoring import reader_appeal as ra

        calls = {"n": 0}
        original = ra.build_design_brief

        def _counting(*args, **kwargs):
            calls["n"] += 1
            return original(*args, **kwargs)

        ra.build_design_brief = _counting  # type: ignore[assignment]
        try:
            scorer = ra.ReaderAppealScorer(llm_client=None)
            for dim in ("coherence", "readability", "logic_holes"):
                scorer._gather_for_eval(dim, str(project))
        finally:
            ra.build_design_brief = original  # type: ignore[assignment]
        assert calls["n"] == 1, f"设计产出装配了 {calls['n']} 次，应缓存在实例上"
