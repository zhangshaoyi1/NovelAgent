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
        gate_mode: str = "advisory",
        console: Console | None = None,
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

        # 自主规划（G3）状态：关键前置失败则阻塞，交给 run() 安全退出、不进写章。
        self._plan_blocked = False
        self._plan_block_reason = ""
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
            )
        return self.planner

    def _ensure_writer(self) -> WriterWorkflow:
        if self.writer_workflow is None:
            from agent.workflows.agentic_write import AgenticWriteWorkflow

            self.writer_workflow = AgenticWriteWorkflow(
                self.project_dir, llm_client=self.llm, console=self.console, tier=self.tier,
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
            self.evaluator = EvaluatorAgent(
                self.project_dir,
                console=self.console,
                rollback_window=self.rollback_window,
                max_rollback_attempts=self.max_rollback_attempts,
                quality_targets=qt or None,
                score_fn=score_fn,
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
                if not gen_ok:
                    self._plan_blocked = True
                    self._plan_block_reason = "M14 架构生成失败（关键前置），已安全退出，不进入写章。"
                    return
            conf_ok = self._safe_step(
                key=True, name="M14 架构确认",
                fn=lambda: m14.with_confirm_yes(True).confirm(),
            ).ok
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
                fn=lambda: wf(M3OutlineWorkflow).run(),
                degrade=self._write_placeholder_outline,
            )
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

    # ---------------------------------------------------------------- 主流程
    def run(self) -> PipelineResult:
        result = PipelineResult(engine="Agentic")

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
        try:
            self._ensure_setting_set()
        except Exception as e:  # noqa: BLE001
            self.console.print(f"[red]自主规划阶段异常：{e}[/red]")
            result.blocked = True
            result.block_reason = f"规划阶段异常：{e}"
            return result
        if getattr(self, "_plan_blocked", False):
            result.blocked = True
            result.block_reason = self._plan_block_reason
            self.console.print(
                f"[red]自主规划关键前置失败，已安全退出（不进入写章）："
                f"{self._plan_block_reason}[/red]"
            )
            return result

        # 2) 逐章写作 + 编辑 + 记忆回写
        writer = self._ensure_writer()
        editor = self._ensure_editor()
        target = self._resolve_target()
        start_total = self._current_total()

        wrote = 0
        while self._current_total() < target:
            try:
                wf_result = writer.run()
            except Exception as e:  # noqa: BLE001 - 单章失败不阻断，记录并跳出
                self.console.print(f"[red]写章失败：{e}[/red]")
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
                        if not gr.passed:
                            self.console.print(
                                f"[red]第 {ch_num} 章硬门禁未过："
                                f"{len(gr.violations)} 项违例，已拒绝发布并终止流水线[/red]"
                            )
                            result.blocked = True
                            result.block_reason = "; ".join(
                                v.get("message", v.get("rule_id", "")) for v in gr.violations
                            )
                            break  # 硬门禁：拒绝发布非合规内容，交由人工/修订
                    else:
                        gr = self.guardrails.check(ch_text)
                        if not gr.passed:
                            self.console.print(
                                f"[red]第 {ch_num} 章护栏告警："
                                f"{len(gr.errors)} 项错误（{', '.join(v.rule_id for v in gr.errors)}）[/red]"
                            )
                except Exception:  # noqa: BLE001
                    pass

            # 记忆回写
            try:
                self.memory.record_chapter(ch_num, ch_title, facts=[])
            except Exception:  # noqa: BLE001
                pass

            wrote += 1
            self.console.print(f"[green]✓ 第 {ch_num} 章完成（{len(ch_text)} 字）[/green]")
            # 防御：避免无限循环（target 必须有限且 writer 必须推进进度）
            if self._current_total() <= start_total + wrote - 1 and wrote >= 1:
                # 进度未推进（stub/异常）→ 强制退出，避免死循环
                if self._current_total() == start_total:
                    self.console.print("[red]写章未推进进度，终止流水线[/red]")
                    break

        result.chapters_written = wrote
        result.final_chapter = self._current_total()

        # 3) 评测 + 自动回溯修复
        if self.eval_enabled:
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
                report = None

            if report is not None:
                result.health_report = report.to_dict()
                result.escalated = report.escalated
                result.escalated_reason = report.escalated_reason
                self.console.print(report.to_markdown())
                try:
                    self.memory.log("eval", "全书体检完成", report.to_dict())
                except Exception:  # noqa: BLE001
                    pass

        return result
