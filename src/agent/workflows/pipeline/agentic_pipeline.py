"""AgenticPipelineWorkflow —— 全流程自主编排（Phase 2 交付核心）

把 Phase 2 的多智能体团队 + 统一 Memory 串成一条**全流程自主**流水线：

    Planner（架构师，产出 Master Plan）
      → 逐章：WriterAgent（Phase 1 自主写章）写作
              + EditorAgent（主编，一致性并联审查）
              + MemoryLayer 回写（语义/会话/整合）
      → Evaluator（评测员，全书"不崩"终审）
              + 不达标 → 自动回溯（M10Rollback）+ 针对性重写（rewriter）+ 重评
      → 输出量化"不崩"报告

设计原则（与项目一致）：
- **最大化复用**：Writer 复用 Phase 1 ``AgenticWriteWorkflow``；回溯复用 ``M10RollbackWorkflow``；
  Editor/Evaluator 复用 ``ConsistencyChecker`` / ``foreshadow.md`` 解析等。
- **可注入、可离线测试**：``planner`` / ``writer_workflow`` / ``editor`` / ``evaluator``
  / ``memory`` 均可外部注入；真实 LLM 依赖仅在默认构造时惰性创建。
- **降级不阻断**：Evaluator 无 LLM 时给"通过型"安全默认；Editor 失败不阻断出章。
"""

from __future__ import annotations

import os
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from rich.console import Console

from agent.client.gateway_adapter import create_gateway
from llmagent.gateway import Gateway
from agent.core.base.exceptions import is_fatal_provider_error
from agent.core.engine.state_machine import Event, State, StateMachine, TRANSITIONS
from agent.core.story.setting_manager import SettingManager
from agent.core.quality.guardrails import is_architecture_confirmed
from agent.core.engine.workflow_registry import workflow
import frontmatter


