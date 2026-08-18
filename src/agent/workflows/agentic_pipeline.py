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

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from rich.console import Console

from agent.core.llm_client import LLMClient
from agent.core.state_machine import State, StateMachine
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

    # ---------------------------------------------------------------- 设定集自检/引导
    def _ensure_setting_set(self) -> None:
        """自主模式引导：若设定集/架构/支线缺失，从 brief + MasterPlan 自动补齐，
        使下游 M5 ``_load_context`` 不再因缺文件而抛错（修复 bug2）。

        仅补齐「缺失项」，已存在的设定/架构/支线不动；幂等、可安全重复调用。
        ``_load_context`` 仅在 world.md 与 subline.md 缺失时硬抛错，其余项
        （路线/关系网/角色/伏笔/压力曲线）缺失均优雅降级为空串——故最小补齐
        即 world.md + 一个主支线 + 已确认架构，并把状态机置为可写状态。
        """
        sm = SettingManager(self.project_dir)
        world = sm.load_world()
        arch_confirmed = is_architecture_confirmed(self.project_dir)
        sublines = sm.list_sublines()
        if world["exists"] and arch_confirmed and sublines:
            return  # 设定齐备，跳过

        # 取已有/刚生成的 MasterPlan 用于充实设定内容
        plan = None
        if self.brief:
            try:
                planner = self._ensure_planner()
                plan = planner.load_plan() or planner.run(self.brief)
            except Exception:  # noqa: BLE001
                plan = None

        if not world["exists"]:
            self._bootstrap_world(plan)
        if not arch_confirmed:
            self._bootstrap_architecture(plan)
        if not sm.list_sublines():
            self._bootstrap_subline(plan)

        # 状态机置为可写（CHARACTER_DESIGN/WRITING），并初始化进度
        self.state_machine.load()
        if self.state_machine.state not in (State.CHARACTER_DESIGN, State.WRITING):
            self.state_machine.state = State.WRITING
            self.state_machine.progress = self.state_machine.progress or {"total_written": 0}
            self.state_machine.save()
        self.console.print("[cyan]已自动补齐设定集/架构/支线（自主模式引导）[/cyan]")

    def _bootstrap_world(self, plan: Any) -> None:
        sm = SettingManager(self.project_dir)
        title = (getattr(plan, "title", "") or "") if plan else ""
        genre = (getattr(plan, "genre", "") or "modern") if plan else "modern"
        synopsis = (getattr(plan, "brief", "") or "（自主生成）") if plan else "（自主生成）"
        metadata = {
            "title": title or "未命名作品",
            "genre": genre or "modern",
            "scope": "autonomous",
            "style": {
                "tone": "热血/治愈",
                "pov": "第三人称有限视角",
                "rhythm": "紧凑",
                "chapter_length": 3000,
                "info_density": "中",
                "banned_elements": [],
            },
        }
        content = (
            "# 世界观设定（自主生成）\n\n"
            f"## 故事简介\n{synopsis}\n\n"
            "## 境界体系\n（依剧情需要设定）\n\n"
            "## 金手指登记\n（依剧情需要设定）\n"
        )
        sm.save_world(metadata, content)

    def _bootstrap_architecture(self, plan: Any) -> None:
        arch_file = self.project_dir / "architecture.md"
        arch_file.parent.mkdir(parents=True, exist_ok=True)
        post = frontmatter.Post(
            "# 故事架构（自主生成）\n\n由 AgenticPipeline 自主引导生成，已进入可写状态。\n",
            confirmed=True,
        )
        arch_file.write_text(frontmatter.dumps(post), encoding="utf-8")

    def _bootstrap_subline(self, plan: Any) -> None:
        sm = SettingManager(self.project_dir)
        name = "主线"
        goal = "故事主线推进"
        if plan is not None:
            arcs = getattr(plan, "episode_tree", None) or []
            if arcs:
                first = arcs[0]
                name = getattr(first, "name", "") or "主线"
                goal = getattr(first, "goal", "") or "主线推进"
        subline_id = "S01_主线"
        metadata = {"subline_name": name, "characters": []}
        content = (
            f"# 支线：{name}\n\n"
            f"## 支线目标\n{goal}\n\n"
            "## 剧集压力曲线\n"
            "| 阶段 | 章节 | 张力等级 |\n|---|---|---|\n| 铺垫 | 1-100 | 低 |\n\n"
            "## 出场角色\n（待角色档案补充）\n"
        )
        sm.save_subline(subline_id, metadata, content)

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

        # 1.5) 自主模式引导：补齐缺失的设定集/架构/支线（修复 bug2，使写章不再因缺文件抛错）
        self._ensure_setting_set()

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
