"""AgenticPipelineWorkflow 拆分的 Mixin（机械搬移，行为零改动）。

拆分背景：单文件 1600+ 行不利维护（对齐 m5 Mixin 拆分模式，架构评审 P2-9）。
主文件保留类定义、``__init__`` 与 ``run()``；本文件（G8 主线推进 + 结局模式）承载对应方法组。
仅供 ``AgenticPipelineWorkflow`` 继承组合，不要单独使用。
"""

from __future__ import annotations

import json

from agent.core.engine.state_machine import StateMachine
from agent.workflows.pipeline.agentic_pipeline_types import PipelineResult

class _PipelineEndingMixin:
    # ---------------------------------------------------------------- G8 主线推进 + 结局模式
    def _maybe_enter_ending_mode(self) -> None:
        """结局模式触发（拍板 2：chapter > book_total*(1-ending_ratio)）。

        一旦进入不退出（拍板 4）：`progress.ending_mode=true` 持久化到 state.json，
        回溯重写（G1）后 M5/AgenticWrite 仍读到 true → 重写仍在结局模式。
        ending_ratio 已在 __init__ 钳制到 [0, 0.5]。

        用 ``_book_total()``（全书设计总章数，plan.json→100）而非本轮写章上限
        target：续写批次的 ``--chapters N`` 只是本轮写到几章，不能用它触发结局，
        否则把批次数当全书目标而过早进入结局模式。
        """
        try:
            self.state_machine.load()
            progress = dict(self.state_machine.progress or {})
            book_total = self._book_total()
            chapter = int(progress.get("total_written", 0)) + 1
            trigger_chapter = int(book_total * (1 - self.ending_ratio)) + 1
            if progress.get("ending_mode"):
                # 一致性自检：total_chapters 调大后触发点后移，早先进入的结局模式
                # 不再自洽（否则全书会被强行带向大结局）。仅当触发点确实后移时
                # 自动退出；book_total 未变时维持「一旦进入不退出」（拍板 4）。
                ended_at = int(progress.get("ending_mode_at") or 0)
                # 仅信任 plan.json 显式配置的 total_chapters（权威来源）；
                # _book_total() 的兜底默认值 100 只是猜测，不得据此退出结局模式
                #（否则无 plan.json 的旧项目/夹具会被误清除，破坏拍板 4 原语义）。
                plan_file = self.project_dir / ".state" / "plan.json"
                plan_configured = False
                try:
                    _plan = json.loads(plan_file.read_text(encoding="utf-8"))
                    plan_configured = bool(_plan.get("total_chapters"))
                except Exception:  # noqa: BLE001 - 读取失败视为未配置
                    plan_configured = False  # noqa: SILENT_DEGRADE
                if plan_configured and ended_at and ended_at < trigger_chapter:
                    progress["ending_mode"] = False
                    progress.pop("ending_mode_at", None)
                    self.state_machine.progress = progress
                    self.state_machine.save()
                    self.console.print(
                        f"[yellow]全书总章数已调整为 {book_total}，结局模式触发点后移至"
                        f"第 {trigger_chapter} 章（原 {ended_at}），自动退出结局模式[/yellow]"
                    )
                    self._emit_event(
                        "ending_mode_exit",
                        chapter=chapter,
                        ended_at=ended_at,
                        new_trigger=trigger_chapter,
                        book_total=book_total,
                    )
                return  # 不退出（拍板 4）：book_total 未变时维持原语义
            if chapter > book_total * (1 - self.ending_ratio):
                progress["ending_mode"] = True
                progress["ending_mode_at"] = chapter
                self.state_machine.progress = progress
                self.state_machine.save()
                self.console.print(
                    f"[cyan]进入结局模式：第 {chapter} 章起"
                    f"（全书 {book_total} 章 · 最后 {int(self.ending_ratio * 100)}%）[/cyan]"
                )
                # ---- G9（补充边界 6）：记录型事件（只记录不反写，G8 语义零改动）----
                self._emit_event("ending_mode", chapter=chapter, ending_ratio=self.ending_ratio)
        except Exception:  # noqa: BLE001 - 触发异常降级不阻断写章
            pass  # noqa: SILENT_DEGRADE

    def _maybe_advance_mainline(self, target: int) -> None:
        """每 mainline_window 章执行主线推进裁决（委托 MainlineOrchestrator，唯一仲裁点）。

        决策点写 `progress.current_subline` + `progress.mainline_visited`（去重），
        落盘由 orchestrator 统一完成；本方法仅负责触发与上报 G9 事件。
        ``target`` 为兼容保留字段（尚无独立语义，推进由 orchestrator 依据状态裁决）。
        """
        try:
            from agent.workflows.pipeline.budget_planner import BudgetPlanner
            from agent.workflows.pipeline.mainline_orchestrator import MainlineOrchestrator

            from_subline = str(
                (self.state_machine.progress or {}).get("current_subline", "") or ""
            )  # G9：事件记录旧支线（裁决前）
            orch = MainlineOrchestrator(
                self.project_dir,
                self.state_machine,
                self.mainline_window,
                self.console,
                budget_planner=BudgetPlanner(
                    self.project_dir, console=self.console, llm_client=self.llm
                ),
            )
            orch.replan_if_due()  # 每窗口先由 LLM 主编重规划分线预算
            new_subline = orch.maybe_advance()
            if not new_subline:
                return
            progress = dict(self.state_machine.progress or {})
            chapter = int(progress.get("total_written", 0)) + 1
            visited = list(progress.get("mainline_visited", []) or [])
            self.console.print(
                f"[cyan]主线推进：第 {chapter} 章起切至支线 {new_subline}"
                f"（已访问 {len(visited)} 条）[/cyan]"
            )
            # ---- G9（补充边界 6）：记录型事件（只记录不反写，G8 语义零改动）----
            self._emit_event(
                "mainline_advance",
                chapter=chapter,
                from_subline=from_subline,
                to_subline=new_subline,
                visited=len(visited),
            )
        except Exception:  # noqa: BLE001 - 决策异常降级不阻断（G3 哲学）
            pass  # noqa: SILENT_DEGRADE

    def _finalize_g8(self, result: PipelineResult) -> None:
        """G8：填充 result.mainline / result.ending（读 state.json progress，降级占位）。"""
        try:
            sm = StateMachine(self.project_dir)
            sm.load()
            progress = sm.progress or {}
            result.mainline = {
                "current_subline": progress.get("current_subline"),
                "mainline_visited": list(progress.get("mainline_visited", []) or []),
                "mainline_window": self.mainline_window,
            } if self.mainline_gate else None
            result.ending = {
                "ending_mode": bool(progress.get("ending_mode", False)),
                "ending_mode_at": progress.get("ending_mode_at"),
                "ending_ratio": self.ending_ratio,
            } if self.ending_gate else None
        except Exception:  # noqa: BLE001 - 摘要失败不阻断主流程
            result.mainline = None
            result.ending = None  # noqa: SILENT_DEGRADE

