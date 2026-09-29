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
from agent.workflows.pipeline.agentic_pipeline import AgenticPipelineWorkflow, build_rewrite_hint


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

    def run(self, rewrite_hint=None, chapter_num=None):
        # 2026-09-12：rewriter 须传 chapter_num 锚定（F-8）——不锚定会把
        # LOCAL_REPAIR 写成「新章」而非重写问题章
        assert chapter_num is not None, "rewriter 必须传 chapter_num 锚定重写章号"
        ch = chapter_num
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
        # 2026-09-18 起写前闸要求**章级**粒度（纯阶段模板 fail-fast）；
        # 本测试聚焦回溯重写链路，造数按新判据补齐即可（保持原意图）。
        "# s\n\n## 支线目标\nx\n\n## 情节点序列\n"
        + "\n".join(f"第{i}章：情节点=测试情节点{i}" for i in range(1, 21))
        + "\n\n## 剧集压力曲线\n"
        "| 阶段 | 章节 | 张力等级 |\n|---|---|---|\n| 铺垫 | 1-100 | 低 |\n",
    )
    st = StateMachine(tmp_path)
    st.state = State.WRITING
    st.progress = {"total_written": 12, "current_chapter": 12}
    st.save()


def test_pipeline_passes_targeted_hint_to_writer(tmp_path: Path, monkeypatch) -> None:
    """2026-09-26 换轨后契约：回溯修复对**已落盘章节**走
    FeedbackRewriter 最小编辑修订（patch 模式）——本测试钉住新契约：
    ①每个待修章调用一次 FeedbackRewriter.rewrite；
    ②feedback 带针对性提示（失败维度标签 + 按章过滤）；
    ③必须 patch 模式 + block 门禁 + 备份（最小改动、坏补丁不落盘）。
    """
    _seed_writable_project(tmp_path)
    # patch 模式要求章节文件存在（不存在则走回滚全章重生成路径）
    ch_dir = tmp_path / "chapters"
    ch_dir.mkdir(exist_ok=True)
    for i in range(8, 13):
        (ch_dir / f"ch{i:03d}.md").write_text(
            f"# 第 {i} 章 · 测试\n\n第{i}章正文。", encoding="utf-8"
        )
    # R2-D：预算规划集成后写章循环注入 BudgetPlanner(llm_client=...)；
    # 本测试聚焦回溯重写链路，预算规划置为无 LLM（plan 返回 False）排除干扰。
    monkeypatch.setattr(
        "agent.workflows.pipeline.budget_planner.BudgetPlanner.plan",
        lambda self: False,
    )

    fake_eval = _FakeEvaluator()

    calls: list[dict] = []

    class _FakeFR:
        def __init__(self, project_dir, llm_client=None, console=None, **kw):
            pass

        def rewrite(self, chapter_num, feedback, *, backup=False, gate_mode="advisory", mode="patch", **kw):
            calls.append(
                {
                    "chapter": chapter_num,
                    "feedback": feedback,
                    "backup": backup,
                    "gate_mode": gate_mode,
                    "mode": mode,
                }
            )
            return SimpleNamespace(
                rewritten=True,
                new_text=f"# 第 {chapter_num} 章 · 修订正文。",
                error="",
                chapter_num=chapter_num,
            )

    monkeypatch.setattr(
        "agent.core.quality.rewrite.feedback_rewriter.FeedbackRewriter", _FakeFR
    )

    pipeline = AgenticPipelineWorkflow(
        project_dir=tmp_path,
        eval_enabled=True,
        target_chapters=12,
        brief="",  # 跳过 planner
        planner=_FakePlanner(),
        writer_workflow=_FakeWriter(),
        editor=_FakeEditor(),
        evaluator=fake_eval,
        memory=_FakeMemory(),
    )
    result = pipeline.run()

    # 回溯触发：FeedbackRewriter 被按章调用 5 次
    assert [c["chapter"] for c in calls] == [8, 9, 10, 11, 12]
    for c in calls:
        hint = c["feedback"]
        assert hint is not None
        # _fail_report 中真正 failed 的维度是连贯性/追读力（其余 0 缺陷判定通过）
        assert "连贯性" in hint and "追读力" in hint
        assert c["mode"] == "patch" and c["backup"] is True and c["gate_mode"] == "block"
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
