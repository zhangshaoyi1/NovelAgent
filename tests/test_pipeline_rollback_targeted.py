"""Pipeline 回溯重写「针对性」回归测试（G1）

验证：当 Evaluator 终审不达标触发回溯时，Pipeline 的 rewriter 会把失败维度
编译成针对性提示，并通过 ``Writer.run(rewrite_hint=...)`` 传给 Writer，而非盲目重写。

纯离线：注入 fake evaluator / writer / editor / planner / memory，不触碰真实 LLM。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from agent.agents.evaluator import DimensionResult, NovelHealthReport, RepairPlan
from agent.core.story.setting_manager import SettingManager
from agent.core.engine.state_machine import State, StateMachine
from agent.workflows.agentic_pipeline import AgenticPipelineWorkflow, build_rewrite_hint


def _fail_report() -> NovelHealthReport:
    return NovelHealthReport(
        overall_pass=False,
        dimensions=[
            DimensionResult("character_stability_high", "人设稳定", 0.0, 0.0, "<=", True),
            DimensionResult("coherence", "连贯性", 60.0, 80.0, ">=", False),
            DimensionResult("readability", "追读力", 50.0, 75.0, ">=", False),
            DimensionResult("foreshadow_recycle_rate", "伏笔闭环", 0.90, 0.90, ">=", False),
            DimensionResult("pacing_abnormal", "节奏异常", 0.0, 0.03, "<=", False),
            DimensionResult("setting_consistency_high", "设定一致", 0.0, 0.0, "<=", True),
            DimensionResult("logic_holes", "逻辑漏洞", 0.0, 0.0, "<=", True),
        ],
        repair=RepairPlan(
            target_chapter=8,
            chapters_to_rewrite=[8, 9, 10, 11, 12],
            reason="硬指标不达标，回溯最近 5 章",
            rolled_back=True,
        ),
    )


class _FakeEvaluator:
    def __init__(self) -> None:
        self.last_failed_report: NovelHealthReport | None = None

    def evaluate_with_repair(self, rewriter) -> NovelHealthReport:
        report = _fail_report()
        self.last_failed_report = report
        # 模拟真实回溯后，调用 Pipeline 传入的 rewriter 重写失败窗口
        rewriter(report.repair.chapters_to_rewrite)
        passed = NovelHealthReport(overall_pass=True, dimensions=report.dimensions)
        passed.rolled_back = True
        passed.rollback_attempts = 1
        return passed


class _FakeWriter:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.next_chapter = 8

    def run(self, rewrite_hint=None):
        ch = self.next_chapter
        self.next_chapter += 1
        self.calls.append({"chapter": ch, "hint": rewrite_hint})
        return SimpleNamespace(
            chapter_num=ch, chapter_text="x" * 100, chapter_title=f"第{ch}章"
        )


class _FakeEditor:
    def review(self, text):
        return SimpleNamespace(passed=True, block_count=0, frozen_violations=[])


class _FakeMemory:
    def log(self, *a, **k):
        return None

    def record_chapter(self, *a, **k):
        return None


class _FakePlanner:
    def load_plan(self):
        return None

    def run(self, brief):
        return None


def _seed_writable_project(tmp_path: Path) -> None:
    sm = SettingManager(tmp_path)
    sm.save_world({"title": "t", "genre": "modern", "style": {}}, "# w\n")
    (tmp_path / "architecture.md").write_text(
        "---\nconfirmed: true\n---\n# a\n", encoding="utf-8"
    )
    sm.save_subline(
        "S01_主线",
        {"subline_name": "主线", "characters": []},
        "# s\n\n## 支线目标\nx\n\n## 剧集压力曲线\n"
        "| 阶段 | 章节 | 张力等级 |\n|---|---|---|\n| 铺垫 | 1-100 | 低 |\n",
    )
    st = StateMachine(tmp_path)
    st.state = State.WRITING
    st.progress = {"total_written": 12, "current_chapter": 12}
    st.save()


def test_pipeline_passes_targeted_hint_to_writer(tmp_path: Path) -> None:
    _seed_writable_project(tmp_path)

    fake_eval = _FakeEvaluator()
    fake_writer = _FakeWriter()

    pipeline = AgenticPipelineWorkflow(
        project_dir=tmp_path,
        eval_enabled=True,
        target_chapters=12,
        brief="",  # 跳过 planner
        planner=_FakePlanner(),
        writer_workflow=fake_writer,
        editor=_FakeEditor(),
        evaluator=fake_eval,
        memory=_FakeMemory(),
    )
    result = pipeline.run()

    # 回溯触发：Writer 被调用重写 5 章
    assert len(fake_writer.calls) == 5
    # 每次重写都带针对性提示，且包含失败维度标签与章节区间
    for c in fake_writer.calls:
        hint = c["hint"]
        assert hint is not None
        assert "连贯性" in hint
        assert "追读力" in hint
        assert "第 8" in hint and "12" in hint
    # 重写后体检通过
    assert result.health_report is not None
    assert result.health_report["overall_pass"] is True
    assert result.health_report["rolled_back"] is True


def test_build_rewrite_hint_includes_failed_dims() -> None:
    report = _fail_report()
    hint = build_rewrite_hint(report, [8, 9, 10, 11, 12])
    assert "连贯性" in hint
    assert "追读力" in hint
    assert "第 8" in hint and "12" in hint
    assert "硬指标不达标" in hint

    # 无报告时返回空串
    assert build_rewrite_hint(None, []) == ""
