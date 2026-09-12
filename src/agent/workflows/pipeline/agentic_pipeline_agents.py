"""AgenticPipelineWorkflow 拆分的 Mixin（机械搬移，行为零改动）。

拆分背景：单文件 1600+ 行不利维护（对齐 m5 Mixin 拆分模式，架构评审 P2-9）。
主文件保留类定义、``__init__`` 与 ``run()``；本文件（构造默认 Agent + 进度读取）承载对应方法组。
仅供 ``AgenticPipelineWorkflow`` 继承组合，不要单独使用。
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from agent.core.infra.degrade import degrade
from agent.workflows.pipeline.agentic_pipeline_types import build_rewrite_hint

if TYPE_CHECKING:  # pragma: no cover - 仅类型标注
    from agent.core.quality.rollback_budget import RollbackBudget


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
                # ---- F-11：D 多维审查透传（None → writer 默认 True）----
                strict_review=self.strict_review,
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

    def _rolling_eval_checkpoint(self) -> bool:
        """B1（2026-09-10）+ P1（2026-09-12）：滚动体检检查点——写满 N 章就地体检**并就地修复**。

        Returns:
            True = 达标（或体检无法执行时保守放行继续写）；False = 不达标且修复未收敛，应中断本批。

        设计动机：原体检只在写章循环**结束后**执行一次。web 端续写按钮把批次翻译成
        「绝对目标章数」（如 --chapters 190），只要目标未写满或中途被 task-stop 打断，
        体检就永远不会触发（2026-09-10 事故：ch156-178 共 23 章零体检记录）。
        改为循环内周期体检后，长批次也能滚动体检、及时刹车。

        P1 变更（2026-09-12，五灵破归档 31 次回退复盘）：
        - 由 ``evaluate()`` 改为 ``evaluate_with_repair(self._make_rewriter())``。
          原实现在硬指标不达标时**只回退、不带失败明细重写**，把修复推给外层
          「再起一批、盲写同样 5 章」——同样章间矛盾、再次回退，是死循环的直接原因。
          现在检查点内就带 ``build_rewrite_hint`` 逐章定向重写并复评。
        - 回退次数经 :class:`RollbackBudget` **跨批持久化**（旧计数只活在单次
          ``evaluate_with_repair`` 循环里、每批新建 Evaluator 即归零），连续超限或
          评测器自身已 escalate → 置 ``_rolling_escalation_reason`` 停批上报人工。

        失败降级：体检抛异常（LLM 不可用等）时**放行**——体检是质量闸门，
        不应因基础设施抖动而中断写作（与既有 `evaluate_with_repair` 的异常处理一致）。
        """
        self._emit_progress("evaluating", 0, 100)
        self._emit_event("evaluating")
        try:
            evaluator = self._ensure_evaluator()
            report = evaluator.evaluate_with_repair(self._make_rewriter())
        except Exception as e:  # noqa: BLE001
            self.console.print(f"[yellow]⚠ 滚动体检执行失败（{e}），放行继续[/yellow]")
            self._emit_failure("eval", str(e), severity="warn")
            return True  # noqa: SILENT_DEGRADE - 体检基建失败不阻断写作
        if report is None:
            return True  # noqa: SILENT_DEGRADE - 无报告视为不可判定，放行
        # ---- HA-Eval L4（2026-09-11）：闸门分级 ----
        # 原实现「overall_pass=False 即中断整批」把软维度（迷爱看/黄金三章/伏笔等
        # required=False 的 LLM 评分维）与硬指标一视同仁——单章软维单次 FAIL 就会
        # 中断整个批次（09-11 事故：爽点密度 38<40 → escalated → 写作停摆）。
        # 现按四象限裁决（见 NovelHealthReport.gate_decision）：
        #   block   → 可信失败的**硬指标** → 中断本批（不可放宽）
        #   warn    → 仅软维度失败         → 告警，继续写作
        #   recheck → 存在不可信证据       → 只告警，不处置（本入口无复评能力）
        dims = getattr(report, "dimensions", None) or []
        failed = [d for d in dims if not getattr(d, "passed", True)]
        score = getattr(report, "score", None)
        if hasattr(report, "gate_decision"):
            gate = report.gate_decision()
        else:  # 兼容缺该属性的旧报告对象
            gate = "pass" if bool(getattr(report, "overall_pass", False)) else "block"  # noqa: SILENT_DEGRADE
        fail_txt = "、".join(
            f"{d.label}={d.value}" for d in failed[:6]
        ) or "见报告"
        if gate == "pass":
            self._rollback_budget().reset()  # P1：连续回退计数归零（通过即翻篇）
            self.console.print(f"[green]✓ 滚动体检通过（得分 {score}）[/green]")
            return True
        if gate == "block":
            # ---- P1：回退计数跨批持久化 + 熔断上报人工 ----
            budget = self._rollback_budget()
            target = self._last_rollback_target(report)
            # 只有真的执行了回退重写才计数；gate=block 且未回退必然 escalated，
            # 由下面 evaluator_gave_up 分支上报，避免同一回合与批末体检重复 bump。
            if getattr(report, "rolled_back", False):
                budget.bump(target, fail_txt)
            evaluator_gave_up = bool(getattr(report, "escalated", False))
            if budget.tripped() or evaluator_gave_up:
                reason = (
                    budget.reason_text()
                    if budget.tripped()
                    else (getattr(report, "escalated_reason", "") or
                          f"滚动体检不达标且定向重写未收敛：{fail_txt}")
                )
                self._rolling_escalation_reason = reason
                self.console.print(
                    f"[red]✗ 滚动体检不达标且修复未收敛（得分 {score}）：{fail_txt}\n"
                    f"  {reason}\n  中断本批并上报人工（不再无限重试）。[/red]"
                )
                self._emit_failure("eval", reason, severity="block")
                return False
            self.console.print(
                f"[yellow]⚠ 滚动体检未达标（硬指标，得分 {score}）：{fail_txt}；"
                f"已就地定向重写并复评，仍未达标 → 中断本批"
                f"（连续第 {budget.consecutive} 次，上限 {budget.limit}）[/yellow]"
            )
            return False
        if gate == "recheck":
            self.console.print(
                f"[yellow]⚠ 滚动体检判定证据不可信（得分 {score}）：{fail_txt}；"
                f"本轮只告警不处置，继续写作[/yellow]"
            )
            self._emit_failure("eval", f"滚动体检证据不可信：{fail_txt}", severity="warn")
            return True
        # warn：仅软维度失败 → 告警，继续写作（不回滚、不中断）
        soft_txt = "、".join(
            f"{d.label}={d.value}" for d in failed if not getattr(d, "required", False)
        ) or fail_txt
        self.console.print(
            f"[yellow]⚠ 滚动体检软维度未达标（得分 {score}）：{soft_txt}；"
            f"仅告警，继续写作（不回滚不中断）[/yellow]"
        )
        self._emit_failure("eval", f"滚动体检软维度告警：{soft_txt}", severity="warn")
        return True

    def _rollback_budget(self) -> "RollbackBudget":
        """P1：读取跨批回退预算（.state/rollback_budget.json），缺省按上限 3 起算。"""
        from agent.core.quality.rollback_budget import RollbackBudget

        return RollbackBudget.load(
            self.project_dir, getattr(self, "max_rollback_attempts", 3)
        )

    @staticmethod
    def _last_rollback_target(report: Any) -> int:
        """取本次回退的目标章（用于识别「同一窗口反复翻车」）。"""
        plan = getattr(report, "repair", None)
        try:
            return int(getattr(plan, "target_chapter", 0) or 0)
        except (TypeError, ValueError) as e:
            degrade("pipeline.last_rollback_target", "回退目标章不可解析，按 0 计", e)
            return 0

    def _make_rewriter(self) -> Any:
        """P1：构造「回退后定向重写」回调——把上一轮失败维度编译成针对性提示。

        检查点与批末体检**共用同一实现**。此前只有批末有这条路径，滚动检查点只回退不重写。
        """

        def rewriter(chapter_nums: list[int]) -> None:
            w = self._ensure_writer()
            ev = self._ensure_evaluator()
            hint = build_rewrite_hint(getattr(ev, "last_failed_report", None), chapter_nums)
            for ch in chapter_nums:
                try:
                    w.run(rewrite_hint=hint)
                except Exception as e:  # noqa: BLE001
                    raise RuntimeError(f"重写第 {ch} 章失败：{e}")

        return rewriter

