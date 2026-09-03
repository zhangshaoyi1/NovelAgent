"""Agentic 写章工作流（Phase 1 默认交付形态）

用 **自主写章 Agent（WriterAgent）** 替代 M5 的"硬编码七步生成 + 固定修订"，
同时**复用** M5 已验证的上下文加载、证据链、落盘与进度更新，确保产物与旧流水线
完全兼容（dashboard / export / 续写不受影响）。

流程
----
1. 状态门禁 + 架构确认（与 M5 一致）。
2. 复用 ``M5WriteChapterWorkflow._load_context`` 加载 7 步上下文（确定性、已验证）。
3. 用 M5 创作提示模板拼出 Writer 任务（保证风格/信息一致）。
4. ``WriterAgent`` 按 tier（auto/heavy/light）自主起草 + 质检门禁 + 修订。
   - 内环：Writer 在 Agentic Loop 中自主调工具、自评、提交。
   - 外环 Critic：复用 M5 九项 LLM 审稿作为门禁（与 M5 同等质量基线）。
5. 标题提取 / 证据链 / 落盘 / 进度更新（复用 M5 方法）。
6. 尽力而为地对新章做 RAG 索引（失败不阻断）。

质量承诺：外环门禁与 M5 同源（九项 LLM 审稿），故"质量不低于现 M5"；
"不崩"由规则层 + LLM 审稿双重把关，且修订循环保证不达标不出章。
"""

from __future__ import annotations

from agent.core.infra.prompt_manager import pm
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from rich.console import Console

from agent.client.gateway_adapter import create_gateway, chat_utility
from llmagent.gateway import Gateway
from agent.core.engine.state_machine import Event, State, StateMachine
from agent.core.tools.builtins import set_project_context
from agent.agents.writer_agent import WriterAgent
from agent.core.engine.workflow_registry import workflow
from agent.utils import parse_llm_json
from agent.workflows.m5_write_chapter import (
    M5WriteChapterWorkflow,
    PreValidationBlocked,
)
from agent.core.quality.scoring.quality_checker import (
    _count_cjk,
    resolve_min_cjk_words,
    resolve_max_cjk_words,
)
from agent.core.quality.guardrails import is_architecture_confirmed
from agent.core.story.evidence_chain import EvidenceChain
from agent.core.infra.prompt_helpers import format_open_debts, format_rag_context


@dataclass
class AgenticWriteResult:
    """Agentic 写章结果（字段对齐 M5 的 M5Result 供 CLI 读取）。"""

    chapter_file: Path
    chapter_num: int
    chapter_title: str
    chapter_text: str
    word_count: int
    quality_passed: bool
    revision_attempts: int
    quality_report: dict[str, Any] = field(default_factory=dict)
    evidence_chain: EvidenceChain = field(default_factory=EvidenceChain)
    rag_context_len: int = 0
    d_issues: list[dict[str, Any]] = field(default_factory=list)


