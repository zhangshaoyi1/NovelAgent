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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from rich.console import Console

from agent.core.llm_client import LLMClient
from agent.core.state_machine import Event, State, StateMachine, TRANSITIONS
from agent.core.setting_manager import SettingManager
from agent.core.confirmation import is_architecture_confirmed
import frontmatter


@dataclass
class PipelineResult:
    """全流程自主结果。"""

    planned: bool = False
    chapters_written: int = 0
    final_chapter: int = 0
    health_report: Optional[dict[str, Any]] = None
    escalated: bool = False
    escalated_reason: str = ""
    blocked: bool = False
    block_reason: str = ""
    engine: str = "Agentic"
    # G4 新增字段
    tripped: bool = False  # 熔断标志
    schema_degraded: bool = False  # Schema 降级标志
    # G6 新增字段（B5-3 修复：Guardrails 结果进结构化结果，供 --json 审计）
    guardrails: Optional[dict[str, Any]] = None
    # G7 新增字段（成本透明，拍板 4）：run 收尾填充；--no-cost 时 CLI 置 None
    cost: Optional[dict[str, Any]] = None
    # ---- G8 新增字段（主线推进 + 结局模式，拍板 6）：run 收尾填充；关闭开关置 None ----
    mainline: Optional[dict[str, Any]] = None
    ending: Optional[dict[str, Any]] = None
    # ---- G9 新增字段（进度事件流 + 失败自助恢复，拍板 2/5/6）：run 收尾填充 ----
    progress_file: Optional[str] = None        # progress.json 绝对路径；--no-progress 置 null
    failures: list[dict[str, Any]] = field(default_factory=list)
    stream: Optional[dict[str, Any]] = None    # 渲染元信息；--no-stream 置 null
    summary: Optional[dict[str, Any]] = None   # build_run_summary 结果（运行摘要）

    def to_dict(self) -> dict[str, Any]:
        return {
            "planned": self.planned,
            "chapters_written": self.chapters_written,
            "final_chapter": self.final_chapter,
            "health_report": self.health_report,
            "escalated": self.escalated,
            "escalated_reason": self.escalated_reason,
            "blocked": self.blocked,
            "block_reason": self.block_reason,
            "engine": self.engine,
            "tripped": self.tripped,
            "schema_degraded": self.schema_degraded,
            "guardrails": self.guardrails,
            # ---- G7（只增不删）：成本汇总 ----
            "cost": self.cost,
            # ---- G8（只增不删）：主线推进 + 结局模式 ----
            "mainline": self.mainline,
            "ending": self.ending,
            # ---- G9（只增不删）：进度事件流 + 失败自助恢复 ----
            "progress_file": self.progress_file,
            "failures": self.failures,
            "stream": self.stream,
            "summary": self.summary,
        }


# 注入的 writer 工作流：有 run() -> 结果对象（含 chapter_num/chapter_text 等），
# 且会自行推进状态机进度并落盘。测试时可替换为 stub。
WriterWorkflow = Any
EditorLike = Any
EvaluatorLike = Any
PlannerLike = Any


@dataclass
class _PlanStepResult:
    """单步规划结果（供 ``_safe_step`` 返回）。"""

    ok: bool
    value: Any = None


# 状态规范链（用于 ``_advance_state_to`` 单向推进）。
_CANON = [
    State.INIT, State.CONFIGURING, State.DISCUSSING, State.ARCHITECTING,
    State.ARCH_CONFIRMED, State.OUTLINING, State.CHARACTER_DESIGN,
    State.WRITING, State.PAUSED, State.COMPLETED, State.ARCH_REVISION,
]
_EVENTS = [
    Event.START, Event.DISCUSS, Event.GENERATE_ARCHITECTURE,
    Event.CONFIRM_ARCHITECTURE, Event.GENERATE_OUTLINE,
    Event.DESIGN_CHARACTERS, Event.WRITE,
]


