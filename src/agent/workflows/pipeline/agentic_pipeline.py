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
from agent.core.infra.degrade import degrade
# ★ 六维门禁阈值唯一真源（纪律 #19）：本类构造参数默认值此前手写 60/40
from agent.core.quality.golden_policy import SIX_DIM_FLOOR, SIX_DIM_PASS_LINE
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
        # ★ 2026-09-18 修复：规划前置闸的阶段级豁免必须**透传到本层**。
        #   pipeline 在 run() 内会**再跑一次** prepare_for_write（覆盖 Web/直调入口），
        #   此前该次调用没带 allow_stage_level ⇒ CLI 层的豁免被自己否掉
        #   （实测：日志先「已按 --allow-stage-level 显式豁免并留痕」，
        #   紧接着「✗ 规划校验失败」⇒ 0 章写出）。**声明了却走不通的豁免＝陷阱**。
        plan_gate_allow_stage_level: bool = False,
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
        appeal_threshold: int = SIX_DIM_PASS_LINE,
        appeal_window: int = 1,
        # ---- G6 新增参数 ----
        golden_three_gate: bool = True,
        golden_three_threshold: int = SIX_DIM_PASS_LINE,
        golden_three_floor: int = SIX_DIM_FLOOR,
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
        # ---- B1（2026-09-10）：滚动体检周期（章）。>0 时每写满 N 章跑一次体检，
        # 不过则立即中断本批，避免长批次（如 --chapters 190）因循环未结束而永不体检。
        # 原设计「体检仅在循环结束后执行」在 web 端绝对目标 + 中途打断场景下会完全失效
        # （2026-09-10 事故：ch156-178 共 23 章零体检）。
        rolling_eval_every: int = 5,
    ) -> None:
        self.project_dir = Path(project_dir)
        self.llm = llm_client
        self.tier = tier
        self.brief = brief
        self.target_chapters = target_chapters
        self.eval_enabled = eval_enabled
        self.rollback_window = rollback_window
        self.max_rollback_attempts = max_rollback_attempts
        # P1（2026-09-12）：滚动体检熔断原因。非空 ⇒ 本批已因「修复未收敛」停批，
        # 最终结果必须 escalated 上报人工（否则外层会再起一批盲写，回退死循环）。
        self._rolling_escalation_reason: str = ""
        self.guardrails = guardrails
        self.gate_mode = gate_mode
        self._plan_gate_allow_stage_level = bool(plan_gate_allow_stage_level)
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
        # B1：滚动体检周期（章）；0=关闭（仅保留循环结束后的终审体检）
        self.rolling_eval_every = max(0, int(rolling_eval_every))
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
        # ---- 门禁可观测性（2026-09-12）：fail-open 不能变成质量黑洞 ----
        # _gate_blind_streak：连续"门禁失明"（Editor/Guardrails/滚动体检调用异常，
        #   异常被降级为放行）计数；连续超限说明质检基建持续故障，继续写=裸奔。
        # _consecutive_flagged：连续"告警留章"章数（门禁打回重写后仍不过）。
        # _gate_escalation_reason：非空 = 门禁侧要求停批上报人工（escalated 语义）。
        self._gate_blind_streak = 0
        self._consecutive_flagged = 0
        self._gate_escalation_reason = ""

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

    def _preflight_plan_gate(self) -> list[str]:
        """写前规划一致性校验（缺口 A/C）：返回致命错误（空 = 通过）。

        ★ 抽成独立方法是为了**可被红线行为级断言**：pipeline 会在 ``run()``
        内再跑一次本校验（覆盖 Web / 直调入口），它必须与 CLI 层**同语义**地
        透传 ``allow_stage_level`` —— 否则 CLI 的显式豁免会被本层自己否掉
        （2026-09-18 实测：日志先「已按 --allow-stage-level 显式豁免并留痕」，
        紧接着「✗ 规划校验失败」⇒ 0 章写出）。
        """
        from agent.workflows.pipeline.plan_consistency import prepare_for_write

        return prepare_for_write(
            self.project_dir,
            console=self.console,
            allow_stage_level=getattr(self, "_plan_gate_allow_stage_level", False),
        )

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
            self._usage_baseline = 0.0  # noqa: SILENT_DEGRADE

        # ---- 规划一致性守护（缺口 A/C，2026-09-06）：写前对账 + 不变量 fail-fast ----
        # 覆盖直接调用 pipeline 的入口（Web / 测试）；CLI autowrite 已另行前置校验。
        try:
            _fatal = self._preflight_plan_gate()
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
            flags_before = len(self._quality_flags)
            try:
                edit = editor.review(ch_text)
            except Exception as e:  # noqa: BLE001 - 失明显性化：降级放行但计入连续熔断
                edit = None
                degrade(
                    "pipeline.gate_blind.editor_review",
                    "Editor 一致性审查调用异常，本章放行（计连续失明）",
                    e,
                )
                self._note_gate_blind("editor_review", e)
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
                        # 联合复检（2026-09-12）：Editor 打回的重写稿此前只复查 Editor，
                        # Guardrails 从未见过新稿——修好一道门可能弄坏另一道，必须双检。
                        gr_joint = None
                        if self.guardrails is not None and str(self.gate_mode).lower() == "block":
                            try:
                                gr_joint = self.guardrails.gate(ch_text, mode="block")
                            except Exception as g_e:  # noqa: BLE001 - 失明计入熔断
                                degrade(
                                    "pipeline.gate_blind.guardrails_joint_recheck",
                                    "联合复检 Guardrails 调用异常，本章放行（计连续失明）",
                                    g_e,
                                )
                                self._note_gate_blind("guardrails_joint_recheck", g_e)
                            else:
                                for v in gr_joint.violations:
                                    if v.get("rule_id") == "ai_flavor":
                                        self._guardrail_hits.append({
                                            "chapter": ch_num,
                                            "rule_id": v.get("rule_id"),
                                            "severity": v.get("severity"),
                                            "message": v.get("message", ""),
                                        })
                        joint_violations = [] if not gr_joint or gr_joint.passed else list(gr_joint.violations)
                        if not still and not joint_violations:
                            self.console.print(f"[green]第 {ch_num} 章重写后通过一致性门禁[/green]")
                        else:
                            combined = ([c.to_dict() for c in still] if still else []) + joint_violations
                            self._flag_chapter_quality(ch_num, combined, ch_text)
                            self.console.print(
                                f"[red]第 {ch_num} 章重写后联合复检仍不通过"
                                f"（一致性阻断 {len(still)} 项 / 护栏 {len(joint_violations)} 项）："
                                f"已标记告警并保留该章[/red]"
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
                    # ---- 问题债务登记（2026-09-12）：WARN 级一致性警告此前只打
                    # console 即丢弃——"当时觉得问题不大"的问题没有任何登记，
                    # 几十章后同类问题复发时无记忆、修复代价巨大。确认放行的
                    # 警告统一登记为 watch 债务，写时注入提醒（销账闭环见
                    # issue_debt.py）。登记失败仅告警不阻断。----
                    try:
                        from agent.core.story.issue_debt import KIND_WATCH, IssueDebtStore

                        _store = IssueDebtStore(self.project_dir).load()
                        for _c in edit.conflicts:
                            _store.add(
                                KIND_WATCH,
                                constraint=(
                                    f"第{ch_num}章一致性警告（{_c.rule_id}）："
                                    f"{_c.description}"
                                ),
                                registered_ch=ch_num,
                                # 结构化来源规则（2026-09-15）：reverify() 靠它重跑
                                # 规则判定债务是否已随规则修复而失效 —— 缺它则债务
                                # 只进不出，历史误报会永久注入 writer 与规划者。
                                rule_id=str(getattr(_c, "rule_id", "") or ""),
                            )
                        _store.save()
                    except Exception as debt_e:  # noqa: BLE001 - 登记失败不阻断
                        self.console.print(
                            f"[yellow]⚠ 问题债务登记失败（不影响本章）：{debt_e}[/yellow]"
                        )  # noqa: SILENT_DEGRADE
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
                                # 联合复检（2026-09-12）：Guardrails 打回的重写稿从未被
                                # Editor 见过——重写可能引入新的一致性冲突，必须双检。
                                still_editor = []
                                try:
                                    edit_joint = editor.review(ch_text)
                                    if edit_joint is not None:
                                        still_editor = [
                                            c.to_dict()
                                            for c in (edit_joint.conflicts or [])
                                            if c.severity == "block"
                                        ]
                                except Exception as e_j:  # noqa: BLE001 - 失明计入熔断
                                    degrade(
                                        "pipeline.gate_blind.editor_joint_recheck",
                                        "联合复检 Editor 调用异常，本章放行（计连续失明）",
                                        e_j,
                                    )
                                    self._note_gate_blind("editor_joint_recheck", e_j)
                                if gr2.passed and not still_editor:
                                    self.console.print(
                                        f"[green]第 {ch_num} 章重写后通过门禁[/green]"
                                    )
                                else:
                                    # 第二次仍不过 → 降级告警标记（决策①：不终止流水线）
                                    combined = list(gr2.violations) + still_editor
                                    self._flag_chapter_quality(
                                        ch_num, combined, ch_text
                                    )
                                    self.console.print(
                                        f"[red]第 {ch_num} 章重写后联合复检仍不通过"
                                        f"（护栏 {len(gr2.violations)} 项 / 一致性阻断 {len(still_editor)} 项）："
                                        f"已标记告警并保留该章[/red]"
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
                except Exception as e:  # noqa: BLE001 - 失明显性化：降级放行但计入连续熔断
                    degrade(
                        "pipeline.gate_blind.guardrails_gate",
                        "Guardrails 门禁调用异常，本章放行（计连续失明）",
                        e,
                    )
                    self._note_gate_blind("guardrails_gate", e)

            # ---- 章级门禁计数复位：本章未被标记告警 → 连续告警清零；门禁正常
            #      工作（Editor 出了结论）→ 失明连击清零 ----
            #      2026-09-16（普查 §三.B1）：清零同样**落盘**，否则跨运行累计只增不减。
            if len(self._quality_flags) == flags_before:
                if self._consecutive_flagged:
                    self._gate_blind_save(flagged_streak=0, last_where="flag_ok")
                self._consecutive_flagged = 0
            if edit is not None:
                self._note_gate_ok()
            # ---- 写时质检失明接入熔断（2026-09-16，普查 §三.C1，拍板「两条都补」）----
            #      agentic_write 的写时九项质检/金三失败会写 gate_skipped 标记，
            #      但此前**不进失明计数**（最频繁的门禁环节失明无人知），批末也无补检。
            #      放在计数复位之后：本章门禁正常则清零，写时质检失明确凿则再计一次。
            self._scan_gate_skipped(ch_num)

            # ---- G14：章节落盘后增量更新全书指纹库（决策③：存 .state/ 下）----
            try:
                if self.guardrails is not None:
                    self.guardrails.register_fingerprints(ch_num, ch_text)
                    from agent.core.quality.guardrails import save_fingerprints
                    fp_path = self.project_dir / ".state" / "chapter_fingerprints.json"
                    save_fingerprints(self.guardrails.fingerprint_db, fp_path)
            except Exception:  # noqa: BLE001 - 指纹持久化失败不阻断
                pass  # noqa: SILENT_DEGRADE

            # 记忆回写（2026-09-12 修复"语义记忆名存实亡"）：从连续性账本提取
            # 本章事实回写语义层——此前恒传 facts=[]，MemoryLayer 从不吸收章节
            # 事实（本回写同时是 P2-13 时序三元组记忆查询的前置数据源）。
            try:
                chapter_facts: list[str] = []
                chapter_summary = ch_title
                try:
                    from agent.core.continuity import ContinuityLedgerStore

                    _led = ContinuityLedgerStore(self.project_dir)
                    _led.load()
                    from agent.core.continuity.ledger import commit_id_matches

                    chapter_facts = [
                        f"{f.domain}/{f.subject_id}/{f.field} = {f.value}（{f.evidence}）"
                        for f in _led.ledger.facts
                        if commit_id_matches(f.source_commit_id, ch_num)
                    ][:12]
                    _h = _led.ledger.latest_handoff()
                    if _h is not None and _h.chapter == ch_num and _h.summary:
                        chapter_summary = _h.summary
                except Exception:  # noqa: BLE001 - 账本读取失败仅降级为标题摘要
                    pass  # noqa: SILENT_DEGRADE
                # ---- 实体名册自动登记（长线一致性第一期尾巴）：本章出场/退场/
                # 道具易主从连续性账本 facts 确定性同步（无 LLM）；失败显性降级，
                # 名册缺失只影响注入丰富度，不阻断写作。----
                try:
                    from agent.core.story.entity_ledger import sync_entities_from_facts
                    from agent.core.continuity.ledger import commit_id_matches

                    _ch_fact_objs = [
                        f for f in _led.ledger.facts
                        if commit_id_matches(f.source_commit_id, ch_num)
                    ]
                    sync_entities_from_facts(self.project_dir, _ch_fact_objs, ch_num)
                except Exception as sync_e:  # noqa: BLE001
                    degrade(
                        "pipeline.entity_sync",
                        "实体名册自动登记失败，本章出场信息未入册",
                        sync_e,
                    )
                # ---- 资源账本同步（设计稿第二期）：同一 facts 流确定性记账 ----
                try:
                    from agent.core.story.resource_ledger import sync_resources_from_facts

                    sync_resources_from_facts(self.project_dir, _ch_fact_objs, ch_num)
                except Exception as res_e:  # noqa: BLE001
                    degrade(
                        "pipeline.resource_sync",
                        "资源账本同步失败，本章资源变动未入账",
                        res_e,
                    )
                self.memory.record_chapter(
                    ch_num, ch_title, summary=chapter_summary, facts=chapter_facts
                )
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

            # ---- B1（2026-09-10）：滚动体检检查点 ----
            # 每写满 rolling_eval_every 章就地体检一次；不过则立即中断本批，
            # 避免长批次因循环未结束（绝对目标 + 中途打断）而完全跳过体检。
            if (
                self.eval_enabled
                and self.rolling_eval_every > 0
                and wrote > 0
                and wrote % self.rolling_eval_every == 0
                and self._current_total() < target  # 批末仍会走完整体检，此处不重复
            ):
                if not self._rolling_eval_checkpoint():
                    self.console.print(
                        "[yellow]⚠ 滚动体检不达标：中断本批，待修复后继续[/yellow]"
                    )
                    break

            # ---- 门禁侧熔断（2026-09-12）：连续门禁失明 / 连续告警留章 → 停批 ----
            if self._gate_escalation_reason:
                self._emit_failure("gate_escalation", self._gate_escalation_reason, severity="block")
                break

        result.chapters_written = wrote
        result.final_chapter = self._current_total()

        # G4: 熔断后跳过评测（拍板 #5）
        # 2026-09-16（登记单 `20260916_闸门信号可达性普查` §三.A2，拍板 A「置位即短路」）：
        # 旧实现只认 `result.tripped`（token/墙钟预算），而**滚动体检熔断**与**门禁失明熔断**
        # 同样在批内 break，却要等到 run() 收尾（:879/:884）才被折入 result ⇒
        # 「判定已记录、但没有改变控制流」：熔断后仍白跑整段批末评测 + 回退重试。
        # 实测（灵荒薪传 2026-09-16 对 `rollback_budget.json` 回查）熔断 14:32:53 以
        # consecutive=4 置位，之后仍涨到 7（又跑 1h06m / 3 次回退 / 重写 5 章 / +0.5M token）。
        # 现三合一：任一熔断置位 ⇒ 立即短路批末评测，并以 escalated 收尾上报人工。
        _trip_reason = ""
        _trip_kind = "budget_trip"
        if result.tripped:
            _trip_reason = result.block_reason
        elif self._rolling_escalation_reason:
            _trip_reason = self._rolling_escalation_reason
            _trip_kind = "eval"
        elif self._gate_escalation_reason:
            _trip_reason = self._gate_escalation_reason
            _trip_kind = "gate_escalation"
        if _trip_reason:
            self.console.print(
                f"[red]✗ 熔断已触发，跳过评测直接返回：{_trip_reason}[/red]"
            )
            # 非预算类熔断必须以 escalated 收尾（否则外层会再起一批盲写）。
            if not result.tripped and not result.escalated:
                result.escalated = True
                result.escalated_reason = _trip_reason
            # ---- G9：failure 事件（熔断跳过评测，warn）----
            self._emit_failure(_trip_kind, _trip_reason, severity="warn")
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
            # P1（2026-09-12）：回退后定向重写回调收口到 _make_rewriter，
            # 与滚动体检检查点共用同一实现（此前检查点只回退不重写）。
            rewriter = self._make_rewriter()

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
                # ---- 基建级故障分层（2026-09-15）：全部 LLM 评分维降级 = 网关不可达，
                # 不是内容不达标。"连不上网关"生成的失败明细一旦落盘，会被下一批当
                # 「上轮教训」注入写作提示（错误放大）；回退预算也会被基建故障吃满。
                _infra_down = bool(getattr(report, "eval_infra_unavailable", False))
                if _infra_down:
                    _infra_err = RuntimeError(
                        getattr(report, "escalated_reason", "") or "全部 LLM 评分维降级"
                    )
                    self.console.print(
                        "[yellow]⚠ 批末体检基建不可用（全部 LLM 评分维 confidence=0）："
                        "本轮不计质量失败、不触发回退、不落失败教训（等待 LLM 网关恢复）"
                        "[/yellow]"
                    )
                    self._note_gate_blind("batch_eval_infra", _infra_err)
                # 体检教训落盘（反馈闭环·缺口2修复 2026-09-08）：失败明细持久化，
                # 供下一轮**新章**写作注入「上轮教训」；体检通过时写空 failures 清零。
                if not _infra_down:
                    try:
                        from agent.core.quality.eval_lessons import save_eval_lessons

                        save_eval_lessons(self.project_dir, report)
                    except Exception:  # noqa: BLE001 - 教训落盘失败不阻断
                        pass  # noqa: SILENT_DEGRADE
                result.escalated = report.escalated
                result.escalated_reason = report.escalated_reason
                # ---- P1（2026-09-12）：跨批回退预算 ----
                # 批末体检同样计入持久化计数：通过即归零；发生回退即 +1，连续超上限
                # 则强制 escalated（旧实现只靠单次循环内的 max_rollback_attempts，
                # 每批新建 Evaluator 即归零 → 31 次回退也没人上报人工）。
                try:
                    budget = self._rollback_budget()
                    # ---- 统一对账点（2026-09-18）：先记账，再决定是否翻篇 ----
                    # 见 ``_reconcile_rollback_ledger``：回退销毁发生在
                    # ``evaluate_with_repair()`` **内部**，而这里拿到的是它**复评后**的
                    # report ⇒ 旧实现「``verified_pass`` 时直接 reset、否则才在
                    # ``not _infra_down`` 分支里记账」会把已经发生的销毁**连记都没记就
                    # 抹平**（reset 覆盖计数），与滚动检查点同一缺陷（登记单
                    # ``20260918_回退销毁与记账分支脱钩``）。
                    # 改为无条件先对账：
                    #   记账依据 = 独立账本是否前进（动作事实），与 gate 象限无关；
                    #   归零依据 = 通过 **且** 本轮确无回退 **且** 证据可信。
                    counted = self._reconcile_rollback_ledger(report, "批末体检")
                    verified = bool(getattr(report, "verified_pass", report.overall_pass))
                    if counted == 0 and verified and not _infra_down:
                        # 对账异常（-1）不归零：未知不是"确无回退"（一号病的镜像）。
                        # ``_infra_down`` 时不归零：基建不可用时证据不可信，保守保留计数。
                        budget.reset()
                    if budget.tripped() and not result.escalated:
                        result.escalated = True
                        result.escalated_reason = budget.reason_text()
                        self._emit_failure(
                            "eval", result.escalated_reason, severity="block"
                        )
                except Exception as e:  # noqa: BLE001 - 预算是护栏，读写异常不阻断收尾
                    degrade("pipeline.rollback_budget", "回退预算读写异常，跳过熔断上报", e)
                # ---- G9：failure 事件（上报人工，warn）----
                if result.escalated:
                    self._emit_failure("eval", result.escalated_reason, severity="warn")
                self.console.print(report.to_markdown())
                try:
                    self.memory.log("eval", "全书体检完成", report.to_dict())
                except Exception:  # noqa: BLE001
                    pass  # noqa: SILENT_DEGRADE

        # ---- P1（2026-09-12）：滚动体检熔断（修复未收敛）必须上报人工 ----
        # 检查点已就地定向重写并复评，仍不达标 → 停批。这里把原因带到最终结果，
        # 外层（compose / autowrite）据此以 escalated 收尾，不再无限重起新批盲写。
        if self._rolling_escalation_reason and not result.escalated:
            result.escalated = True
            result.escalated_reason = self._rolling_escalation_reason

        # ---- 门禁侧熔断上报（2026-09-12）：连续失明/连续告警留章 → escalated ----
        if self._gate_escalation_reason and not result.escalated:
            result.escalated = True
            result.escalated_reason = self._gate_escalation_reason

        # ---- L2 批末反思（设计稿 §9 第二期）：每批 1 次，作战笔记经
        # batch_replan 摘要注入下一批复规划；失败显性降级不阻断收尾 ----
        if result.chapters_written > 0:
            try:
                from agent.core.quality.batch_reflection import record_batch_reflection

                if record_batch_reflection(self.project_dir, batch_end_ch=result.final_chapter):
                    self.console.print(
                        "[cyan]批末反思完成（作战笔记落盘 .state/batch_reflection.json）[/cyan]"
                    )
            except Exception as ref_e:  # noqa: BLE001
                degrade("pipeline.batch_reflection", "批末反思调用异常", ref_e)

            # ---- M26 批末监督（2026-09-19 接线，修复 SupervisorEngine 零消费点）----
            # 只读确定性扫描（零 LLM）；advisory 语义：告警+留痕+SUPERVISOR_ALERT
            # 事件，**不阻断不 escalated** —— checker 阈值未经真实项目标定
            # （纪律 #13/#26：动作强度≤判据可达性），阻断需先跑阈值分位表。
            self._run_supervisor_batch_end(result)

            # ---- S6 跨章集成检查（2026-09-20 接线）----
            # 十项跨章**确定性**指标（节奏连续 / 伏笔账龄 / 配角停滞 / 实体漂移 /
            # 章末钩子重复 / 超短章…）此前**只有 CLI 手动入口**，而批末自动跑的
            # evaluator 七维只判"不崩"正确性 ⇒「读者可见质量」自动覆盖为 0。
            # advisory：**只告警不阻断**（第五部分纪律：小说要创造性生长，
            # 照搬软件 blocking 会一拦全冻；事后观测面不得越权阻断）。
            self._run_book_checkup_batch_end(result)

        # ---- G7（拍板 4）：成本汇总（纯复用，异常降级占位不阻断）----
        self._finalize_cost(result)
        # ---- G8（拍板 6）：主线推进/结局模式摘要（纯读 state，异常降级占位不阻断）----
        self._finalize_g8(result)
        # ---- G9（补充边界 3）：运行摘要 + failures 进 PipelineResult + 最终落盘 ----
        self._finalize_g9(result)
        return result