@workflow("agentic_write")
class AgenticWriteWorkflow:
    """Agentic 写章工作流（替代 M5 硬编码流程）。

    Args:
        project_dir: 小说项目目录。
        llm_client: LLM 客户端（WriterAgent 与质检门禁共用）。
        console: rich 控制台（``--json`` 时传静默控制台）。
        tier: auto（默认）/ heavy / light。
        max_drafts: 覆盖 tier 的默认最大起草次数（一般不必传）。
    """

    def __init__(
        self,
        project_dir: Path,
        llm_client: Gateway | None = None,
        console: Console | None = None,
        tier: str = "auto",
        max_drafts: int | None = None,
        # ---- 主线推进：每 mainline_window 章执行一次决策（与 agentic_pipeline 对齐；拍板 1）----
        mainline_window: int = 5,
        # ---- G9 新增参数：章内子阶段事件（默认 None 零开销；由 pipeline 注入）----
        event_emitter: Callable[[dict[str, Any]], None] | None = None,
        # ---- G11 新增参数：风格模仿（默认开：project/style.md 存在即注入）----
        style_enabled: bool = True,
        style_file: str | None = None,
        # ---- G12 新增参数：爽点剧本/情绪目标注入（默认开）----
        payoff_enabled: bool = True,
        # ---- P0 新增参数：去AI味（默认开；--no-deslop 关闭）----
        deslop_enabled: bool = True,
    ) -> None:
        self.project_dir = Path(project_dir)
        self.llm = llm_client or create_gateway()
        self.console = console or Console()
        self.tier = tier
        self.max_drafts = max_drafts
        self.state_machine = StateMachine(self.project_dir)
        self.mainline_window = max(1, int(mainline_window))
        # G11：风格开关（透传给 M5._load_context 使用）
        self.style_enabled = style_enabled
        self.style_file = style_file
        # G12：爽点/情绪开关（透传给 M5._load_context 使用）
        self.payoff_enabled = payoff_enabled
        # P0：去AI味开关（质量门禁通过后、落盘前执行；--no-deslop 关闭）
        self.deslop_enabled = deslop_enabled
        # G9：章内子阶段事件发射器（pipeline 注入；None 时零开销）
        self.event_emitter = event_emitter

    def _emit_substage(self, substage: str, chapter: int) -> None:
        """G9：章内子阶段事件（时序略滞后可接受，见共享知识 #10）；未注入零开销。"""
        if self.event_emitter is not None:
            try:
                self.event_emitter({
                    "type": "chapter_substage",
                    "chapter": chapter,
                    "substage": substage,
                })
            except Exception:  # noqa: BLE001 - 子阶段事件异常不阻断写章（拍板 3）
                pass

    def _run_deslop(self, text: str, ctx: dict[str, Any]) -> str:
        """P0 去AI味：质量门禁通过后、落盘前执行（轻度规则/中重 LLM）。

        与 M5 ``_maybe_deslop`` 共用策略：轻度走规则后处理（零 LLM），中/重度走 LLM
        改写（6 Gate + 三遍法）。任何失败降级返回原文，绝不阻断写章（G3 哲学）。
        标题已由调用方在 deslop 前提取，故此处可安全改写正文。
        """
        if not self.deslop_enabled:
            return text
        try:
            from agent.core.anti_ai.rewriter import DeslopRewriter

            # 先做与落盘一致的清理，避免把标题行/元信息交给 LLM 改写
            body = M5WriteChapterWorkflow._clean_chapter_body(text)
            rewriter = DeslopRewriter(
                self.llm, project_dir=self.project_dir, console=self.console
            )
            result = rewriter.rewrite(body, level="auto")
            self._emit_substage(f"deslop:{result.level}", ctx["chapter_num"])
            if result.changed and result.text.strip():
                return result.text
            return text
        except Exception:  # noqa: BLE001 - 去AI味失败降级原文，不阻断写章
            return text

    # ------------------------------------------------------------------
    # 门禁（与 M5 一致）
    # ------------------------------------------------------------------
    def _guard(self) -> None:
        self.state_machine.load()
        if self.state_machine.state not in (State.CHARACTER_DESIGN, State.WRITING):
            raise RuntimeError(
                f"当前状态 {self.state_machine.state.value} 不允许章节创作，"
                f"需先运行 /design-characters 进入 CHARACTER_DESIGN"
            )
        if not is_architecture_confirmed(self.project_dir):
            raise RuntimeError("故事架构尚未确认，无法开始章节创作")

    def _maybe_advance_mainline(self) -> None:
        """写章前执行主线推进裁决（委托 MainlineOrchestrator，唯一仲裁点）。

        必须在 ``_load_context`` 之前调用：支线切换要在上下文加载前落盘生效。
        异常降级不阻断写章（G3 哲学）。
        """
        try:
            from agent.workflows.budget_planner import BudgetPlanner
            from agent.workflows.mainline_orchestrator import MainlineOrchestrator

            orch = MainlineOrchestrator(
                self.project_dir,
                self.state_machine,
                self.mainline_window,
                self.console,
                budget_planner=BudgetPlanner(self.project_dir, console=self.console),
            )
            orch.replan_if_due()  # 每窗口先由 LLM 主编重规划分线预算
            new_subline = orch.maybe_advance()
            if not new_subline:
                return
            chapter = int((self.state_machine.progress or {}).get("total_written", 0)) + 1
            visited = list(
                (self.state_machine.progress or {}).get("mainline_visited", []) or []
            )
            self.console.print(
                f"[cyan]主线推进：第 {chapter} 章起切至支线 {new_subline}"
                f"（已访问 {len(visited)} 条）[/cyan]"
            )
            self._emit_substage(f"mainline_advance:{new_subline}", chapter)
        except Exception:  # noqa: BLE001 - 决策异常降级不阻断写章（G3 哲学）
            pass

    # ------------------------------------------------------------------
    # 任务提示构建（复用 M5 创作模板，保证风格/信息一致）
    # ------------------------------------------------------------------
    def _build_task(self, ctx: dict[str, Any]) -> str:
        wi = ctx["world_info"]
        rag_context_text = format_rag_context(ctx.get("rag_context", []))
        open_debts_text = format_open_debts(ctx.get("open_debts", []))
        task = pm.get("m5.generate").render_user(
            title=wi["title"],
            tone=wi["tone"],
            pov=wi["pov"],
            rhythm=wi["rhythm"],
            chapter_length=wi["chapter_length"],
            info_density=wi["info_density"],
            banned_elements=wi["banned_elements"],
            chapter_num=ctx["chapter_num"],
            subline_id=ctx["subline_id"],
            subline_name=ctx["subline_name"],
            subline_goal=ctx["subline_goal"],
            pressure_stage=ctx["pressure_stage"],
            tension_level=ctx["tension_level"],
            world_synopsis=wi["synopsis"],
            realm_system=wi["realm_system"],
            golden_finger_info=wi["golden_finger_info"],
            route_node_id=ctx["route_node_id"],
            route_milestone=ctx["route_milestone"],
            route_main_title=ctx["route_main_title"],
            route_main_result=ctx["route_main_result"],
            route_main_growth=ctx["route_main_growth"],
            characters_info=ctx["characters_info"],
            relations_info=ctx["relations_info"],
            foreshadow_task=ctx["foreshadow_task"],
            prev_chapter_summary=ctx["prev_chapter_summary"],
            rag_context=rag_context_text,
            open_debts=open_debts_text,
        )
        # ---- G8（补充边界 4）：结局模式指令注入（ending 为空降级「收尾」通用指令，不阻断）----
        if ctx.get("ending_mode"):
            ending = (ctx.get("ending") or "").strip()
            if ending:
                task += pm.get("g8.ending_instruction").render_user(
                    subline_id=ctx.get("subline_id", ""),
                    mainline="、".join(ctx.get("mainline", []) or []) or "—",
                    ending=ending,
                )
            else:
                task += pm.get("g8.ending_fallback_instruction").render_user()

        # ---- G11：风格指引注入（style.md 存在即注入；缺失/关闭 → 与 G10 输出逐字节一致）----
        style_guide = (ctx.get("style_guide") or "").strip()
        if style_guide:
            task += pm.get("g11.style_instruction").render_user(style_guide=style_guide)

        # ---- G12：爽点剧本 + 情绪目标 + 读者反馈注入（追加顺序：爽点 → 情绪 → 反馈）----
        payoff_task = (ctx.get("payoff_task") or "").strip()
        if payoff_task:
            task += pm.get("g12.payoff_instruction").render_user(payoff_task=payoff_task)
        emotion_target = (ctx.get("emotion_target") or "").strip()
        if emotion_target:
            task += pm.get("g12.emotion_instruction").render_user(emotion_target=emotion_target)
        signals = ctx.get("reader_signals") or []
        if signals:
            lines = []
            for s in signals:
                desc = str(s.get("desc", "") or "")
                planted = int(s.get("planted_ch", 0) or 0)
                marker = "（位于本章之前，请针对此反馈强化本章）" if planted and planted < ctx.get("chapter_num", 0) else ""
                lines.append(f"- {desc}{marker}")
            if lines:
                task += pm.get("g12.reader_feedback").render_user(reader_signals="\n".join(lines))
        return task

    # ------------------------------------------------------------------
    # 外环 Critic：复用 M5 九项 LLM 审稿作为门禁（与 M5 同等质量基线）
    # ------------------------------------------------------------------
    def _llm_quality_gate(self, text: str, ctx: Any) -> tuple[bool, dict[str, Any]]:
        wi = ctx["world_info"]
        is_climax = ctx.get("pressure_stage") == "高潮"
        # ---- 确定性字数下限门禁：LLM 审稿可能对过短章节放行，这里用硬阈值兜底 ----
        # 先做与落盘一致的去元信息/去重清理，避免「提示词回显 / 编辑批注 / 重复正文」
        # 抬高原始字数而漏判（ch081=244 字即因原始含回显文本而逃过门禁）。
        cleaned = M5WriteChapterWorkflow._clean_chapter_body(text)
        cleaned = M5WriteChapterWorkflow._dedup_repeated_chapter(cleaned)
        cleaned = M5WriteChapterWorkflow._dedup_tail_loop(cleaned)
        # 与 m5 / quality_checker 统一的中文字数口径（动态下限随目标伸缩）
        cur_len = _count_cjk(cleaned)
        target_len = int(wi.get("chapter_length") or 3000)
        # 动态门禁下限 = max(绝对下限1500, 目标×0.8)；与 m5 硬关卡一致。
        min_len = resolve_min_cjk_words(target_len)
        if cur_len < min_len:
            report = {
                "overall_pass": False,
                "rules": [],
                "issues": [
                    {
                        "rule_id": "min_length",
                        "severity": "blocking",
                        "description": (
                            f"本章正文过短：仅约 {cur_len} 字，远低于目标字数"
                            f"约 {target_len} 字（下限 {min_len} 字）。"
                            "请完整展开本章冲突、铺垫与结尾钩子，扩写到接近目标字数后再提交。"
                        ),
                    }
                ],
                "suggestions": "扩写本章，补充场景/动作/环境描写与章末悬念，确保字数接近目标。",
            }
            return False, report
        # 超合理上限仅告警，不阻断（区间口径：目标×1.2 视为合理上限）
        max_len = resolve_max_cjk_words(target_len)
        if max_len and cur_len > max_len:
            report = {
                "overall_pass": True,
                "rules": [],
                "issues": [
                    {
                        "rule_id": "max_length",
                        "severity": "warning",
                        "description": (
                            f"本章正文偏长：约 {cur_len} 字，超过目标字数 {target_len} 字的"
                            f"合理上限 {max_len} 字（目标×1.2），可考虑精简冗余描写使其更紧凑。"
                        ),
                    }
                ],
                "suggestions": "无需强制改写；如篇幅过大可适当精简冗余场景/对白。",
            }
            return True, report
        check_prompt = pm.get("m5.quality_check").render_user(
            tone=wi["tone"],
            chapter_length=wi["chapter_length"],
            characters_fingerprint=ctx.get("characters_fingerprint", ""),
            is_climax="是" if is_climax else "否",
            chapter_text=cleaned,
        )
        try:
            resp = chat_utility(
                self.llm,
                messages=[
                    {"role": "system", "content": pm.get("m5.quality_check").system},
                    {"role": "user", "content": check_prompt},
                ],
                max_tokens=1500,
                enable_thinking=False,
            )
            report = parse_llm_json(resp)
            passed = bool(report.get("overall_pass", True))
        except Exception:  # noqa: BLE001 - 质检失败降级为通过，不阻断出章
            report = {"overall_pass": True, "rules": [], "suggestions": "门禁解析失败，默认通过"}
            passed = True
        return passed, report

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------
    def run(self, rewrite_hint: str | None = None) -> AgenticWriteResult:
        self._guard()

        # ---- 主线推进：写章前先裁决是否切支线（必须在 _load_context 之前，分支线生效）----
        self._maybe_advance_mainline()

        # 复用 M5 上下文加载（确定性、已验证）；不注入冲突仲裁以避免前置拦截副作用
        m5 = M5WriteChapterWorkflow(
            project_dir=self.project_dir,
            llm_client=self.llm,
            console=self.console,
            conflict_arbiter=None,
            pre_validate=False,
            # G9：透传同一 event_emitter（复用 M5 无副作用方法时不另发事件）
            event_emitter=self.event_emitter,
            # G11：风格开关透传（_load_context 读取 style.md）
            style_enabled=self.style_enabled,
            style_file=self.style_file,
            # G12：爽点/情绪开关透传（_load_context 读取 payoff_script.json）
            payoff_enabled=self.payoff_enabled,
        )
        # 修复（P0，2026-08-21）：M5 实例的 state_machine 需先从磁盘 load()，
        # 否则 _load_context 里 progress 恒空 → chapter_num 恒为 1、支线恒取第一条。
        m5.state_machine.load()
        ctx = m5._load_context()

        task = self._build_task(ctx)

        # 针对性重写提示（由 Pipeline 回溯闭环传入）：把全书体检未达标项
        # 编译成 Writer 可读的修正要求，避免盲目重写导致反复不达标。
        if rewrite_hint:
            task = (
                task
                + "\n\n# 针对性修正要求（全书体检回溯重写）\n"
                + rewrite_hint
                + "\n请在上文各项设定/风格要求不变的前提下，优先消除上述未达标项后重新提交。"
            )

        # WriterAgent（默认门禁注入 LLM 九项审稿，使质量不低于 M5）
        writer = WriterAgent(
            project_dir=self.project_dir,
            llm_client=self.llm,
            tier=self.tier,
            console=self.console,
            quality_gate=self._llm_quality_gate,
        )
        self.console.print(
            f"[cyan]Agentic 写章（tier={self.tier}）第 {ctx['chapter_num']} 章...[/cyan]"
        )
        # ---- G9：章内子阶段事件（生成；quality_check/revise 在 writer.run 返回后补发）----
        self._emit_substage("generate", ctx["chapter_num"])
        text, revision_attempts, quality_passed = writer.run(task, ctx)
        # ---- G9：章内子阶段事件（质量校验；门禁在 WriterAgent 内已完成，时序略滞后，§14-6）----
        self._emit_substage("quality_check", ctx["chapter_num"])
        if revision_attempts > 0:
            self._emit_substage("revise", ctx["chapter_num"])

        # 落盘（复用 M5 方法，保证产物兼容）
        title = m5._extract_title(text, ctx)

        # ---- P0 去AI味：质量门禁通过后、落盘前（轻度规则/中重 LLM；失败降级原文）----
        text = self._run_deslop(text, ctx)

        word_count = len(re.sub(r"\s", "", text))
        evidence_chain = m5._build_evidence_chain(ctx)
        chapter_file = m5._save_chapter(
            ctx,
            text,
            title,
            word_count,
            quality_passed,
            revision_attempts,
            evidence_chain,
        )
        m5._update_progress(ctx)

        # 修复（P0，2026-08-21）：与 M5 run() 对齐，首次写章后 CHARACTER_DESIGN → WRITING，
        # 否则磁盘状态一直停在 CHARACTER_DESIGN（门禁/看板/状态展示均受影响）。
        if m5.state_machine.state == State.CHARACTER_DESIGN:
            m5.state_machine.transition(Event.WRITE)

        # 尽力而为：对新章做 RAG 索引（供后续章节召回；失败不阻断）
        self._maybe_index(chapter_file)

        return AgenticWriteResult(
            chapter_file=chapter_file,
            chapter_num=ctx["chapter_num"],
            chapter_title=title,
            chapter_text=text,
            word_count=word_count,
            quality_passed=quality_passed,
            revision_attempts=revision_attempts,
            quality_report={},
            evidence_chain=evidence_chain,
            rag_context_len=len(ctx.get("rag_context", []) or []),
            d_issues=[],
        )

    def _maybe_index(self, chapter_file: Path) -> None:
        try:
            from agent.core.rag.retriever import Retriever

            retriever = Retriever(self.project_dir)
            if hasattr(retriever, "index_chapter"):
                retriever.index_chapter(chapter_file)
        except Exception:  # noqa: BLE001 - 索引失败不阻断出章
            pass