def build_rewrite_hint(report: Any, chapter_nums: list[int]) -> str:
    """把上一轮全书体检的失败项编译成写给 Writer 的针对性修正提示。

    回溯重写若不带反馈，Writer 只会盲目重生成、极易再次不达标而触发无谓上报。
    这里把未达标维度、回溯原因与重写章节区间浓缩为可读指令，让重写「对症」。
    """
    if report is None:
        return ""
    failed = [d for d in getattr(report, "dimensions", []) or [] if not d.passed]
    lo = chapter_nums[0] if chapter_nums else "?"
    hi = f"–{chapter_nums[-1]}" if chapter_nums else ""
    lines = [
        "【全书体检未达标 · 针对性重写要求】",
        f"以下章节被回退并重写：第 {lo}{hi} 章。",
    ]
    if failed:
        lines.append("上轮未达标维度（请在本轮重写中重点修正）：")
        for d in failed:
            arrow = "≥" if d.direction == ">=" else "≤"
            lines.append(
                f"- {d.label}（{d.name}）：实测 {d.value} {arrow} 合格线 {d.threshold}"
            )
    reason = getattr(report, "escalated_reason", "") or ""
    if reason:
        lines.append(f"上下文：{reason}")
    plan = getattr(report, "repair", None)
    if plan is not None:
        r = getattr(plan, "reason", "") or ""
        if r:
            lines.append(f"回溯原因：{r}")
    lines.append(
        "请在重写时针对以上维度改善（如补全伏笔回收、修复人设/设定冲突、"
        "提升连贯与追读节奏、控制注水），并保持与世界观/角色档案一致。"
    )
    return "\n".join(lines)


# G10（拍板 3）：预算档位降档方向 quality→balanced→economy（模块级，供测试引用）
_DOWNGRADE_ORDER: list[str] = ["quality", "balanced", "economy"]


