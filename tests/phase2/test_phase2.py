"""Phase 2 离线测试（无 LLM / 无网络）

覆盖：
- 统一 Memory Layer（语义 / 会话 / 整合 / 门面）
- PlannerAgent（注入 decide）
- EditorAgent（注入 consistency_fn + 冻结字段启发式）
- EvaluatorAgent（确定性指标 + 自动回溯归档 + 超限上报）
- AgenticPipelineWorkflow（注入 stub 编排全流程）

运行：pytest tests/phase2/test_phase2.py
"""

from __future__ import annotations

import shutil
from pathlib import Path

import frontmatter
import pytest

from agent.agents.evaluator_agent import (
    DimensionResult,
    EvaluatorAgent,
    NovelHealthReport,
    RepairPlan,
)
from agent.agents.editor_agent import EditConflict, EditorAgent, EditReport
from agent.agents.planner_agent import MasterPlan, PlannerAgent
from agent.core.state_machine import State, StateMachine
from agent.memory import MemoryLayer
from agent.memory.consolidated import ConsolidatedMemory
from agent.memory.conversation import ConversationMemory
from agent.memory.semantic import SemanticMemory


# ============================================================
# 辅助：构造临时项目
# ============================================================
def _make_project(tmp_path: Path, n_chapters: int = 0, foreshadows: str = "") -> Path:
    (tmp_path / "chapters").mkdir(parents=True, exist_ok=True)
    (tmp_path / "world.md").write_text(
        "# 测试书\n\n题材：xiuxian\n体量：短篇\n", encoding="utf-8"
    )
    if foreshadows:
        (tmp_path / "foreshadows.md").write_text(foreshadows, encoding="utf-8")
    # 状态机：WRITING + 进度
    sm = StateMachine(tmp_path)
    sm.state = State.WRITING
    sm.progress = {"total_written": n_chapters, "current_chapter": n_chapters}
    sm.save()
    for n in range(1, n_chapters + 1):
        f = tmp_path / "chapters" / f"ch{n:03d}.md"
        f.write_text(
            f"---\nchapter_title: 第{n}章\n---\n第{n}章内容。林轩修炼，境界提升。\n",
            encoding="utf-8",
        )
    return tmp_path


def _seed_planning(tmp_path: Path) -> None:
    """预置完整规划产物，使 G3 编排器幂等跳过真实规划（离线测试用，避免触发真实 LLM）。

    仅用于单元测试：让 AgenticPipelineWorkflow 跳过 M1~M4 规划阶段，
    直接验证写章/护栏等下游逻辑。G3 改造后 ``_ensure_setting_set`` 不再写 stub，
    而是串联真实工作流；本函数模拟「规划已完成的半残/完整项目」，对齐新语义。
    """
    (tmp_path / "world.md").write_text(
        "# 测试书\n\n题材：xiuxian\n体量：短篇\n", encoding="utf-8"
    )
    (tmp_path / "discussion.md").write_text(
        "# 脉络讨论（测试占位）\n", encoding="utf-8"
    )
    (tmp_path / "architecture.md").write_text(
        "---\n"
        "confirmed: true\n"
        "theme: 测试\ncore_conflict: 测试\nworld_building: 测试\n"
        "power_system: 测试\nmajor_plotlines: 测试\ncharacter_arcs: 测试\n"
        "pacing: 测试\ntone: 测试\n"
        "---\n\n# 故事架构（测试）\n",
        encoding="utf-8",
    )
    (tmp_path / "outline.md").write_text(
        "---\nsublines: []\n---\n\n# 故事大纲（测试）\n", encoding="utf-8"
    )
    chars_dir = tmp_path / "characters"
    chars_dir.mkdir(parents=True, exist_ok=True)
    (chars_dir / "主角.md").write_text(
        "# 主角\n\n- identity: 测试\n- core_motivation: 测试\n", encoding="utf-8"
    )


# ============================================================
# 1. Memory Layer
# ============================================================
def test_semantic_add_and_retrieve_ranking():
    mem = SemanticMemory(None)
    mem.add("主角林轩是青云门弟子，金丹期", type="character", tags=["林轩"])
    mem.add("世界观：修仙界分九大宗门", type="setting", tags=["world"])
    res = mem.retrieve("主角林轩的境界", top_k=1)
    assert res, "应召回至少一条"
    assert "林轩" in res[0][0].text
    assert res[0][1] > 0