from agent.workflows.pipeline.agentic_pipeline_types import (  # noqa: F401
    PipelineResult,
    _PlanStepResult,
    _now_iso,
    build_rewrite_hint,
)
from agent.workflows.pipeline.agentic_pipeline_types import _DOWNGRADE_ORDER  # noqa: F401
from agent.workflows.pipeline.agentic_pipeline_agents import _PipelineAgentsMixin
from agent.workflows.pipeline.agentic_pipeline_cost import _PipelineCostMixin
from agent.workflows.pipeline.agentic_pipeline_ending import _PipelineEndingMixin
from agent.workflows.pipeline.agentic_pipeline_events import _PipelineEventsMixin
from agent.workflows.pipeline.agentic_pipeline_planning import _PipelinePlanningMixin
@workflow("agentic_pipeline")
class AgenticPipelineWorkflow(
    _PipelineAgentsMixin,
    _PipelinePlanningMixin,
    _PipelineCostMixin,
    _PipelineEventsMixin,
    _PipelineEndingMixin,
):
    """全流程自主写作流水线。

    Args:
        project_dir: 小说项目目录。
        llm_client: LLM 客户端（默认构造各 Agent 时惰性使用）。
        tier: Writer 引擎档位（auto/heavy/light）。
        brief: 用户思路（Planner 用）。
        target_chapters: 目标章节数（None 取 MasterPlan.total_chapters 或 state）。
        eval_enabled: 是否运行 Evaluator 终审（默认 True）。
        rollback_window / max_rollback_attempts: 传给 Evaluator。
        planner / writer_workflow / editor / evaluator / memory: 注入（测试/替换用）。
        console: rich 控制台。
    """

    def __init__(
        self,
        project_dir: str | Path,
        llm_client: Gateway | None = None,
        tier: str = "auto",
        brief: str = "",
        target_chapters: int | None = None,
        eval_enabled: bool = True,
        rollback_window: int = 5,
        max_rollback_attempts: int = 3,
        planner: PlannerLike = None,
        writer_workflow: WriterWorkflow = None,
        editor: EditorLike = None,
        evaluator: EvaluatorLike = None,
        memory: Any = None,
        guardrails: Any = None,
        gate_mode: str = "block",  # G10（拍板 5）：默认 block（AI 味命中拒落盘；--ai-gate-mode advisory 显式放宽）
        # F-11：D 多维审查透传（None → writer 默认 True；autowire/write 按 quality_policy 注入）
        strict_review: bool | None = None,
        console: Console | None = None,
        # G4 新增参数（T4 CLI 透传）
        max_time: int | None = None,
        cost_tier: str = "balanced",
        budget_margin: float = 1.0,
        # G10（拍板 6）：auto_downgrade 默认 False（G4 直接调用/测试零回归；CLI 默认 True）
        auto_downgrade: bool = False,
        budget_plan: dict | None = None,  # G10（拍板 6）：.state/budget.json 解析结果（--budget-plan 注入）
        llm_timeout: int | None = None,
        on_progress: Callable[[str, int, int], None] | None = None,
        # G5 新增参数（迷爱看六维双闸）
        appeal_gate: bool = True,
        appeal_threshold: int = 60,
        appeal_window: int = 1,
        # ---- G6 新增参数 ----
        golden_three_gate: bool = True,
        golden_three_threshold: int = 60,
        golden_three_floor: int = 40,
        padding_gate: bool = True,
        padding_threshold: float = 0.30,
        # ---- G7 新增参数（人话总结层展示开关；--no-human-summary 关闭）----
        human_summary: bool = True,
        # ---- G8 新增参数（主线推进 + 结局模式，拍板 1/2/6）----
        mainline_window: int = 5,
        ending_ratio: float = 0.25,
        mainline_gate: bool = True,
        ending_gate: bool = True,
        # ---- G9 新增参数（进度事件流，拍板 2 + 补充边界 1：on_progress 旧签名不动）----
        on_event: Callable[[dict[str, Any]], None] | None = None,
        progress_file: str | Path = ".state/progress.json",
        # ---- G11 新增参数（竞品借鉴三件套：风格模仿 + 写作方法模板）----
        style_enabled: bool = True,
        style_file: str | None = None,
        method_enabled: bool = True,
        # ---- G12 新增参数（读者反馈闭环：爽点剧本/情绪目标注入）----
        payoff_enabled: bool = True,
        # ---- 写章失败冷却重试等待（秒；0=关闭；默认 90s 抵御 provider 间歇性风暴）----
        chapter_retry_wait_s: float = 90.0,
    ) -> None:
        self.project_dir = Path(project_dir)
        self.llm = llm_client
        self.tier = tier
        self.brief = brief
        self.target_chapters = target_chapters
        self.eval_enabled = eval_enabled
        self.rollback_window = rollback_window
        self.max_rollback_attempts = max_rollback_attempts
        self.guardrails = guardrails
        self.gate_mode = gate_mode
        self.strict_review = strict_review
        self.console = console or Console()

        # G4 新增字段：熔断相关（T1 + T4 CLI 透传）
        self._max_time = max_time  # 整轮墙钟上限（秒）
        self._cost_tier = cost_tier  # 预算档位
        self._budget_margin = budget_margin  # 预算安全系数
        # G10（拍板 3/6）：自动降档（CLI 默认 True；直接构造/测试默认 False = G4 行为）
        self._auto_downgrade = bool(auto_downgrade)
        self.budget_plan = budget_plan or {}  # G10（拍板 6）：预算计划配置（仅回显/键覆盖，hard_limit_tokens 不参与判定）
        self._llm_timeout = llm_timeout  # 单调用超时（覆盖 .env）
        self._start_time: float = 0.0  # 起始时间（墙钟计时）
        # F-8（2026-09-07）：token 用量基线（本轮窗口起点快照）。
        # TraceStore 落盘跨轮累计历史 span，直接用 totals() 会把历史消耗计入
        # "本轮预算" → 旧项目开局即误判超预算（降档/熔断）（五灵破 ch13 实证）。
        # 预算判定一律用 _used_tokens() = totals() - _usage_baseline。
        self._usage_baseline: float = 0.0
        self._schema_degraded: bool = False  # Schema 降级标志（从 Planner 读取）
        self.on_progress = on_progress  # 进度回调（T4 CLI 订阅）

        # G5：迷爱看六维双闸
        self.appeal_gate = appeal_gate
        self.appeal_threshold = max(1, appeal_threshold)
        self.appeal_window = max(1, appeal_window)

        # G6：B4/B6 三闸
        self.golden_three_gate = golden_three_gate
        self.golden_three_threshold = max(1, golden_three_threshold)
        self.golden_three_floor = max(1, golden_three_floor)
        self.padding_gate = padding_gate
        self.padding_threshold = max(0.0, min(1.0, padding_threshold))
        # G7：人话总结层展示开关（透传给 EvaluatorAgent）
        self.human_summary = human_summary
        # G8：主线推进 + 结局模式（钳制语义：window≥1，ratio∈[0,0.5]）
        self.mainline_window = max(1, int(mainline_window))
        self.ending_ratio = max(0.0, min(0.5, float(ending_ratio)))
        self.mainline_gate = bool(mainline_gate)
        self.ending_gate = bool(ending_gate)
        # 写章失败的冷却重试等待（秒；0=关闭）。>0 时单章失败先冷却等待再重试 1 次，
        # 用于抵御 provider 间歇性 403/429 风暴（免费池过载），避免整批报废。
        self._chapter_retry_wait_s = float(chapter_retry_wait_s)
        # G11：风格模仿 + 写作方法模板（透传给 writer/planner/outline；默认开）
        self.style_enabled = bool(style_enabled)
        self.style_file = style_file
        self.method_enabled = bool(method_enabled)
        # G12：爽点剧本/情绪目标注入（透传给 writer；默认开）
        self.payoff_enabled = bool(payoff_enabled)
        # G6：写章循环 Guardrails 命中收集（B5-3 修复：结果进报告，不只 console）
        self._guardrail_hits: list[dict[str, Any]] = []
        # ---- G14：门禁重写后仍不达标章节的告警标记（决策①：不阻断，写文件留痕）----
        self._quality_flags: list[dict[str, Any]] = []

        # ---- G9：事件总线（未订阅 on_event / progress_file=None 时零落盘开销）----
        from agent.core.engine.events import ProgressEventBus
        # ---- 统一事件落盘：按项目注册 FileEventStore → <project_dir>/.events/events.jsonl ----
        from agent.core.event_sourcing.event_bus import EventBus

        EventBus.get_instance().configure(self.project_dir)

        # ---- LLM / RAG 调用事件接线：每次 chat/interface/recall 调用 → .events/events.jsonl ----
        # 统一复用 agent_service / CLI 入口的接线（含 RAG recall/index 事件转发），
        # 避免此处手写只覆盖 LLM 而漏掉 RAG 埋点。
        from agent.core.event_sourcing.llm_wiring import wire_llm_event_hook

        wire_llm_event_hook(self.project_dir)

        self._event_bus = ProgressEventBus(
            on_event=on_event,
            progress_file=progress_file,
            cost_provider=self._current_cost_fields,  # G10（拍板 2）：每事件附加成本字段
            event_bus=EventBus.get_instance(),        # 统一总线：全部事件转发落盘 .events/events.jsonl
        )
        self._chapter_t0: float = 0.0  # 本章起点（墙钟，供 chapter_elapsed_s/ETA）

        # 自主规划（G3）状态：关键前置失败则阻塞，交给 run() 安全退出、不进写章。
        self._plan_blocked = False
        self._plan_block_reason = ""
        self._plan_tripped = False  # G4: 规划阶段熔断标志
        self._traced_llm_cache: Any = None

        # Memory：默认按项目新建（持久化到 .state/memory/）
        if memory is not None:
            self.memory = memory
        else:
            from agent.memory import MemoryLayer

            self.memory = MemoryLayer(self.project_dir)

        # Planner：默认构造 PlannerAgent（惰性 LLM）
        self.planner = planner
        # Writer：默认用 Phase 1 的 AgenticWriteWorkflow
        self.writer_workflow = writer_workflow
        # Editor：默认构造 EditorAgent（惰性 LLM）
        self.editor = editor
        # Evaluator：默认构造 EvaluatorAgent
        self.evaluator = evaluator
        self.state_machine = StateMachine(self.project_dir)

    # ---------------------------------------------------------------- 构造默认 Agent
    def run(self) -> PipelineResult:
        import time

        result = PipelineResult(engine="Agentic")

        # G4 记录起始时间（墙钟计时）
        self._start_time = time.monotonic()

        # F-8：记录本轮 token 用量基线（窗口差值口径，见 __init__ 注释）
        try:
            from agent.core.llmops.trace import get_tracer as _get_tracer

            self._usage_baseline = float(
                _get_tracer().totals().get("tokens_total", 0) or 0
            )
        except Exception:  # noqa: BLE001 - 基线快照失败退化为 0（等同旧行为）
            self._usage_baseline = 0.0

        # ---- 规划一致性守护（缺口 A/C，2026-09-06）：写前对账 + 不变量 fail-fast ----
        # 覆盖直接调用 pipeline 的入口（Web / 测试）；CLI autowrite 已另行前置校验。
        try:
            from agent.workflows.pipeline.plan_consistency import prepare_for_write

            _fatal = prepare_for_write(self.project_dir, console=self.console)
            if _fatal:
                result.blocked = True
                result.block_reason = "规划校验失败：" + "；".join(_fatal)
                self.console.print(f"[red]✗ {result.block_reason}[/red]")
                self._emit_failure("plan_block", result.block_reason, severity="error")
                self._finalize_cost(result)
                self._finalize_g9(result)
                return result
        except Exception as e:  # noqa: BLE001 - 守护自身异常不阻断既有流程
            self.console.print(f"[yellow]规划一致性守护异常（忽略）：{e}[/yellow]")  # noqa: SILENT_DEGRADE

        # 1) 规划（若提供 planner 且尚未有计划文件）
        planner = self._ensure_planner()
        if planner is not None and self.brief:
            try:
                plan = planner.run(self.brief)
                result.planned = True
                self.console.print(
                    f"[cyan]Planner 产出 Master Plan：{plan.total_chapters} 章目标，"
                    f"{len(plan.character_skeleton)} 角色，"
                    f"{len(plan.episode_tree)} 剧情弧[/cyan]"
                )
            except Exception as e:  # noqa: BLE001 - 规划失败不阻断写作
                self.console.print(f"[yellow]Planner 失败（{e}），跳过规划[/yellow]")  # noqa: SILENT_DEGRADE

        # 1.5) 自主模式引导：复用真实 M1~M4 自主规划（G3）。失败不阻断写章；
        #      关键前置失败则安全退出（不进入半残写章，拍板 #2）。
        # G9：规划开始事件（规划前插桩）
        self._emit_event("planning")
        try:
            self._ensure_setting_set()
        except Exception as e:  # noqa: BLE001
            self.console.print(f"[red]自主规划阶段异常：{e}[/red]")
            result.blocked = True
            result.block_reason = f"规划阶段异常：{e}"
            # ---- G9：failure 事件（规划异常，error）----
            self._emit_failure("plan_block", f"规划阶段异常：{e}", severity="error")
            self._finalize_cost(result)
            self._finalize_g9(result)
            return result
        if getattr(self, "_plan_blocked", False):
            result.blocked = True
            result.block_reason = self._plan_block_reason
            if getattr(self, "_plan_tripped", False):
                result.tripped = True
            self.console.print(
                f"[red]自主规划关键前置失败，已安全退出（不进入写章）："
                f"{self._plan_block_reason}[/red]"
            )
            # ---- G9：failure 事件（规划阻塞，error）----
            self._emit_failure("plan_block", self._plan_block_reason, severity="error")
            self._finalize_cost(result)
            self._finalize_g9(result)
            return result

        # 2) 逐章写作 + 编辑 + 记忆回写
        writer = self._ensure_writer()
        editor = self._ensure_editor()
        target = self._resolve_target()
        start_total = self._current_total()

        wrote = 0
        # F-7：双条件终止——本地 wrote 兜底 + state 校验。
        # 并发写（双进程）时 state.total_written 可能被竞争滞后，单靠
        # `while _current_total() < target` 会多写章节（五灵破归档事故：目标 5 章出 6 章）；
        # 本地 wrote 达到「本轮应写章数」即停，不依赖外部 state 的唯一性。
        while wrote < target - start_total and self._current_total() < target:
            # ── G10 检查点顺序（拍板 4）：_check_budget → G8 决策点 → 降档判定 → 事件 ──
            # 1) G4 熔断检查点（判定逻辑零改动，727-774）
            budget_over = self._check_budget("write_chapter")
            # 2) G8 决策点（顺序前提至降档判定之前；逻辑零改动）
            if self.ending_gate:
                self._maybe_enter_ending_mode()
            if self.mainline_gate:
                self._maybe_advance_mainline(target)
            # 3) G10 降档判定：超限且非最低档 → 自动降档继续；否则走既有 G4 熔断
            if budget_over and not self._maybe_downgrade_tier():
                result.tripped = True
                result.block_reason = "Token 预算超限或墙钟超时熔断（写章阶段）"
                self.console.print(f"[red]✗ 熔断中止：{result.block_reason}[/red]")
                # ---- G9：failure 事件（写章熔断，warn；既有 break 语义零改动）----
                self._emit_failure("budget_trip", result.block_reason, severity="warn")
                break
            # 4) 进度回调 + 事件（chapter_start 含成本字段，经 bus cost_provider）
            self._emit_progress("writing", self._current_total(), target)
            # ---- G9：chapter_start（插桩顺序：检查点 → G8 决策点 → 进度回调 → 事件）----
            self._chapter_t0 = time.monotonic()
            self._emit_event(
                "chapter_start",
                chapter=self._current_total() + 1,
                total=target,
                subline=str((self.state_machine.progress or {}).get("current_subline", "") or ""),
                pressure_stage=self._prev_pressure_stage(),
            )
            try:
                # LLMOps：章级用量窗口起点（覆盖本章生成+质检+可能的重写全程）
                from agent.core.llmops.trace import usage_snapshot as _usage_snapshot

                _u0 = _usage_snapshot()
                wf_result = writer.run()
            except Exception as e:  # noqa: BLE001 - 单章失败不阻断，记录并跳出
                self.console.print(f"[red]写章失败：{e}[/red]")
                # ---- G9：failure 事件（写章失败，error）----
                self._emit_failure("write_chapter", str(e), severity="error")
                # 配额/鉴权类致命错误（403 配额耗尽、欠费、鉴权失败等）：
                # 冷却重试必然复现，立即终止本批并提示人工处理
                if is_fatal_provider_error(e):
                    result.block_reason = (
                        "Provider 配额/鉴权类致命错误，已停止写作："
                        "请充值、切换模型或检查 API Key 后重跑"
                    )
                    result.tripped = True
                    self.console.print(f"[red]✗ {result.block_reason}[/red]")
                    self._emit_failure(
                        "provider_fatal", result.block_reason, severity="error"
                    )
                    break
                # 弹性重试（2026-09-05）：provider 间歇性 403/429 风暴（免费池过载）
                # 会在数分钟内连续打死整批章节。冷却等待后重试 1 次，
                # 风暴短窗可自然恢复；仍失败才终止本批（等价旧行为）。
                retry_ok = False
                if self._chapter_retry_wait_s > 0:
                    self.console.print(
                        f"[yellow]冷却 {self._chapter_retry_wait_s}s 后重试本章一次"
                        f"（provider 瞬时故障保护）...[/yellow]"
                    )
                    time.sleep(self._chapter_retry_wait_s)
                    try:
                        wf_result = writer.run()
                        retry_ok = True
                        self.console.print("[green]本章重试成功，继续批次[/green]")
                    except Exception as e2:  # noqa: BLE001
                        self.console.print(f"[red]本章重试仍失败：{e2}[/red]")
                        self._emit_failure(
                            "write_chapter_retry", str(e2), severity="warn"
                        )  # noqa: SILENT_DEGRADE
                if not retry_ok:
                    break  # noqa: SILENT_DEGRADE
            ch_num = int(getattr(wf_result, "chapter_num", 0))
            ch_text = str(getattr(wf_result, "chapter_text", ""))
            ch_title = str(getattr(wf_result, "chapter_title", ""))

            # 编辑并联审查：一致性硬门禁（BLOCK 冲突自动打回重写 1 次，与 Guardrails 门禁同级）
            try:
                edit = editor.review(ch_text)
            except Exception:  # noqa: BLE001
                edit = None  # noqa: SILENT_DEGRADE
            if edit is not None:
                block_conflicts = [c for c in edit.conflicts if c.severity == "block"]
                if block_conflicts:
                    critique = self._format_edit_critique(block_conflicts)
                    self.console.print(
                        f"[yellow]第 {ch_num} 章一致性硬门禁未过"
                        f"（{len(block_conflicts)} 项阻断）：自动打回 Writer 重写 1 次[/yellow]"
                    )
                    try:
                        # F-8：章号锚定——重写必须是"同一章"（否则溢出成下一章）
                        wf_result = writer.run(rewrite_hint=critique, chapter_num=ch_num)
                        ch_text = str(getattr(wf_result, "chapter_text", ""))
                        ch_title = str(getattr(wf_result, "chapter_title", ""))
                        ch_num = int(getattr(wf_result, "chapter_num", ch_num))
                        edit2 = editor.review(ch_text)
                        still = [c for c in (edit2.conflicts or []) if c.severity == "block"] if edit2 else []
                        if not still:
                            self.console.print(f"[green]第 {ch_num} 章重写后通过一致性门禁[/green]")
                        else:
                            self._flag_chapter_quality(ch_num, [c.to_dict() for c in still], ch_text)
                            self.console.print(
                                f"[red]第 {ch_num} 章重写后仍 {len(still)} 项阻断未过："
                                f"已标记告警并保留该章（不阻断写作）[/red]"
                            )
                    except Exception as re_e:  # noqa: BLE001 - 重写失败降级为告警
                        self._flag_chapter_quality(ch_num, [c.to_dict() for c in block_conflicts], ch_text)
                        self.console.print(
                            f"[red]第 {ch_num} 章一致性门禁重写失败（{re_e}）："
                            f"已标记告警并保留该章[/red]"
                        )  # noqa: SILENT_DEGRADE
                elif edit.conflicts:
                    self.console.print(
                        f"[yellow]第 {ch_num} 章编辑提示：{len(edit.conflicts)} 项一致性警告[/yellow]"
                    )
                elif edit.frozen_violations:
                    self.console.print(
                        f"[yellow]第 {ch_num} 章编辑提示："
                        f"{len(edit.frozen_violations)} 项冻结违例[/yellow]"
                    )

            # Phase 5 · Guardrails 门禁（advisory 提示 / block 硬门禁）。未注入则跳过。
            if self.guardrails is not None:
                try:
                    if str(self.gate_mode).lower() == "block":
                        gr = self.guardrails.gate(ch_text, mode="block")
                        # G6（B5-3 修复）：收集 ai_flavor 命中明细（block 命中同样进报告）
                        for v in gr.violations:
                            if v.get("rule_id") == "ai_flavor":
                                self._guardrail_hits.append({
                                    "chapter": ch_num,
                                    "rule_id": v.get("rule_id"),
                                    "severity": v.get("severity"),
                                    "message": v.get("message", ""),
                                })
                        if not gr.passed:
                            # ---- G14（决策①）：硬门禁命中 → 自动打回重写 1 次 ----
                            critique = self._format_guardrail_critique(gr.violations)
                            self.console.print(
                                f"[yellow]第 {ch_num} 章硬门禁未过（{len(gr.violations)} 项）："
                                f"自动打回 Writer 重写 1 次[/yellow]"
                            )
                            try:
                                # F-8：章号锚定——重写必须是"同一章"（否则溢出成下一章）
                                wf_result = writer.run(rewrite_hint=critique, chapter_num=ch_num)
                                ch_text = str(getattr(wf_result, "chapter_text", ""))
                                ch_title = str(getattr(wf_result, "chapter_title", ""))
                                ch_num = int(getattr(wf_result, "chapter_num", ch_num))
                                gr2 = self.guardrails.gate(ch_text, mode="block")
                                # 重写后若仍命中，收集明细
                                for v in gr2.violations:
                                    if v.get("rule_id") == "ai_flavor":
                                        self._guardrail_hits.append({
                                            "chapter": ch_num,
                                            "rule_id": v.get("rule_id"),
                                            "severity": v.get("severity"),
                                            "message": v.get("message", ""),
                                        })
                                if gr2.passed:
                                    self.console.print(
                                        f"[green]第 {ch_num} 章重写后通过门禁[/green]"
                                    )
                                else:
                                    # 第二次仍不过 → 降级告警标记（决策①：不终止流水线）
                                    self._flag_chapter_quality(
                                        ch_num, gr2.violations, ch_text
                                    )
                                    self.console.print(
                                        f"[red]第 {ch_num} 章重写后仍 {len(gr2.violations)} 项未过："
                                        f"已标记告警并保留该章（不阻断写作）[/red]"
                                    )
                            except Exception as re_e:  # noqa: BLE001 - 重写失败降级为告警
                                self._flag_chapter_quality(ch_num, gr.violations, ch_text)
                                self.console.print(
                                    f"[red]第 {ch_num} 章门禁重写失败（{re_e}）："
                                    f"已标记告警并保留该章[/red]"
                                )  # noqa: SILENT_DEGRADE
                    else:
                        gr = self.guardrails.check(ch_text)
                        if not gr.passed:
                            self.console.print(
                                f"[red]第 {ch_num} 章护栏告警："
                                f"{len(gr.errors)} 项错误（{', '.join(v.rule_id for v in gr.errors)}）[/red]"
                            )
                        # G6（B5-3 修复）：收集 ai_flavor 命中明细（warn 标红进报告，不只 console）
                        for v in gr.violations:
                            if v.rule_id == "ai_flavor":
                                self._guardrail_hits.append({
                                    "chapter": ch_num,
                                    "rule_id": v.rule_id,
                                    "severity": v.severity,
                                    "message": v.message,
                                })
                except Exception:  # noqa: BLE001
                    pass  # noqa: SILENT_DEGRADE

            # ---- G14：章节落盘后增量更新全书指纹库（决策③：存 .state/ 下）----
            try:
                if self.guardrails is not None:
                    self.guardrails.register_fingerprints(ch_num, ch_text)
                    from agent.core.quality.guardrails import save_fingerprints
                    fp_path = self.project_dir / ".state" / "chapter_fingerprints.json"
                    save_fingerprints(self.guardrails.fingerprint_db, fp_path)
            except Exception:  # noqa: BLE001 - 指纹持久化失败不阻断
                pass  # noqa: SILENT_DEGRADE

            # 记忆回写
            try:
                self.memory.record_chapter(ch_num, ch_title, facts=[])
            except Exception:  # noqa: BLE001
                pass  # noqa: SILENT_DEGRADE

            wrote += 1
            self.console.print(f"[green]✓ 第 {ch_num} 章完成（{len(ch_text)} 字）[/green]")
            # ---- LLMOps：本章用量（窗口差值，含重写）+ 控制台回显 ----
            try:
                from agent.core.llmops.trace import usage_snapshot as _usage_snapshot

                _u1 = _usage_snapshot()
                _usage = {
                    "llm_calls": max(0, _u1["calls"] - _u0["calls"]),
                    "tokens_in": max(0, _u1["tokens_in"] - _u0["tokens_in"]),
                    "tokens_out": max(0, _u1["tokens_out"] - _u0["tokens_out"]),
                }
                _usage["tokens_total"] = _usage["tokens_in"] + _usage["tokens_out"]
                if _usage["llm_calls"] > 0:
                    self.console.print(
                        f"[cyan]📊 第 {ch_num} 章用量："
                        f"in {_usage['tokens_in']:,} / out {_usage['tokens_out']:,} tokens"
                        f"（{_usage['llm_calls']} 次调用）[/cyan]"
                    )
            except Exception:  # noqa: BLE001 - 用量统计失败不影响写作
                _usage = None  # noqa: SILENT_DEGRADE
            # ---- G9：chapter_done（words/quality_passed/chapter_elapsed_s/eta_s/用量）----
            self._emit_event(
                "chapter_done",
                chapter=ch_num,
                words=len(ch_text),
                quality_passed=bool(getattr(wf_result, "quality_passed", True)),
                chapter_elapsed_s=round(time.monotonic() - self._chapter_t0),
                eta_s=self._compute_eta_s(target),
                usage=_usage,
            )
            # 防御：避免无限循环（target 必须有限且 writer 必须推进进度）
            if self._current_total() <= start_total + wrote - 1 and wrote >= 1:
                # 进度未推进（stub/异常）→ 强制退出，避免死循环
                if self._current_total() == start_total:
                    self.console.print("[red]写章未推进进度，终止流水线[/red]")
                    break

        result.chapters_written = wrote
        result.final_chapter = self._current_total()

        # G4: 熔断后跳过评测（拍板 #5）
        if result.tripped:
            self.console.print("[red]✗ 熔断已触发，跳过评测直接返回[/red]")
            # ---- G9：failure 事件（熔断跳过评测，warn）----
            self._emit_failure("budget_trip", result.block_reason, severity="warn")
            self._finalize_cost(result)
            self._finalize_g9(result)
            return result

        # 3) 评测 + 自动回溯修复
        if self.eval_enabled:
            # G4 熔断检查点：评测前
            if self._check_budget("eval"):
                result.tripped = True
                result.block_reason = "Token 预算超限或墙钟超时熔断（评测阶段）"
                self.console.print(f"[red]✗ 熔断中止：{result.block_reason}[/red]")
                # ---- G9：failure 事件（评测熔断，warn）----
                self._emit_failure("budget_trip", result.block_reason, severity="warn")
                self._finalize_cost(result)
                self._finalize_g9(result)
                return result
            self._emit_progress("evaluating", 0, 100)
            # ---- G9：评测开始事件 ----
            self._emit_event("evaluating")
            evaluator = self._ensure_evaluator()

            def rewriter(chapter_nums: list[int]) -> None:
                # 回退后逐章重写（writer.run 按进度写下一章）。
                # 把上一轮体检失败项编译成针对性提示传入，避免盲目重写反复不达标。
                w = self._ensure_writer()
                ev = self._ensure_evaluator()
                hint = build_rewrite_hint(getattr(ev, "last_failed_report", None), chapter_nums)
                for ch in chapter_nums:
                    try:
                        w.run(rewrite_hint=hint)
                    except Exception as e:  # noqa: BLE001
                        raise RuntimeError(f"重写第 {ch} 章失败：{e}")

            try:
                report = evaluator.evaluate_with_repair(rewriter)
            except Exception as e:  # noqa: BLE001
                self.console.print(f"[red]评测失败：{e}[/red]")
                # ---- G9：failure 事件（评测失败，warn；不阻断继续）----
                self._emit_failure("eval", str(e), severity="warn")
                report = None  # noqa: SILENT_DEGRADE

            if report is not None:
                # G6：B5 结果写入 PipelineResult.guardrails + health_report.ai_flavor 子块（拍板 4）
                ai_flavor_hits = list(self._guardrail_hits)
                result.guardrails = {
                    "mode": str(self.gate_mode),
                    "ai_flavor_hits": ai_flavor_hits,
                    "ai_flavor_count": len(ai_flavor_hits),
                    "blocked": result.blocked,
                }
                if ai_flavor_hits:
                    report.ai_flavor = {
                        "mode": str(self.gate_mode),
                        "hits": ai_flavor_hits,
                        "count": len(ai_flavor_hits),
                    }
                result.health_report = report.to_dict()
                result.escalated = report.escalated
                result.escalated_reason = report.escalated_reason
                # ---- G9：failure 事件（上报人工，warn）----
                if result.escalated:
                    self._emit_failure("eval", result.escalated_reason, severity="warn")
                self.console.print(report.to_markdown())
                try:
                    self.memory.log("eval", "全书体检完成", report.to_dict())
                except Exception:  # noqa: BLE001
                    pass  # noqa: SILENT_DEGRADE

        # ---- G7（拍板 4）：成本汇总（纯复用，异常降级占位不阻断）----
        self._finalize_cost(result)
        # ---- G8（拍板 6）：主线推进/结局模式摘要（纯读 state，异常降级占位不阻断）----
        self._finalize_g8(result)
        # ---- G9（补充边界 3）：运行摘要 + failures 进 PipelineResult + 最终落盘 ----
        self._finalize_g9(result)
        return result