class AgenticPipelineWorkflow:
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
        llm_client: LLMClient | None = None,
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
        # G11：风格模仿 + 写作方法模板（透传给 writer/planner/outline；默认开）
        self.style_enabled = bool(style_enabled)
        self.style_file = style_file
        self.method_enabled = bool(method_enabled)
        # G12：爽点剧本/情绪目标注入（透传给 writer；默认开）
        self.payoff_enabled = bool(payoff_enabled)
        # G6：写章循环 Guardrails 命中收集（B5-3 修复：结果进报告，不只 console）
        self._guardrail_hits: list[dict[str, Any]] = []

        # ---- G9：事件总线（未订阅 on_event / progress_file=None 时零落盘开销）----
        from agent.core.events import ProgressEventBus

        self._event_bus = ProgressEventBus(
            on_event=on_event,
            progress_file=progress_file,
            cost_provider=self._current_cost_fields,  # G10（拍板 2）：每事件附加成本字段
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
    def _ensure_planner(self) -> PlannerLike:
        if self.planner is None:
            from agent.agents.planner_agent import PlannerAgent

            self.planner = PlannerAgent(
                self.project_dir, llm_client=self.llm, memory=self.memory,
                console=self.console,
                method_enabled=self.method_enabled,  # G11：写作方法模板注入
            )
        return self.planner

    def _ensure_writer(self) -> WriterWorkflow:
        if self.writer_workflow is None:
            from agent.workflows.agentic_write import AgenticWriteWorkflow

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
            from agent.agents.editor_agent import EditorAgent

            self.editor = EditorAgent(
                self.project_dir, llm_client=self.llm, console=self.console, memory=self.memory,
            )
        return self.editor

    def _ensure_evaluator(self) -> EvaluatorLike:
        if self.evaluator is None:
            from agent.agents.evaluator_agent import EvaluatorAgent

            # 质量目标优先取自 MasterPlan
            qt: dict[str, float] = {}
            try:
                plan = self._ensure_planner().load_plan()
                if plan is not None:
                    qt = plan.quality_targets.model_dump()
            except Exception:  # noqa: BLE001
                pass
            score_fn = None
            try:
                from agent.core.reader_appeal import ReaderAppealScorer

                # 默认接真 LLM 评分（B1）；LLM 不可用时 scorer 内部自动降级为离线安全默认。
                score_fn = ReaderAppealScorer(llm_client=self.llm).score
            except Exception:  # noqa: BLE001
                score_fn = None
            appeal_scorer = None
            if self.appeal_gate:
                try:
                    from agent.core.reader_appeal import ReaderAppealScorer

                    appeal_scorer = ReaderAppealScorer(llm_client=self.llm)
                except Exception:  # noqa: BLE001
                    appeal_scorer = None
            # G6：B4 golden_scorer 复用同一六维评分器实例（评前三章与评末章可共用，拍板 §12-3）
            golden_scorer = appeal_scorer if self.golden_three_gate else None
            self.evaluator = EvaluatorAgent(
                self.project_dir,
                console=self.console,
                rollback_window=self.rollback_window,
                max_rollback_attempts=self.max_rollback_attempts,
                quality_targets=qt or None,
                score_fn=score_fn,
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
            pass
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
            pass
        return 100

    # ---------------------------------------------------------------- 设定集自检/引导（G3）
    def _ensure_setting_set(self) -> None:
        """自主模式引导（G3 薄壳）：委托 ``_autoplan_full_book`` 串联真实
        M1→M2→M14→M3→M4 复用规划工作流，使下游 M5 ``_load_context`` 不再因缺文件
        而抛错。保留公开签名（``run`` 仍调用），幂等、不崩。
        """
        self._plan_blocked = False
        self._plan_block_reason = ""
        self._autoplan_full_book()

    # ---------------------------------------------------------------- G3 自主规划编排器
    def _autoplan_full_book(self) -> None:
        """自主规划整本书：串联真实 M1→M2→M14→M3→M4，复用同一
        ``llm_client / state_machine / SettingManager / console``（设计 §1.1）。

        每步「产物已存在且有效则跳过」（幂等，§2.2），从任意半残态续跑均安全。
        关键前置（M1 world / M14 架构确认）失败 → 置 ``_plan_blocked`` 并提前
        return（安全退出，不进写章，拍板 #2）；非关键（M2/M3/M4）失败 →
        ``_safe_step`` 重试（默认 2 次）后降级占位继续。
        """
        from agent.workflows.m1_config import M1ConfigWorkflow, M1Input
        from agent.workflows.m2_discuss import M2DiscussWorkflow, M2Input
        from agent.workflows.m14_architecture import M14ArchitectureWorkflow
        from agent.workflows.m3_outline import M3OutlineWorkflow
        from agent.workflows.m4_character import M4CharacterWorkflow

        # G4 进度回调（T4）：规划阶段
        self._emit_progress("planning", 0, 100)

        # 自动模式：显式设定 auto 档（设计 §9 #5），使下游交互默认跳过。
        # 注意：必须先 load() 再 set_mode，否则会用内存默认 INIT 覆盖既有状态/进度。
        try:
            self.state_machine.load()
            self.state_machine.set_mode("auto")
        except Exception:  # noqa: BLE001
            pass

        sm = SettingManager(self.project_dir)
        llm = self._traced_llm()

        def wf(cls: type, **extra: Any) -> Any:
            return cls(
                self.project_dir,
                llm_client=llm,
                setting_manager=sm,
                state_machine=self.state_machine,
                console=self.console,
                **extra,
            )

        # ---- M1 配置（关键前置）----
        if (self.project_dir / "world.md").exists():
            self._advance_state_to(State.DISCUSSING)
        else:
            m1_input = M1Input(
                title=(self.brief[:30] or "未命名作品").strip(),
                scope="long",
                genre=os.getenv("G3_GENRE", "xiuxian"),
                story_core=self.brief or "（未提供创作思路，请基于默认值自主生成）",
            )
            ok = self._safe_step(
                key=True, name="M1 世界观生成",
                fn=lambda: wf(M1ConfigWorkflow).run(m1_input),
            ).ok
            # G4 熔断检查点：规划每步后
            if self._check_budget("plan_step"):
                self._plan_tripped = True
                self._plan_blocked = True
                self._plan_block_reason = "Token 预算超限或墙钟超时熔断（规划阶段）"
                return
            if not ok:
                self._plan_blocked = True
                self._plan_block_reason = "M1 世界观生成失败（关键前置），已安全退出，不进入写章。"
                return
            self._advance_state_to(State.DISCUSSING)

        # ---- M2 脉络讨论（非关键，非交互）----
        if (self.project_dir / "discussion.md").exists():
            self._advance_state_to(State.ARCHITECTING)
        else:
            m2_input = M2Input(
                max_rounds=int(os.getenv("G3_M2_ROUNDS", "1")),
                preset_answers=[
                    self.brief or "（请基于世界观直接收敛主线与关键冲突）"
                ],
            )
            self._safe_step(
                key=False, name="M2 脉络讨论",
                fn=lambda: wf(M2DiscussWorkflow).run(m2_input),
            )
            # G4 熔断检查点：规划每步后
            if self._check_budget("plan_step"):
                self._plan_tripped = True
                self._plan_blocked = True
                self._plan_block_reason = "Token 预算超限或墙钟超时熔断（规划阶段）"
                return
            self._advance_state_to(State.ARCHITECTING)

        # ---- M14 架构生成 + 确认（关键前置）----
        if is_architecture_confirmed(self.project_dir):
            self._advance_state_to(State.ARCH_CONFIRMED)
        else:
            m14 = wf(M14ArchitectureWorkflow)
            if not (self.project_dir / "architecture.md").exists():
                gen_ok = self._safe_step(
                    key=True, name="M14 架构生成",
                    fn=lambda: m14.generate(),
                ).ok
                # G4 熔断检查点：规划每步后
                if self._check_budget("plan_step"):
                    self._plan_tripped = True
                    self._plan_blocked = True
                    self._plan_block_reason = "Token 预算超限或墙钟超时熔断（规划阶段）"
                    return
                if not gen_ok:
                    self._plan_blocked = True
                    self._plan_block_reason = "M14 架构生成失败（关键前置），已安全退出，不进入写章。"
                    return
            conf_ok = self._safe_step(
                key=True, name="M14 架构确认",
                fn=lambda: m14.with_confirm_yes(True).confirm(),
            ).ok
            # G4 熔断检查点：规划每步后
            if self._check_budget("plan_step"):
                self._plan_tripped = True
                self._plan_blocked = True
                self._plan_block_reason = "Token 预算超限或墙钟超时熔断（规划阶段）"
                return
            if not conf_ok:
                self._plan_blocked = True
                self._plan_block_reason = "M14 架构确认失败（关键前置），已安全退出，不进入写章。"
                return
            self._advance_state_to(State.ARCH_CONFIRMED)

        # ---- M3 大纲生成（非关键）----
        if (self.project_dir / "outline.md").exists():
            self._advance_state_to(State.OUTLINING)
        else:
            self._safe_step(
                key=False, name="M3 大纲生成",
                fn=lambda: wf(M3OutlineWorkflow, method_enabled=self.method_enabled).run(),  # G11：方法模板注入
                degrade=self._write_placeholder_outline,
            )
            # G4 熔断检查点：规划每步后
            if self._check_budget("plan_step"):
                self._plan_tripped = True
                self._plan_blocked = True
                self._plan_block_reason = "Token 预算超限或墙钟超时熔断（规划阶段）"
                return
            self._advance_state_to(State.OUTLINING)

        # ---- M4 角色设计（非关键）----
        if self._m4_done():
            self._advance_state_to(State.CHARACTER_DESIGN)
        else:
            self._safe_step(
                key=False, name="M4 角色设计",
                fn=lambda: wf(M4CharacterWorkflow).run(),
                degrade=self._write_placeholder_characters,
            )
            # G4 熔断检查点：规划每步后
            if self._check_budget("plan_step"):
                self._plan_tripped = True
                self._plan_blocked = True
                self._plan_block_reason = "Token 预算超限或墙钟超时熔断（规划阶段）"
                return
            self._advance_state_to(State.CHARACTER_DESIGN)

        # 规划完成：推进到 WRITING（对齐拍板 #6），写章循环在 CHARACTER_DESIGN/WRITING 下运行。
        self._advance_state_to(State.WRITING)

    def _m4_done(self) -> bool:
        """判断 M4 产物是否已齐备（幂等跳过的依据，§2.2）。"""
        chars_dir = self.project_dir / "characters"
        if chars_dir.exists() and any(chars_dir.glob("*.md")):
            return True
        if (self.project_dir / "protagonist_route.md").exists():
            return True
        return False

    def _advance_state_to(self, target: State) -> None:
        """状态调和器：沿规范链单向推进到 ``target``（处理「产物存在但状态落后」的续跑态）。

        已越过 target（如 WRITING 续写场景）则保持不变，绝不降级状态。
        """
        self.state_machine.load()
        for _ in range(len(_EVENTS) + 1):
            cur = self.state_machine.state
            if cur == target or _CANON.index(cur) >= _CANON.index(target):
                break
            advanced = False
            for ev in _EVENTS:
                if (cur, ev) in TRANSITIONS:
                    try:
                        self.state_machine.transition(ev)
                        self.state_machine.save()
                        advanced = True
                        break
                    except ValueError:
                        break
            if not advanced:
                break

    def _safe_step(
        self,
        *,
        key: bool,
        name: str,
        fn: Callable[[], Any],
        retries: int = 2,
        degrade: Callable[[], None] | None = None,
    ) -> _PlanStepResult:
        """包装单步规划调用（失败不阻断，拍板 #2）。

        Args:
            key: True=关键前置（耗尽重试后安全退出，置 ``_plan_blocked``）；
                 False=非关键（耗尽重试后调用 ``degrade`` 占位并继续）。
            fn: 单步执行函数。
            retries: 统一重试上限（默认 2，即最多尝试 3 次）。
            degrade: 非关键最终失败时的降级占位回调（可选）。
        """
        last: Exception | None = None
        for attempt in range(retries + 1):
            try:
                return _PlanStepResult(ok=True, value=fn())
            except Exception as e:  # noqa: BLE001
                last = e
                self.console.print(
                    f"[yellow]⚠ 规划步骤[{name}] 第{attempt + 1}次失败：{e}[/yellow]"
                )
                self._alert_cost(name)
        if key:
            return _PlanStepResult(ok=False)
        if degrade is not None:
            try:
                degrade()
            except Exception:  # noqa: BLE001
                pass
        return _PlanStepResult(ok=False)

    # ---------------------------------------------------------------- 降级占位（非关键失败）
    def _write_placeholder_outline(self) -> None:
        """M3 耗尽重试后的降级：写最小 outline.md，使 M4._load_outline 不崩。"""
        f = self.project_dir / "outline.md"
        f.write_text(
            "---\nsublines: []\n---\n\n# 故事大纲（自主规划降级占位）\n\n"
            "## 故事简介\n（大纲生成失败，已降级占位；请手动 /outline 补生成）\n",
            encoding="utf-8",
        )

    def _write_placeholder_characters(self) -> None:
        """M4 耗尽重试后的降级：用 M4 模板渲染最小占位角色集，使 G2 Evaluator 有对象可读。"""
        from agent.workflows.m4_character import M4CharacterWorkflow

        wf = M4CharacterWorkflow(
            self.project_dir,
            llm_client=self._traced_llm(),
            setting_manager=SettingManager(self.project_dir),
            state_machine=self.state_machine,
            console=self.console,
        )
        placeholder = [{
            "name": "主角（自主规划占位）",
            "role": "protagonist",
            "identity": "（占位）待规划补全",
            "core_motivation": "（占位）",
            "arc": {"start": "（占位）", "end": "（占位）"},
            "language_fingerprint": {
                "catchphrase": "", "sentence_style": "",
                "vocabulary": "", "banned_words": [],
            },
            "relations": "（占位）",
        }]
        try:
            title = ""
            try:
                title = (
                    SettingManager(self.project_dir).load_world()["metadata"].get("title", "")
                )
            except Exception:  # noqa: BLE001
                title = ""
            wf._render_characters(placeholder, title)
            wf._render_graph({})
            wf._render_foreshadows([])
            wf._render_golden_finger({})
            wf._render_route({})
        except Exception:  # noqa: BLE001
            pass

    # ---------------------------------------------------------------- P1-2 成本可观测 + 单步超时
    def _traced_llm(self) -> Any:
        """返回包着 ``self.llm`` 的 ``TracedLLMClient``（注入同 tracer，供 M1~M4 调用）。"""
        if self._traced_llm_cache is None:
            from agent.core.llmops import TraceStore, TracedLLMClient, set_tracer

            try:
                set_tracer(TraceStore(self.project_dir))
            except Exception:  # noqa: BLE001
                pass
            self._traced_llm_cache = TracedLLMClient(self.llm, model="creative-strong")
        return self._traced_llm_cache

    def _alert_cost(self, step: str) -> None:
        """成本告警（仅提示不拦截，拍板 #3；硬熔断归 G4）。"""
        try:
            from agent.core.llmops.cost import CostModel
            from agent.core.llmops.trace import get_tracer

            tracer = get_tracer()
            totals = tracer.totals()
            used = totals.get("tokens_total", 0)
            model = CostModel()
            msg = model.alert_if_over(used, "balanced", self._resolve_target())
            if msg:
                self.console.print(f"[yellow]{msg}（步骤：{step}）[/yellow]")
        except Exception:  # noqa: BLE001
            pass

    def _check_budget(self, step: str) -> bool:
        """检查预算/墙钟是否超限。

        G4 熔断检查：从 G3 _alert_cost 升级，复用 CostModel.baseline_tokens + get_tracer().totals()。

        Args:
            step: 检查点名称（用于日志）。

        Returns:
            True 表示超限，应熔断中止；False 表示预算内。
        """
        import time

        try:
            from agent.core.llmops.cost import CostModel
            from agent.core.llmops.trace import get_tracer

            tracer = get_tracer()
            totals = tracer.totals()
            used_tokens = totals.get("tokens_total", 0)

            # 1) Token 检查
            model = CostModel()
            _, token_limit = model.baseline_tokens(self._cost_tier, self._resolve_target())
            token_limit *= self._budget_margin

            if used_tokens > token_limit:
                self.console.print(
                    f"[red]✗ Token 预算超限熔断（{used_tokens/1_000_000:.2f}M > {token_limit/1_000_000:.2f}M）"
                    f"（步骤：{step}）[/red]"
                )
                return True

            # 2) 墙钟检查
            if self._max_time and self._start_time > 0:
                elapsed = time.monotonic() - self._start_time
                if elapsed > self._max_time:
                    self.console.print(
                        f"[red]✗ 墙钟超时熔断（{elapsed:.0f}s > {self._max_time}s）"
                        f"（步骤：{step}）[/red]"
                    )
                    return True

        except Exception:  # noqa: BLE001
            # 检查失败不阻断（避免熔断本身异常）
            pass

        return False

    # ================================================================
    # G10：写中成本视图 + 超预算自动降档（拍板 2/3/4，只增不改）
    # ================================================================
    def _current_cost_fields(self) -> dict[str, Any]:
        """G10（拍板 2）：当前成本视图（tokens_used/budget/remaining）。

        数据源与 _check_budget **同源**（不新造统计）：used = get_tracer().totals()["tokens_total"]；
        budget = baseline_tokens(tier, target)[1] * budget_margin；
        remaining = budget - used（保留原始差值，可为负；渲染层钳制 ≥0）。
        全 try/except：任何异常返回 {}（事件不带成本字段，不阻断发射）。
        """
        try:
            from agent.core.llmops.cost import CostModel
            from agent.core.llmops.trace import get_tracer

            tracer = get_tracer()
            used = float((tracer.totals().get("tokens_total", 0) or 0))
            model = CostModel()
            _, token_limit = model.baseline_tokens(self._cost_tier, self._resolve_target())
            budget = token_limit * self._budget_margin
            return {
                "tokens_used": used,
                "tokens_budget": budget,
                "tokens_remaining": budget - used,
            }
        except Exception:  # noqa: BLE001 - 成本视图失败降级 {}，不阻断
            return {}

    def _maybe_downgrade_tier(self) -> bool:
        """G10（拍板 3/4）：超预算降档判定（纯确定性，全 try/except 降级不阻断）。

        Returns:
            True  = 本次检查点**已降档** → 外层继续写章（不熔断）
            False = 不应/不能降档 → 外层走既有 G4 熔断（tripped=True + break）

        返回语义是零回归关键：仅「已降档」返回 True；其余（token 预算内 / 最低档 /
        未知档位 / 墙钟超时 / auto 关 / 异常）一律 False → 保证 test_breaker_*
        （monkeypatch _check_budget=True 模拟墙钟超时）仍走 G4 熔断。
        """
        try:
            if not self._auto_downgrade:
                return False  # --no-auto-downgrade：G4 行为
            from agent.core.llmops.cost import CostModel
            from agent.core.llmops.trace import get_tracer

            used = float(get_tracer().totals().get("tokens_total", 0) or 0)
            model = CostModel()
            _, token_limit = model.baseline_tokens(self._cost_tier, self._resolve_target())
            token_limit *= self._budget_margin
            if used <= token_limit:
                return False  # token 预算内 → 超限来自墙钟 → 熔断
            if self._cost_tier not in _DOWNGRADE_ORDER:
                return False  # 未知档位 → 熔断（保守）
            idx = _DOWNGRADE_ORDER.index(self._cost_tier)
            if idx >= len(_DOWNGRADE_ORDER) - 1:
                return False  # 最低档仍超限 → 既有 G4 熔断
            old_tier = self._cost_tier
            new_tier = _DOWNGRADE_ORDER[idx + 1]
            self._cost_tier = new_tier  # 仅改预算档位，writer tier 不动（拍板 3）
            self._emit_event(
                "cost_downgrade",
                from_tier=old_tier,
                to_tier=new_tier,
                reason=f"Token 预算超限：{used / 1_000_000:.2f}M > {token_limit / 1_000_000:.2f}M",
            )
            self.console.print(
                f"[yellow]已自动降档至 {new_tier}，后续按新预算续跑（此前 {old_tier}）[/yellow]"
            )
            return True  # 已降档 → 继续写章
        except Exception:  # noqa: BLE001 - 降档判定异常 → 保守走 G4 熔断，不静默超支
            return False

    def _emit_progress(self, phase: str, current: int, total: int) -> None:
        """触发进度回调（若订阅）。

        G4 进度可见性骨架：由 CLI 订阅并在 stderr 输出（避免污染 stdout JSON 信封）。

        Args:
            phase: 阶段名称（"planning"/"writing"/"evaluating"）。
            current: 当前进度。
            total: 总量。
        """
        if self.on_progress:
            try:
                self.on_progress(phase, current, total)
            except Exception:  # noqa: BLE001
                pass

    # ---------------------------------------------------------------- G9 事件发射（只读观察层）
    def _emit_event(self, type_: str, **fields: Any) -> None:
        """G9：发射事件（全 try/except，bus 内兜底；不阻断主流程）。"""
        try:
            self._event_bus.emit(type_, **fields)
        except Exception:  # noqa: BLE001
            pass

    def _emit_substage(self, partial: dict[str, Any]) -> None:
        """G9：writer 层子阶段事件入口（注入给 m5/agentic_write 的 event_emitter）。"""
        try:
            self._event_bus.emit_partial(partial)
        except Exception:  # noqa: BLE001
            pass

    def _emit_failure(self, step: str, reason: str, severity: str = "error") -> None:
        """G9：发射 failure 事件（含 next_steps；确定性零 LLM，不阻断主流程）。"""
        try:
            from agent.core.events import next_steps_for

            self._emit_event(
                "failure",
                step=step,
                reason=reason,
                severity=severity,
                next_steps=next_steps_for(step, self.project_dir),
            )
        except Exception:  # noqa: BLE001
            pass

    def _compute_eta_s(self, target: int) -> Optional[int]:
        """G9：ETA = 已写章平均耗时 × 剩余章数（拍板 4；无可计算时 None）。"""
        try:
            from agent.core.events import compute_eta_s

            return compute_eta_s(self._event_bus.events, target, self._current_total())
        except Exception:  # noqa: BLE001 - ETA 计算失败降级 None，不阻断
            return None

    def _prev_pressure_stage(self) -> str:
        """G9：best-effort 读上一章 frontmatter 的 pressure_stage（首章/缺失置 ""）。

        共享知识 #11：pipeline 写前无法精确得知本章压力阶段；取上一章
        chapters/ch{total_written}.md frontmatter（缺失/首章置 ""），
        精确值随 chapter_substage（writer 层 ctx 已知）出现。
        """
        try:
            total = self._current_total()
            if total <= 0:
                return ""
            f = self.project_dir / "chapters" / f"ch{total:03d}.md"
            if not f.exists():
                return ""
            post = frontmatter.load(f)
            return str(post.metadata.get("pressure_stage", "") or "")
        except Exception:  # noqa: BLE001 - 读取失败降级为空串，不阻断
            return ""

    def _finalize_g9(self, result: PipelineResult) -> None:
        """G9：填充 result.failures / progress_file / summary + done 事件 + 最终落盘。

        纯读 bus，降级占位不阻断（G3 哲学）；done 事件先入 events，
        build_run_summary 再聚合（含 done 的总耗时/结局标志）。
        """
        try:
            import time

            from agent.core.events import build_run_summary

            result.failures = [
                e for e in self._event_bus.events if e.get("type") == "failure"
            ]
            result.progress_file = (
                str(self._event_bus.progress_file)
                if self._event_bus.progress_file is not None
                else None
            )
            # done 事件（含 chapters_written/blocked/tripped/escalated/total_elapsed_s）
            self._emit_event(
                "done",
                chapters_written=result.chapters_written,
                blocked=result.blocked,
                tripped=result.tripped,
                escalated=result.escalated,
                total_elapsed_s=round(time.monotonic() - self._start_time),
            )
            result.summary = build_run_summary(self._event_bus.events, result)
            self._event_bus.flush(result.summary)
        except Exception:  # noqa: BLE001 - 摘要失败不阻断主流程（G3 哲学）
            pass

    # ---------------------------------------------------------------- 主流程
    def _finalize_cost(self, result: PipelineResult) -> None:
        """G7（拍板 4）：成本汇总（纯复用，异常降级占位不阻断）。"""
        try:
            from agent.core.llmops import build_cost_summary

            result.cost = build_cost_summary(
                self.project_dir, self._cost_tier, self._resolve_target()
            )
        except Exception:  # noqa: BLE001 - 成本汇总失败不阻断主流程（G3）
            result.cost = None

    # ---------------------------------------------------------------- G8 主线推进 + 结局模式
    def _maybe_enter_ending_mode(self, target: int) -> None:
        """结局模式触发（拍板 2：chapter > target*(1-ending_ratio)）。

        一旦进入不退出（拍板 4）：`progress.ending_mode=true` 持久化到 state.json，
        回溯重写（G1）后 M5/AgenticWrite 仍读到 true → 重写仍在结局模式。
        ending_ratio 已在 __init__ 钳制到 [0, 0.5]。
        """
        try:
            self.state_machine.load()
            progress = dict(self.state_machine.progress or {})
            if progress.get("ending_mode"):
                return  # 不退出（拍板 4）
            chapter = int(progress.get("total_written", 0)) + 1
            if chapter > target * (1 - self.ending_ratio):
                progress["ending_mode"] = True
                progress["ending_mode_at"] = chapter
                self.state_machine.progress = progress
                self.state_machine.save()
                self.console.print(
                    f"[cyan]进入结局模式：第 {chapter} 章起（最后 {int(self.ending_ratio * 100)}%）[/cyan]"
                )
                # ---- G9（补充边界 6）：记录型事件（只记录不反写，G8 语义零改动）----
                self._emit_event("ending_mode", chapter=chapter, ending_ratio=self.ending_ratio)
        except Exception:  # noqa: BLE001 - 触发异常降级不阻断写章
            pass

    def _maybe_advance_mainline(self, target: int) -> None:
        """每 mainline_window 章执行一次确定性支线推进决策（拍板 1/补充边界 2）。

        决策点写 `progress.current_subline` + `progress.mainline_visited`（去重），
        合并写入（load → dict 副本 → 改 → save），保留既有键。
        """
        try:
            self.state_machine.load()
            chapter = int((self.state_machine.progress or {}).get("total_written", 0)) + 1
            if chapter <= 1 or (chapter - 1) % self.mainline_window != 0:
                return  # 第 1 章前不决策；此后每 window 章一次
            from agent.workflows.mainline import decide_mainline_advance

            new_subline = decide_mainline_advance(
                self.project_dir, self.state_machine, self.mainline_window
            )
            if not new_subline:
                return
            progress = dict(self.state_machine.progress or {})
            from_subline = str(progress.get("current_subline", "") or "")  # G9：事件记录旧支线
            progress["current_subline"] = new_subline
            visited = list(progress.get("mainline_visited", []) or [])
            if new_subline not in visited:
                visited.append(new_subline)
            progress["mainline_visited"] = visited
            self.state_machine.progress = progress
            self.state_machine.save()
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
            pass

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
            result.ending = None

    def run(self) -> PipelineResult:
        import time

        result = PipelineResult(engine="Agentic")

        # G4 记录起始时间（墙钟计时）
        self._start_time = time.monotonic()

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
                self.console.print(f"[yellow]Planner 失败（{e}），跳过规划[/yellow]")

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
        while self._current_total() < target:
            # ── G10 检查点顺序（拍板 4）：_check_budget → G8 决策点 → 降档判定 → 事件 ──
            # 1) G4 熔断检查点（判定逻辑零改动，727-774）
            budget_over = self._check_budget("write_chapter")
            # 2) G8 决策点（顺序前提至降档判定之前；逻辑零改动）
            if self.ending_gate:
                self._maybe_enter_ending_mode(target)
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
                wf_result = writer.run()
            except Exception as e:  # noqa: BLE001 - 单章失败不阻断，记录并跳出
                self.console.print(f"[red]写章失败：{e}[/red]")
                # ---- G9：failure 事件（写章失败，error）----
                self._emit_failure("write_chapter", str(e), severity="error")
                break
            ch_num = int(getattr(wf_result, "chapter_num", 0))
            ch_text = str(getattr(wf_result, "chapter_text", ""))
            ch_title = str(getattr(wf_result, "chapter_title", ""))

            # 编辑并联审查（advisory；硬一致由 Evaluator 终审兜底）
            try:
                edit = editor.review(ch_text)
                if not edit.passed:
                    self.console.print(
                        f"[yellow]第 {ch_num} 章编辑提示："
                        f"{edit.block_count} 项阻断，{len(edit.frozen_violations)} 项冻结违例[/yellow]"
                    )
            except Exception:  # noqa: BLE001
                edit = None

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
                            self.console.print(
                                f"[red]第 {ch_num} 章硬门禁未过："
                                f"{len(gr.violations)} 项违例，已拒绝发布并终止流水线[/red]"
                            )
                            result.blocked = True
                            result.block_reason = "; ".join(
                                v.get("message", v.get("rule_id", "")) for v in gr.violations
                            )
                            # ---- G9：failure 事件（硬门禁拒绝，error；break 语义零改动）----
                            self._emit_failure("gate", result.block_reason, severity="error")
                            break  # 硬门禁：拒绝发布非合规内容，交由人工/修订
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
                    pass

            # 记忆回写
            try:
                self.memory.record_chapter(ch_num, ch_title, facts=[])
            except Exception:  # noqa: BLE001
                pass

            wrote += 1
            self.console.print(f"[green]✓ 第 {ch_num} 章完成（{len(ch_text)} 字）[/green]")
            # ---- G9：chapter_done（words/quality_passed/chapter_elapsed_s/eta_s）----
            self._emit_event(
                "chapter_done",
                chapter=ch_num,
                words=len(ch_text),
                quality_passed=bool(getattr(wf_result, "quality_passed", True)),
                chapter_elapsed_s=round(time.monotonic() - self._chapter_t0),
                eta_s=self._compute_eta_s(target),
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
                report = None

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
                    pass

        # ---- G7（拍板 4）：成本汇总（纯复用，异常降级占位不阻断）----
        self._finalize_cost(result)
        # ---- G8（拍板 6）：主线推进/结局模式摘要（纯读 state，异常降级占位不阻断）----
        self._finalize_g8(result)
        # ---- G9（补充边界 3）：运行摘要 + failures 进 PipelineResult + 最终落盘 ----
        self._finalize_g9(result)
        return result