def test_conversation_append_and_query():
    conv = ConversationMemory(None)
    conv.append("plan", "制定计划A")
    conv.append("chapter", "第1章完成")
    conv.append("edit", "第1章编辑通过")
    recent = conv.query(recent=2)
    assert [e.kind for e in recent] == ["chapter", "edit"]
    kinds = conv.query(kinds=["plan"])
    assert len(kinds) == 1 and kinds[0].kind == "plan"
    kw = conv.query(keyword="编辑")
    assert all("编辑" in e.message for e in kw)


def test_consolidated_merge_and_persist(tmp_path):
    cm = ConsolidatedMemory(tmp_path)
    cm.update(open_debts=["伏笔F01"], plot_threads=["主线复仇"])
    cm.update(open_debts=["伏笔F02"], plot_threads=["主线复仇"])  # 去重
    assert cm.get("open_debts") == ["伏笔F01", "伏笔F02"]
    assert cm.get("plot_threads") == ["主线复仇"]
    # 重新加载
    cm2 = ConsolidatedMemory(tmp_path)
    assert cm2.get("open_debts") == ["伏笔F01", "伏笔F02"]


def test_memory_layer_record_chapter(tmp_path):
    ml = MemoryLayer(tmp_path)
    ml.record_chapter(1, "试炼", "林轩入山门")
    bible = ml.book_bible()
    assert bible["last_consolidated_chapter"] == 1
    assert any(e.kind == "chapter" for e in ml.conversation.query())


# ============================================================
# 2. PlannerAgent
# ============================================================
def _planner_decide(messages):
    return {
        "brief": "测试思路",
        "genre": "xiuxian",
        "title": "测试书",
        "total_chapters": 30,
        "episode_tree": [
            {"id": "A1", "name": "第一弧", "chapter_start": 1, "chapter_end": 10,
             "goal": "入门", "subline_id": ""}
        ],
        "character_skeleton": [
            {"name": "林轩", "role": "主角", "faction": "青云门", "realm": "金丹",
             "arc": "成长", "fingerprint": ""}
        ],
        "foreshadow_plan": [],
        "quality_targets": {
            "foreshadow_recycle_rate": 0.9, "coherence": 80.0, "readability": 75.0,
            "pacing_abnormal": 0.03, "character_stability_high": 0,
            "setting_consistency_high": 0, "logic_holes": 0,
        },
        "notes": "",
    }


def test_planner_produces_plan_and_persists(tmp_path):
    ml = MemoryLayer(tmp_path)
    planner = PlannerAgent(tmp_path, memory=ml, decide=_planner_decide)
    plan = planner.run("我想写修仙")
    assert isinstance(plan, MasterPlan)
    assert plan.total_chapters == 30
    assert plan.character_skeleton[0].name == "林轩"
    # 落盘
    assert (tmp_path / ".state" / "plan.json").exists()
    # 写入 Memory
    chars = ml.semantic.retrieve("林轩", types=["character"])
    assert chars, "角色应写入语义记忆"
    assert ml.consolidated.get("quality_targets")["foreshadow_recycle_rate"] == 0.9


def test_planner_revise_appends_notes(tmp_path):
    planner = PlannerAgent(tmp_path, decide=_planner_decide)
    planner.run("思路")
    revised = planner.revise_plan(5, "节奏偏慢")
    assert "进度@5" in revised.notes and "节奏偏慢" in revised.notes


# ============================================================
# 3. EditorAgent
# ============================================================
def test_editor_passes_when_no_conflict(tmp_path):
    editor = EditorAgent(tmp_path, consistency_fn=lambda p, t, c: [])
    rep = editor.review("林轩踏入山门，开始修炼。")
    assert isinstance(rep, EditReport)
    assert rep.passed is True
    assert rep.block_count == 0


def test_editor_blocks_on_injected_conflict(tmp_path):
    def cf(project, text, ctx):
        return [EditConflict("field_conflict", "block", "与世界观冲突：境界不符", ["改为金丹期"])]

    editor = EditorAgent(tmp_path, consistency_fn=cf)
    rep = editor.review("林轩突破到圣人境。")
    assert rep.passed is False
    assert rep.block_count == 1
    assert rep.conflicts[0].description.startswith("与世界观冲突")


