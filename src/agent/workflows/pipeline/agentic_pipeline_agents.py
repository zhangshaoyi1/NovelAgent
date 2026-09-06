"""AgenticPipelineWorkflow 拆分的 Mixin（机械搬移，行为零改动）。

拆分背景：单文件 1600+ 行不利维护（对齐 m5 Mixin 拆分模式，架构评审 P2-9）。
主文件保留类定义、``__init__`` 与 ``run()``；本文件（构造默认 Agent + 进度读取）承载对应方法组。
仅供 ``AgenticPipelineWorkflow`` 继承组合，不要单独使用。
"""

from __future__ import annotations

import json
from typing import Any

class _PipelineAgentsMixin:
    def _ensure_planner(self) -> PlannerLike:
        if self.planner is None:
            from agent.agents.planner import PlannerAgent

            self.planner = PlannerAgent(
                self.project_dir, llm_client=self.llm, memory=self.memory,
                console=self.console,
                method_enabled=self.method_enabled,  # G11：写作方法模板注入
            )
        return self.planner

    def _ensure_writer(self) -> WriterWorkflow:
        if self.writer_workflow is None:
            from agent.workflows.writing.agentic_write import AgenticWriteWorkflow

            self.writer_workflow = AgenticWriteWorkflow(
                self.project_dir, llm_client=self.llm, console=self.console, tier=self.tier,
                # ---- G9：章内子阶段事件注入（默认 None 零开销）----
                event_emitter=self._emit_substage,
                # ---- G11：风格模仿透传（project/style.md 存在即注入）----
                style_enabled=self.style_enabled,
                style_file=self.style_file,
                # ---- G12：爽点剧本/情绪目标透传（.state/payoff_script.json 存在即注入）----
                payoff_enabled=self.payoff_enabled,
            )
        return self.writer_workflow

    def _ensure_editor(self) -> EditorLike:
        if self.editor is None:
            from agent.agents.editor import EditorAgent

            self.editor = EditorAgent(
                self.project_dir, llm_client=self.llm, console=self.console, memory=self.memory,
            )
        return self.editor

    def _ensure_evaluator(self) -> EvaluatorLike:
        if self.evaluator is None:
            from agent.agents.evaluator import EvaluatorAgent

            # 质量目标优先取自 MasterPlan
            qt: dict[str, float] = {}
            try:
                plan = self._ensure_planner().load_plan()
                if plan is not None:
                    qt = plan.quality_targets.model_dump()
            except Exception:  # noqa: BLE001
                pass  # noqa: SILENT_DEGRADE
            score_fn = None
            try:
                from agent.core.quality.scoring.reader_appeal import ReaderAppealScorer

                # 默认接真 LLM 评分（B1）；LLM 不可用时 scorer 内部自动降级为离线安全默认。
                score_fn = ReaderAppealScorer(llm_client=self.llm).score
            except Exception:  # noqa: BLE001
                score_fn = None  # noqa: SILENT_DEGRADE
            appeal_scorer = None
            if self.appeal_gate:
                try:
                    from agent.core.quality.scoring.reader_appeal import ReaderAppealScorer

                    appeal_scorer = ReaderAppealScorer(llm_client=self.llm)
                except Exception:  # noqa: BLE001
                    appeal_scorer = None  # noqa: SILENT_DEGRADE
            # G6：B4 golden_scorer 复用同一六维评分器实例（评前三章与评末章可共用，拍板 §12-3）
            golden_scorer = appeal_scorer if self.golden_three_gate else None
            # D-J：由 workflow 侧注入回退能力（agents 不再直接 import workflows）
            from agent.workflows.evaluation.m10_rollback import M10RollbackWorkflow

            self.evaluator = EvaluatorAgent(
                self.project_dir,
                console=self.console,
                rollback_window=self.rollback_window,
                max_rollback_attempts=self.max_rollback_attempts,
                quality_targets=qt or None,
                score_fn=score_fn,
                rollback_provider=M10RollbackWorkflow(self.project_dir, console=self.console),
                # G5：迷爱看六维双闸透传
                appeal_scorer=appeal_scorer,
                appeal_gate=self.appeal_gate,
                appeal_threshold=self.appeal_threshold,
                appeal_window=self.appeal_window,
                # ---- G6 透传 ----
                golden_scorer=golden_scorer,
                golden_three_gate=self.golden_three_gate,
                golden_three_threshold=self.golden_three_threshold,
                golden_three_floor=self.golden_three_floor,
                padding_gate=self.padding_gate,
                padding_threshold=self.padding_threshold,
                # ---- G7 透传：人话总结层展示开关 ----
                human_summary=self.human_summary,
                # ---- G8 透传：验收维度开关 + 口径参数 ----
                mainline_gate=self.mainline_gate,
                ending_gate=self.ending_gate,
                mainline_window=self.mainline_window,
                ending_ratio=self.ending_ratio,
            )
        # 把回溯事件写进 Memory
        try:
            self.evaluator.memory_log = lambda kind, msg, data: self.memory.log(kind, msg, data)  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass  # noqa: SILENT_DEGRADE
        return self.evaluator

    # ---------------------------------------------------------------- 进度
    def _current_total(self) -> int:
        try:
            self.state_machine.load()
            return int((self.state_machine.progress or {}).get("total_written", 0))
        except Exception:  # noqa: BLE001
            return 0

    def _resolve_target(self) -> int:
        if self.target_chapters:
            return self.target_chapters
        # 取 MasterPlan 的总章数
        try:
            plan = self._ensure_planner().load_plan()
            if plan is not None and plan.total_chapters:
                return plan.total_chapters
        except Exception:  # noqa: BLE001
            pass  # noqa: SILENT_DEGRADE
        return 100

    def _book_total(self) -> int:
        """全书设计总章数（用于结局模式/主线决策），与 Evaluator 同源。

        关键：与『本轮续写写几章』（batch）解耦。续写批次把绝对目标传进
        ``--chapters`` 时，target 只是本轮写章上限，绝不能把 batch 当全书目标，
        否则会在 batch 量就过早进入结局模式。判定顺序：
        1) ``.state/plan.json`` 的 total_chapters（规划产物，最权威）；
        2) ``_resolve_target()``（--chapters 绝对目标 / plan 总章数 / 兜底 100）。

        注意：**禁止**用「当前 chapters 目录是否为空」这类运行期判定。它会在写
        完第一章后即变为非空，导致全书章数在一次连续写入中漂移、结局判定失效。
        本方法只读稳定来源（plan.json / target），每次返回一致值。
        """
        try:
            plan_file = self.project_dir / ".state" / "plan.json"
            if plan_file.exists():
                data = json.loads(plan_file.read_text(encoding="utf-8"))
                if data.get("total_chapters"):
                    return int(data["total_chapters"])
        except Exception:  # noqa: BLE001 - 读取失败走兜底
            pass  # noqa: SILENT_DEGRADE
        # 修复（2026-09-05）：plan.json 缺失时绝不能把本轮批次目标（--chapters）
        # 当全书总章数——否则 10 章批次会让结局模式在第 8 章触发、路线跨度失真。
        # 与 _resolve_target 的兜底一致，回归设计默认值 100。
        return 100