def test_editor_frozen_field_heuristic(tmp_path):
    editor = EditorAgent(tmp_path, frozen_fields=["冻结:主角名为林轩"])
    rep = editor.review("林轩不再是原来的林轩，他其实是卧底。")
    assert rep.frozen_violations, "应检测到冻结字段疑似被改写"
    # 正常提及不应误报
    rep2 = editor.review("林轩踏入山门。")
    assert not rep2.frozen_violations


# ============================================================
# 4. EvaluatorAgent
# ============================================================
def test_evaluator_foreshadow_recycle_rate(tmp_path):
    proj = _make_project(
        tmp_path,
        n_chapters=3,
        foreshadows=(
            "| ID | 内容 | 埋设 | 预期回收 | 状态 |\n"
            "|---|---|---|---|---|\n"
            "| F-01 | 古剑 | ch001 | ch010 | 已回收 |\n"
            "| F-02 | 秘境 | ch002 | ch020 | 已埋 |\n"
            "| F-03 | 身世 | ch003 | ch030 | 逾期 |\n"
        ),
    )
    ev = EvaluatorAgent(proj)
    rate, stat = ev._metric_foreshadow_recycle()
    # resolved=1, unresolved=2 → rate = 1/3
    assert stat["resolved"] == 1 and stat["unresolved"] == 2
    assert abs(rate - 1 / 3) < 1e-6


def test_evaluator_pacing_abnormal(tmp_path):
    proj = _make_project(tmp_path, n_chapters=5)
    # 制造 1 个异常超短章
    (proj / "chapters" / "ch003.md").write_text(
        "---\nchapter_title: x\n---\n短。\n", encoding="utf-8"
    )
    ev = EvaluatorAgent(proj)
    rate, stat = ev._metric_pacing()
    assert stat["chapters"] == 5
    assert stat["abnormal"] == 1
    assert abs(rate - 0.2) < 1e-6


def test_evaluator_overall_pass_with_safe_defaults(tmp_path):
    proj = _make_project(tmp_path, n_chapters=2,
                          foreshadows="| ID | 内容 | 埋设 | 预期 | 状态 |\n|---|---|---|---|---|\n|F-01|a|ch001|ch002|已回收|\n")
    # G8（拍板 6）：本测试仅测七维 safe-defaults；G8 验收维度默认开 → 关闭
    ev = EvaluatorAgent(proj, mainline_gate=False, ending_gate=False)  # 无 score_fn → 默认通过型
    rep = ev.evaluate()
    assert rep.overall_pass is True
    assert rep.score >= 0


def test_evaluator_auto_rollback_on_failure(tmp_path):
    # 8 章 + 伏笔回收率 0（2 已埋、0 回收）→ 不达标 → 自动回溯最近 5 章
    proj = _make_project(
        tmp_path,
        n_chapters=8,
        foreshadows=(
            "| ID | 内容 | 埋设 | 预期 | 状态 |\n|---|---|---|---|---|\n"
            "| F-01 | a | ch001 | ch010 | 已埋 |\n"
            "| F-02 | b | ch002 | ch020 | 已埋 |\n"
        ),
    )
    # 强制 recycle 不达标：score_fn 不影响 recycle（确定性），但让其他维度也通过，
    # 仅靠 recycle<0.9 触发回溯。
    ev = EvaluatorAgent(proj, rollback_window=5)  # 默认 auto_rollback=True
    rep = ev.evaluate()
    assert rep.overall_pass is False
    assert rep.rolled_back is True
    assert isinstance(rep.repair, RepairPlan)
    # target = max(1, 8-5+1) = 4；重写 4..8
    assert rep.repair.target_chapter == 4
    assert rep.repair.chapters_to_rewrite == [4, 5, 6, 7, 8]
    # 4..8 被归档
    archived = list((proj / "chapters" / "_archived").glob("*/ch*.md"))
    archived_nums = sorted(int(p.stem[2:]) for p in archived)
    assert archived_nums == [4, 5, 6, 7, 8]
    # 剩余章节 1..3
    remain = sorted(int(p.stem[2:]) for p in (proj / "chapters").glob("ch*.md"))
    assert remain == [1, 2, 3]
    # 进度指针回退
    sm = StateMachine(proj)
    sm.load()
    assert sm.progress["total_written"] == 3


def test_evaluator_escalates_when_repair_fails(tmp_path):
    proj = _make_project(tmp_path, n_chapters=8)
    # score_fn 永远让硬指标失败 → 重写也无法通过 → 超限上报
    def bad_score(name, project):
        if name == "character_stability_high":
            return 1  # 永远不达标
        if name in ("coherence", "readability"):
            return 0.0
        return 0.0

    # G8（拍板 6）：本测试仅测 G1 回溯超限 escalated；G8 验收维度默认开 → 关闭
    ev = EvaluatorAgent(proj, score_fn=bad_score, rollback_window=5, max_rollback_attempts=2,
                        mainline_gate=False, ending_gate=False)

    def noop_rewriter(nums):
        return None  # 重写不修复

    rep = ev.evaluate_with_repair(noop_rewriter)
    assert rep.escalated is True
    assert "上限" in rep.escalated_reason or "无可回退" in rep.escalated_reason


# ============================================================
# 5. AgenticPipelineWorkflow（注入 stub 编排）
# ============================================================
class _StubWriter:
    """离线写章 stub：写文件 + 推进进度 + 返回结果对象。"""

    def __init__(self, project_dir):
        self.project_dir = Path(project_dir)
        self.chapters_dir = self.project_dir / "chapters"

    def run(self):
        sm = StateMachine(self.project_dir)
        sm.load()
        total = int((sm.progress or {}).get("total_written", 0))
        n = total + 1
        self.chapters_dir.mkdir(parents=True, exist_ok=True)
        content = f"第{n}章 测试章节。林轩修炼。\n"
        f = self.chapters_dir / f"ch{n:03d}.md"
        f.write_text(f"---\nchapter_title: 第{n}章\n---\n{content}", encoding="utf-8")
        sm.progress = {**(sm.progress or {}), "total_written": n, "current_chapter": n}
        sm.save()

        class R:
            pass

        r = R()
        r.chapter_num = n
        r.chapter_title = f"第{n}章"
        r.chapter_text = content
        r.word_count = len(content)
        r.quality_passed = True
        r.revision_attempts = 0
        return r


class _StubEvaluator:
    memory_log = None

    def evaluate_with_repair(self, rewriter):
        dims = [
            DimensionResult("foreshadow_recycle_rate", "伏笔闭环", 1.0, 0.9, ">=", False, "computed")
        ]
        return NovelHealthReport(overall_pass=True, score=100.0, dimensions=dims)


def test_pipeline_full_run_with_stubs(tmp_path):
    proj = _make_project(tmp_path, n_chapters=0)
    _seed_planning(proj)
    ml = MemoryLayer(tmp_path)
    planner = PlannerAgent(tmp_path, memory=ml, decide=_planner_decide)
    editor = EditorAgent(tmp_path, consistency_fn=lambda p, t, c: [], memory=ml)

    from agent.workflows.agentic_pipeline import AgenticPipelineWorkflow

    pipeline = AgenticPipelineWorkflow(
        project_dir=proj,
        brief="测试思路",
        target_chapters=3,
        eval_enabled=True,
        planner=planner,
        writer_workflow=_StubWriter(proj),
        editor=editor,
        evaluator=_StubEvaluator(),
        memory=ml,
    )
    result = pipeline.run()
    assert result.planned is True
    assert result.chapters_written == 3
    assert result.final_chapter == 3
    assert result.health_report is not None
    assert result.health_report["overall_pass"] is True
    # 记忆回写：3 条章节事件
    ch_events = ml.conversation.query(kinds=["chapter"])
    assert len(ch_events) == 3
    # 章节文件确实写出
    assert len(list((proj / "chapters").glob("ch*.md"))) == 3


def test_pipeline_skips_plan_when_no_brief(tmp_path):
    proj = _make_project(tmp_path, n_chapters=0)
    _seed_planning(proj)
    ml = MemoryLayer(tmp_path)
    editor = EditorAgent(tmp_path, consistency_fn=lambda p, t, c: [], memory=ml)
    from agent.workflows.agentic_pipeline import AgenticPipelineWorkflow

    pipeline = AgenticPipelineWorkflow(
        project_dir=proj,
        target_chapters=2,
        eval_enabled=True,
        writer_workflow=_StubWriter(proj),
        editor=editor,
        evaluator=_StubEvaluator(),
        memory=ml,
    )
    result = pipeline.run()
    assert result.planned is False
    assert result.chapters_written == 2
