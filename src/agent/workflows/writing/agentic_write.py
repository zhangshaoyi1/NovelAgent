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
from agent.core.infra.degrade import degrade
import logging
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
from agent.workflows.writing.m5_write_chapter import (
    M5WriteChapterWorkflow,
    PreValidationBlocked,
)
from agent.core.quality.scoring.quality_checker import (
    _count_cjk,
    resolve_min_cjk_words,
    resolve_max_cjk_words,
)
from agent.core.quality.guardrails import is_architecture_confirmed
from agent.workflows.writing.m5_quality_gate import (
    GOLDEN_WRITE_GATE_FLOOR,
    GOLDEN_WRITE_GATE_FIRST_N,
    GOLDEN_WRITE_GATE_TOTAL,
)
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
        # ---- F-11 新增参数：D 多维审查（爽点/OOC/连贯/追读力；默认开，统一 write/autowrite）----
        strict_review: bool = True,
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
        # F-11：D 多维审查开关（爽点/OOC/连贯/追读力；默认开，与 write 命令统一）
        self.strict_review = strict_review
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
                pass  # noqa: SILENT_DEGRADE

    def _run_deslop(self, text: str, ctx: dict[str, Any]) -> str:
        """P0 去AI味：质量门禁通过后、落盘前执行（轻度规则/中重 LLM）。

        优化（2026-09-07）：中度/重度去AI味改为**合并修订+去AI味单次 LLM 调用**，
        替代原来的「revise（WriterAgent 内部）+ deslop（DeslopRewriter）」两次调用。
        轻度仍走规则后处理（零 LLM），与 M5 ``_maybe_deslop`` 共用策略。
        任何失败降级返回原文，绝不阻断写章（G3 哲学）。
        标题已由调用方在 deslop 前提取，故此处可安全改写正文。
        """
        if not self.deslop_enabled:
            return text
        try:
            from agent.core.anti_ai.detector import AIFlavorScanner
            from agent.core.anti_ai.rewriter import DeslopRewriter
            from agent.client.gateway_adapter import chat_creative

            # 先做与落盘一致的清理，避免把标题行/元信息交给 LLM 改写
            body = M5WriteChapterWorkflow._clean_chapter_body(text)

            # 轻度：规则后处理（零 LLM）
            scanner = AIFlavorScanner(self.project_dir)
            report = scanner.scan(body)
            if report.level == "light":
                rewriter = DeslopRewriter(
                    self.llm, project_dir=self.project_dir, console=self.console
                )
                result = rewriter.rewrite(body, level="light")
                self._emit_substage(f"deslop:{result.level}", ctx["chapter_num"])
                if result.changed and result.text.strip():
                    return result.text
                return text

            # 中度/重度：合并修订+去AI味单次 LLM 调用（替代 revise + deslop 两次调用）
            self._emit_substage(f"deslop:{report.level}", ctx["chapter_num"])
            level_label = {"medium": "中度", "heavy": "重度"}.get(report.level, report.level)

            # 构建合并审稿意见：包含质量门禁建议 + AI 味检测结果
            quality_suggestions = ""
            if isinstance(ctx.get("_last_quality_report"), dict):
                quality_suggestions = ctx["_last_quality_report"].get("suggestions", "")

            prompt = pm.get("m5.revise_deslop")
            resp = chat_creative(
                self.llm,
                messages=[
                    {"role": "system", "content": prompt.system},
                    {
                        "role": "user",
                        "content": prompt.render_user(
                            quality_report=quality_suggestions,
                            ai_level=report.level,
                            level_label=level_label,
                            chapter_text=body,
                        ),
                    },
                ],
                temperature=0.6,
                max_tokens=8192,
                enable_thinking=False,
            )
            revised = (resp or "").strip()
            if revised and len(revised) >= max(200, len(body) // 2):
                if self.console is not None:
                    self.console.print(
                        f"[dim]  合并修订+去AI味（{level_label}）完成："
                        f"{len(body)} → {len(revised)} 字[/dim]"
                    )
                return revised
            # 合并改写失败 → 降级到原 DeslopRewriter 流程
            rewriter = DeslopRewriter(
                self.llm, project_dir=self.project_dir, console=self.console
            )
            result = rewriter.rewrite(body, level=report.level)
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
            from agent.workflows.pipeline.budget_planner import BudgetPlanner
            from agent.workflows.pipeline.mainline_orchestrator import MainlineOrchestrator

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
            from agent.core.progress import next_chapter

            chapter = next_chapter(self.state_machine.progress or {})
            visited = list(
                (self.state_machine.progress or {}).get("mainline_visited", []) or []
            )
            self.console.print(
                f"[cyan]主线推进：第 {chapter} 章起切至支线 {new_subline}"
                f"（已访问 {len(visited)} 条）[/cyan]"
            )
            self._emit_substage(f"mainline_advance:{new_subline}", chapter)
        except Exception as e:  # noqa: BLE001 - 决策异常降级不阻断写章（G3 哲学）
            degrade("agentic_write.mainline", "主线推进决策异常，维持原支线继续写章", e)
            pass

    # ------------------------------------------------------------------
    # 任务提示构建（复用 M5 创作模板，保证风格/信息一致）
    # ------------------------------------------------------------------
    def _build_craft_guide(self, ctx: dict[str, Any]) -> str:
        """按本章特性按需选载写作技法知识（prompts/methods/）。

        - 章首/章尾钩子技法：每章都需要；
        - 黄金三章开篇指导：前 3 章注入；
        - 爽点/情绪结构技法：高潮章注入。
        任何失败降级为空，绝不阻断写章（G3 哲学）。
        """
        try:
            parts: list[str] = []
            parts.append(pm.get("methods.hooks").render_system())
            chapter_num = int(ctx.get("chapter_num", 0) or 0)
            if chapter_num <= 3:
                parts.append(pm.get("methods.opening").render_system())
            if ctx.get("pressure_stage") == "高潮":
                parts.append(pm.get("methods.payoff").render_system())
            return "\n\n".join(p for p in parts if p and p.strip())
        except Exception as e:  # noqa: BLE001 - 技法知识加载失败降级为空，不阻断写章
            degrade("agentic_write.craft_guide", "写作技法知识库加载失败，降级为空（本章无技法参考）", e)
            return ""

    def _build_task(self, ctx: dict[str, Any]) -> str:
        wi = ctx["world_info"]
        # RAG 渲染放宽（2026-09-10）：此前 3 片×120 字≈360 字，12 片召回 97% 被丢弃，
        # 模板"不得与之矛盾"形同虚设——设定一致/人设稳定 4 次回滚的帮凶。8×300≈2400 字。
        rag_context_text = format_rag_context(ctx.get("rag_context", []), max_chunks=8, max_text_len=300)
        open_debts_text = format_open_debts(ctx.get("open_debts", []), max_debts=5, max_desc_len=60)
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
            chapter_hooks=ctx.get("chapter_hooks", ""),
            plot_points=ctx.get("plot_points", ""),
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
        # ---- 长线一致性底座（设计稿第一期·A/D）：批间复规划裁决 + 三账注入 ----
        # 批间复规划的下一批方向（规划者缺席/未复规划 → 空跳过）；
        directive = ctx.get("batch_directive") or {}
        _d_focus = str(directive.get("focus") or "").strip()
        _d_rationale = str(directive.get("rationale") or "").strip()
        if _d_focus or _d_rationale:
            _d_parts = ["\n\n# 下一批方向（规划者批间复规划裁决，本章情节须服务该方向）"]
            if _d_focus:
                _d_parts.append(f"写作焦点：{_d_focus}")
            if _d_rationale:
                _d_parts.append(f"规划理由：{_d_rationale}")
            task += "\n".join(_d_parts)
        # 实体名册/信息账本/战力标尺账（有名实体的既有档案与"谁知道什么"，缺 → 空跳过）
        ledger_text = str(ctx.get("ledger_context") or "")
        if ledger_text:
            task += "\n\n" + ledger_text
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

            # ---- 收尾缺口数字注入（2026-09-12 风险 5）：给 writer 可执行的余量 ——
            # 未回收伏笔数（附前几条 F-id）+ 主线支线访问进度，让收尾章知道
            # "还欠多少、欠什么"，而不是只靠批末验收后人工兜底。确定性读取，
            # 失败降级为空（不影响既有 ending 指令）。----
            try:
                _gap_lines: list[str] = []
                _fs_file = Path(self.project_dir) / "foreshadows.md"
                if _fs_file.exists():
                    _open: list[str] = []
                    for _line in _fs_file.read_text(encoding="utf-8").splitlines():
                        if _line.startswith("| F-"):
                            _cells = [c.strip() for c in _line.split("|")]
                            if len(_cells) >= 6 and _cells[5] in ("未埋", "已埋"):
                                _open.append(f"{_cells[1]}（{_cells[2][:20]}）")
                    if _open:
                        _gap_lines.append(
                            f"未回收伏笔 {len(_open)} 条，优先安排：{'、'.join(_open[:3])}"
                        )
                _ml_file = Path(self.project_dir) / ".state" / "mainline.json"
                if _ml_file.exists():
                    import json as _json

                    _ml = _json.loads(_ml_file.read_text(encoding="utf-8"))
                    _visited = _ml.get("mainline_visited") or []
                    _total = len(_ml.get("sublines") or _visited) or 0
                    if _total:
                        _gap_lines.append(
                            f"主线支线已访问 {len(_visited)}/{_total}"
                        )
                if _gap_lines:
                    task += "\n\n【收尾缺口（本章应推进偿还）】\n" + "\n".join(
                        f"- {g}" for g in _gap_lines
                    )
            except Exception as e:  # noqa: BLE001 - 缺口读取失败不阻断
                degrade("agentic_write.ending_gap", "收尾缺口读取失败，本章无缺口注入", e)

            # ---- 完本收束清单（设计稿第一期·E）：结局模式章携带全部未了事项
            # 逐条处置（回收/有意留白），缺计划 → 空（由 compose 批前生成）----
            _closure_text = str(ctx.get("closure_text") or "")
            if _closure_text:
                task += _closure_text

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

        # ---- 写作技法知识库注入（按需加载：钩子/黄金三章/爽点结构；失败降级为空）----
        craft_guide = self._build_craft_guide(ctx)
        if craft_guide:
            task += pm.get("m5.craft_instruction").render_user(craft_guide=craft_guide)

        # ---- 角色状态硬约束（P-C 修复·agentic 路径补齐 2026-09-10）：m5 直写路径把
        # characters/*.md 的生死/时间线真源注入为 system 不可违背规则，但本路径的任务
        # 模板（m5.generate）无该字段且从未渲染 → autowrite 主路径 Writer 长期看不到
        # 角色状态权威约束，是设定一致/人设稳定反复不达标（4 次回滚）的直接缺口。
        character_constraints = (ctx.get("character_constraints") or "").strip()
        if character_constraints:
            task += pm.get("g.character_state_constraint").render_user(
                character_constraints=character_constraints
            )

        # ---- 问题债务注入（2026-09-12）：确认未解决的问题（presence_ban/watch/
        # gate_skipped）从 .state/issue_debts.json 渲染成硬约束/提醒，随任务注入
        # writer——确认的问题必须登记并约束后续章节，不允许"当时放行、几十章后
        # 才发现修不起"。空 = 无未销账债务，不注入。----
        try:
            from agent.core.story.issue_debt import render_constraints

            _debt_constraints = render_constraints(
                self.project_dir, int(ctx.get("chapter_num") or 0)
            )
        except Exception as e:  # noqa: BLE001 - 债务注入失败不阻断写章
            degrade("agentic_write.issue_debt", "问题债务约束渲染失败，本章无债务注入", e)
            _debt_constraints = ""
        if _debt_constraints:
            task += "\n" + _debt_constraints

        # ---- 设定台账硬约束（P0-1 补·2026-09-12）：world.md 自开书起零回写，写作中
        # 涌现的设定（阵盘/镇灵符/导引纹……）只存在于正文，Writer 隔几章就重新发明一遍
        # → 章间设定矛盾 → 连贯性/设定一致硬指标长期不达标（43 次体检仅 1 次通过）。
        # m5_context 已渲染 setting_canon，但此前**无任何调用点消费**（哑火接线）；
        # 此处接入生产入口（agentic_write），与 character_constraints 同位、同语义。
        setting_constraints = (ctx.get("setting_canon") or "").strip()
        if setting_constraints:
            task += pm.get("g.setting_canon_constraint").render_user(
                setting_constraints=setting_constraints
            )

        # ---- 主线契约注入（2026-09-12 书级质检）：全书主题承诺一等公民化。
        # 此前系统只在"章内"闭环，无"这本书承诺讲什么"的牵引 → 长篇复印机式
        # 推进（五灵破 192 章实证）。契约从 plan.json.brief 播种，只读不写；
        # 读取失败降级为空串（不注入、不阻断），与 style_guide 同语义。
        try:
            from agent.core.quality.book_ledger import theme_contract_text

            _contract = theme_contract_text(self.project_dir, ctx["chapter_num"])
        except Exception as e:  # noqa: BLE001 - 契约读取失败不影响写作
            _contract = ""
            degrade("agentic_write.theme_contract", "主线契约读取失败，跳过注入", e)
        if _contract:
            task += "\n\n" + _contract
        return task

    def _build_beat_ban(self, ctx: dict[str, Any]) -> str:
        """回溯重写时的【已用桥段禁用清单】（2026-09-10 回滚率削减·P0）。

        背景五灵破 ch151-155 连续重写 3 遍后 Writer 用"教学→失败→成功→感动"
        模板连灌 3 章（coherence 85→30）。重写时 Writer 既看不到相邻新章写过
        什么，也看不到被打回的上一版——本方法补齐这两个输入：

        - 相邻前 2 章（本轮窗口内已写完的新版）：chapters/ch<N-1>.md 等；
        - 本章上一版（被打回）：chapters/_archived/rollback_to_*/ch<N>.md
          取最近一次归档。

        提取用确定性段首骨架（beat_sketch，零 LLM 成本）；任何读取失败静默
        跳过（该清单是增强信息，不得阻断重写）。
        """
        from agent.core.quality.beat_sketch import extract_beat_sketch, render_beats

        try:
            num = int(ctx.get("chapter_num") or 0)
        except Exception:  # noqa: BLE001
            num = 0  # noqa: SILENT_DEGRADE
        if num <= 0:
            return ""
        sections: list[str] = []

        # 相邻前 2 章的新版桥段
        for off in (2, 1):
            p = Path(self.project_dir) / "chapters" / f"ch{num - off:03d}.md"
            if not p.exists():
                continue
            try:
                beats = extract_beat_sketch(p.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                continue  # noqa: SILENT_DEGRADE
            if beats:
                sections.append(f"第 {num - off} 章（本轮新版，已写完）：\n{render_beats(beats)}")

        # 本章上一版（最近一次归档的被打回稿）
        archive_dir = Path(self.project_dir) / "chapters" / "_archived"
        try:
            candidates = sorted(
                archive_dir.glob(f"rollback_to_*/ch{num:03d}.md"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
        except Exception:  # noqa: BLE001
            candidates = []  # noqa: SILENT_DEGRADE
        if candidates:
            try:
                beats = extract_beat_sketch(candidates[0].read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                beats = []  # noqa: SILENT_DEGRADE
            if beats:
                sections.append(
                    f"第 {num} 章（上一版，已因体检不达标被打回——其骨架整体作废）：\n{render_beats(beats)}"
                )

        if not sections:
            return ""
        return pm.get("g.beat_ban").render_user(
            beats_text="\n\n".join(sections)[:1600]
        )

    # ------------------------------------------------------------------
    # 外环 Critic：复用 M5 九项 LLM 审稿作为门禁（与 M5 同等质量基线）
    # ------------------------------------------------------------------
    def _lessons_focus(self) -> str:
        """P-1：质检复审重点——上轮体检教训（读取失败降级为空，不影响质检）。"""
        try:
            from agent.core.quality.eval_lessons import load_eval_lessons_text

            text = (load_eval_lessons_text(self.project_dir) or "").strip()
            return text[:400] if text else ""
        except Exception:  # noqa: BLE001 - 教训读取失败不影响质检
            return ""  # noqa: SILENT_DEGRADE

    def _record_gate_skipped(self, ctx: Any, reason: str) -> None:
        """gate_skipped 登记（2026-09-12 风险 3 收口）：写时门禁因基础设施故障
        放行时，把该章写入 .state/chapter_quality_flags.json（violations 带
        "gate_skipped" 前缀），供批末/人工扫描补检——不允许"未经门禁"静默滑过。
        落盘失败仅留日志（同 pipeline _flag_chapter_quality 的降级语义）。"""
        try:
            import json as _json

            ch = ctx.get("chapter_num") if isinstance(ctx, dict) else None
            if ch is None:
                return
            qf_path = Path(self.project_dir) / ".state" / "chapter_quality_flags.json"
            qf_path.parent.mkdir(parents=True, exist_ok=True)
            existing: list[dict[str, Any]] = []
            if qf_path.exists():
                try:
                    existing = _json.loads(qf_path.read_text(encoding="utf-8")).get("flags", [])
                except Exception as e:  # noqa: BLE001 - 旧文件损坏视为空，显性留痕
                    degrade(
                        "agentic_write.gate_skipped_log",
                        "chapter_quality_flags.json 解析失败，按空表处理（旧 flag 丢失）",
                        e, level=logging.DEBUG,
                    )
                    existing = []
            existing.append({
                "chapter": ch,
                "violations": [f"gate_skipped: {reason}"],
                "flagged_at": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
            })
            tmp = qf_path.with_suffix(".json.tmp")
            tmp.write_text(
                _json.dumps({"flags": existing}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            tmp.replace(qf_path)
            # 问题债务登记：gate_skipped 章同时挂账（kind=gate_skipped），写时
            # 注入提醒 + 批末查漏，不允许"未经门禁"的章静默滑过。
            try:
                from agent.core.story.issue_debt import KIND_GATE_SKIPPED, IssueDebtStore

                _store = IssueDebtStore(self.project_dir).load()
                _store.add(
                    KIND_GATE_SKIPPED,
                    constraint=f"第{ch}章写时门禁故障放行：{reason}",
                    registered_ch=int(ch),
                )
                _store.save()
            except Exception as debt_e:  # noqa: BLE001 - 债务登记失败不影响 flag
                degrade("agentic_write.gate_skipped_log", "gate_skipped 债务登记失败", debt_e)
        except Exception as e:  # noqa: BLE001 - 登记失败不阻断写章，degrade 留痕
            degrade("agentic_write.gate_skipped_log", "gate_skipped 登记落盘失败", e)

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
            # 情节点补充式反馈（对齐 oh-story「回细纲补情节点再写」；避免盲目扩写注水浪费 token）：
            # 指引 Writer 从本章已有的情节点素材中补充「可推进剧情/情绪的子事件」，再按情节点扩写。
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
                            "请**先补充情节点、再按情节点扩写**：优先从本章素材（细纲情节点序列、"
                            "细纲钩子设计、爽点剧本、伏笔任务、未回收钩子债/伏笔债、角色冲突）"
                            "中挖掘 3-6 个可推进剧情或情绪的子事件（谁做了什么，一句话一个），"
                            "把这些子事件织入正文扩写到接近目标字数；"
                            "禁止用重复描写、空泛抒情或大段心理独白注水凑字。"
                        ),
                    }
                ],
                "suggestions": (
                    "情节点补充式扩写：① 列出本章可补充的 3-6 个子事件（如：配角泄露关键信息、"
                    "反派进一步施压、金手指新用法亮相、伏笔回收线索推进、旁线人物登场搅局）；"
                    "② 将子事件按『动作→对话→情绪反应』织入现有段落或新增段落；"
                    "③ 扩写后再次自检字数，达标再提交。"
                ),
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

        # ---- 书级·文体卫生确定性门禁（2026-09-12）：残缺比喻/成语误用/短语复读/
        # 密度失控等生成残留走纯规则拦截（LLM 九项审稿无文笔维度，且不带前文，
        # 抓不住「跟……似的」「名不传虚」这类确定性硬伤——五灵破 ch004/ch050、
        # 无灵 ch140/ch240/ch350 实证）。blocking → 打回修订；warning 随报告透出。
        try:
            from agent.core.quality.text_hygiene import hygiene_issues, split_issues

            _hygiene = hygiene_issues(cleaned)
        except Exception as e:  # noqa: BLE001 - 卫生扫描失败不阻断质检
            _hygiene = []
            degrade("agentic_write.text_hygiene", "文体卫生扫描失败，跳过", e)
        _hygiene_block, _hygiene_warn = split_issues(_hygiene)
        # 登场连续性（初次登场用『再次』口吻）：窄口径 warning，不做 blocking
        # （语义证据不足以支撑不可逆处置，走 ESCALATE 哲学）。
        try:
            from agent.core.quality.book_ledger import check_debut_echo

            _hygiene_warn += check_debut_echo(
                self.project_dir, cleaned, ctx["chapter_num"]
            )
        except Exception as e:  # noqa: BLE001 - 登场检查失败不影响质检
            degrade("agentic_write.debut_check", "登场连续性检查失败，跳过", e)
        if _hygiene_block:
            report = {
                "overall_pass": False,
                "rules": [],
                "issues": _hygiene_block + _hygiene_warn,
                "suggestions": (
                    "文体卫生逐条修复：残缺比喻补全本体；成语按建议替换；"
                    "同一短语全章至多两次；削减感叹号与比喻堆叠；复读短语改写。"
                    "修复后重新提交。"
                ),
            }
            return False, report

        # ---- 跨章段落重复前置（G14 写时化，2026-09-12 风险 1）----
        # 此前只在落盘后 pipeline gate + 完本 fullbook_dup_scan 才扫，命中仅贴 flag
        # 保留该章（changan 书 672 处相似段落事后人工修复实证）。指纹库命中 → blocking。
        try:
            from agent.core.quality.guardrails import Guardrails, load_fingerprints

            _fpdb = load_fingerprints(
                Path(self.project_dir) / ".state" / "chapter_fingerprints.json"
            )
            _fpdb.pop(str(ctx.get("chapter_num")), None)  # 打回重写时排除本章旧指纹
            if _fpdb:
                _dup_gr = Guardrails(
                    check_junk=False, check_title=False, check_dup=True,
                    check_meta_leak=False, check_narrative_tell=False,
                    check_density=False, fingerprint_db=_fpdb,
                )
                _dup_hits = _dup_gr.check_cross_chapter_dup(cleaned)
            else:
                _dup_hits = []
        except Exception as e:  # noqa: BLE001 - 指纹库异常不阻断写作（批末 gate 仍会扫）
            _dup_hits = []
            degrade("agentic_write.dup_check", "跨章重复扫描失败，本轮跳过", e)
        if _dup_hits:
            return False, {
                "overall_pass": False,
                "rules": [],
                "issues": [
                    {
                        "rule_id": "cross_chapter_dup",
                        "severity": "blocking",
                        "description": h
                        + "。禁止整段复制/复用已发布章节：请为该段换切入角度、换措辞、"
                        "换视角重写（情节可以呼应，文字必须独立）。",
                    }
                    for h in _dup_hits
                ],
                "suggestions": (
                    "跨章重复：逐条把相似段落重写为独立性表达；可沿用情节目的，"
                    "但禁止沿用原句式与原描写顺序。修复后重新提交。"
                ),
            }

        # ---- 一致性确定性规则前置（2026-09-12 风险 4）----
        # 生死/时间线（BLOCK）此前只在落盘后 Editor 审查，二次失败即固化；
        # 现前置为写时 blocking。golden_finger/realm/relation（WARN）随报告透出。
        try:
            from agent.core.quality.consistency.checker import (
                CheckTrigger,
                ConsistencyChecker,
                Severity as _CSeverity,
            )

            _crep = ConsistencyChecker(Path(self.project_dir)).check(
                CheckTrigger.POST_WRITE, {"chapter_text": cleaned}
            )
        except Exception as e:  # noqa: BLE001 - 一致性扫描异常不阻断（批末评估仍会覆盖）
            _crep = None
            degrade("agentic_write.consistency_check", "一致性规则扫描失败，本轮跳过", e)
        if _crep is not None:
            _cblock = [
                {
                    "rule_id": f"consistency_{c.rule_id}",
                    "severity": "blocking",
                    "description": (
                        c.description
                        + " 以 characters/*.md、world.md 为唯一真源修正本章表述"
                        "（若剧情确需如此，须先更新真源再写）。"
                        + ("建议：" + "；".join(c.suggestions[:2]) if c.suggestions else "")
                    ),
                }
                for c in _crep.conflicts
                if c.severity == _CSeverity.BLOCK
            ]
            _cwarn = [
                {
                    "rule_id": f"consistency_{c.rule_id}",
                    "severity": "warning",
                    "description": c.description,
                }
                for c in _crep.conflicts
                if c.severity != _CSeverity.BLOCK
            ]
            if _cblock:
                return False, {
                    "overall_pass": False,
                    "rules": [],
                    "issues": _cblock + _cwarn,
                    "suggestions": (
                        "一致性冲突逐条修复：以角色档案/世界书真源为准改写本章表述；"
                        "确需改剧情走向的，先走设定更新流程，禁止正文与真源打架。"
                    ),
                }
            if _cwarn:
                _hygiene_warn.extend(_cwarn)  # 与文体卫生 warning 同路透出

        # ---- 实体/人名一致性写时前置（T1 写时化，2026-09-13）----
        # 正典外组织名在本章首现 / 注册角色被同姓新名接棒 → blocking（在漂移
        # 诞生那一刻拦住，灵荒炉火「灵渊宗」「沈长风→沈清舟」类硬伤）；
        # 沿用旧漂移、配角漏登记 → warning 显性透出。
        try:
            from agent.core.quality.book_checkup import write_time_entity_check

            _ent = write_time_entity_check(
                Path(self.project_dir), int(ctx.get("chapter_num") or 0), cleaned
            )
        except Exception as e:  # noqa: BLE001 - 实体检查异常不阻断（全书体检仍会覆盖）
            _ent = None
            degrade("agentic_write.entity_check", "实体/人名检查失败，本轮跳过", e)
        if _ent:
            if _ent["blocking"]:
                return False, {
                    "overall_pass": False,
                    "rules": [],
                    "issues": [
                        {"rule_id": "entity_consistency", "severity": "blocking", "description": d}
                        for d in _ent["blocking"]
                    ]
                    + [
                        {"rule_id": "entity_consistency", "severity": "warning", "description": d}
                        for d in _ent["warnings"]
                    ],
                    "suggestions": (
                        "实体一致性修复：① 设定外组织名——改用 world.md 既有的宗门/组织名，"
                        "或先走设定更新流程登记 world.md 再写；② 人物改名——沿用 characters/"
                        " 既登记的名字；确需新名/新组织，先更新真源（characters/*.md、"
                        "world.md）再重写本章。"
                    ),
                }
            if _ent["warnings"]:
                _hygiene_warn.extend(
                    [
                        {"rule_id": "entity_consistency", "severity": "warning", "description": d}
                        for d in _ent["warnings"]
                    ]
                )

        # ---- 伏笔回收写时验证（2026-09-12 风险 6，窄口径防误杀）----
        # 仅当本章被分配**强制**回收任务（结局阶段"本章强制回收 ≥1"或十位章
        # "强制回收 ≥1 旧伏笔"）时启用：至少一条被列出的伏笔内容在正文中留下
        # 痕迹（4 字窗口命中）；完全无痕迹 = 回收任务被无视 → blocking 打回。
        # 非强制的"可回收"建议不拦（避免误杀）。
        _fs_task = str(ctx.get("foreshadow_task") or "")
        if ("强制回收" in _fs_task) or ("强制埋 ≥1" in _fs_task):
            try:
                _fs_file = Path(self.project_dir) / "foreshadows.md"
                _fs_content: dict[str, str] = {}
                if _fs_file.exists():
                    for _line in _fs_file.read_text(encoding="utf-8").splitlines():
                        if _line.startswith("| F-"):
                            _cells = [c.strip() for c in _line.split("|")]
                            if len(_cells) >= 4:
                                _fs_content[_cells[1]] = _cells[2]
                # 收集任务行中列出的候选回收 F-id（"可回收 F-xx" / "强制回收 ≥1"）
                _cand_ids = re.findall(r"F-\d+", _fs_task)
                _has_evidence = False
                for _fid in dict.fromkeys(_cand_ids):
                    _content = _fs_content.get(_fid, "")
                    # 4 字滑动窗口（剔除纯标点/数字窗口），任一命中即算有痕迹
                    for _w in (_content[_k:_k + 4] for _k in range(len(_content) - 3)):
                        if _w and re.search(r"[\u4e00-\u9fff]{3}", _w) and _w in cleaned:
                            _has_evidence = True
                            break
                    if _has_evidence:
                        break
                if _cand_ids and not _has_evidence:
                    return False, {
                        "overall_pass": False,
                        "rules": [],
                        "issues": [
                            {
                                "rule_id": "foreshadow_recycle_missing",
                                "severity": "blocking",
                                "description": (
                                    "本章被分配伏笔回收任务（强制回收 ≥1），但正文未出现"
                                    f"任何候选伏笔（{'、'.join(dict.fromkeys(_cand_ids)[:5])}）"
                                    "的内容痕迹。请把至少一条伏笔的回收写成**具体场景**"
                                    "——有可观察的动作/事件/对话、至少 60 字，让读者"
                                    "\"看见\"兑现；仅在内心独白里提及不算回收。"
                                ),
                            }
                        ],
                        "suggestions": "伏笔回收必须落在具体场景中；从候选列表选一条与本章情节最相关的，织入一个兑现场景。",
                    }
            except Exception as e:  # noqa: BLE001 - 伏笔验证异常不阻断（批末维度仍会评）
                degrade("agentic_write.foreshadow_check", "伏笔回收验证失败，本轮跳过", e)

        check_prompt = pm.get("m5.quality_check").render_user(
            tone=wi["tone"],
            chapter_length=wi["chapter_length"],
            characters_fingerprint=ctx.get("characters_fingerprint", ""),
            is_climax="是" if is_climax else "否",
            # 提速·评审校准：开篇/铺垫章不以中后期节奏苛求，减少无效重写轮
            stage_calibration=M5WriteChapterWorkflow._stage_calibration(ctx, 0),
            # P-1（提示词改进）：质检也带上轮体检教训作复审重点——教训不仅写前注入，
            # 质检复查同样聚焦（反馈闭环完整化；读取失败降级为空，不影响质检）
            recheck_focus=self._lessons_focus(),
            # P-4/P-8（提示词改进）：质检校验角色硬约束 + 细纲情节点覆盖（数据缺省为空）
            hard_constraints=ctx.get("character_constraints", ""),
            plot_points=ctx.get("plot_points", ""),
            # P-9（提示词改进）：事实对照卡 = 连续性账本投影 + 设定台账（≤800 字，规则 6 逐条对照）
            # P0-1 补：同时带上已确立设定与已知冲突，让质检能直接抓「重新发明设定」
            fact_card=(
                str(ctx.get("continuity_projection", "") or "")
                + (
                    "\n【设定台账】\n" + str(ctx.get("setting_canon", ""))
                    if ctx.get("setting_canon")
                    else ""
                )
            )[:800],
            # T2（2026-09-13）：上一章原文尾部随质检透传——规则 13 跨章复述
            # 对照的依据；纸条归属式矛盾此前对 gate 不可见（writer 看得到
            # 上一章全文，审稿人看不到，复述错了也没人拦）。
            prev_chapter_excerpt=(str(ctx.get("prev_chapter_summary") or "")[-1500:]),
            chapter_text=cleaned,
        )
        try:
            resp = chat_utility(
                self.llm,
                messages=[
                    {"role": "system", "content": pm.get("m5.quality_check").system},
                    {"role": "user", "content": check_prompt},
                ],
                # H4 修复：九项审稿输出含 rules/issues/d_issues 全量 JSON，1500 会被截断
                # 导致 parse_llm_json 失败走 fail-open 放行；放宽到 4096（与修订评审一致）。
                max_tokens=4096,
                enable_thinking=False,
            )
            try:
                report = parse_llm_json(resp)
            except ValueError as e:
                # fail-open 收口（2026-09-12 风险 3）：解析失败多为截断，可修复——
                # 附错误详情重试一次（对齐 G4 约定）；仍失败才降级放行并写显性 flag。
                try:
                    resp = chat_utility(
                        self.llm,
                        messages=[
                            {"role": "system", "content": pm.get("m5.quality_check").system},
                            {"role": "user", "content": check_prompt
                             + f"\n\n【上次质检输出解析失败原因，务必修正】"
                               f"请只输出一个合法的 JSON 对象，不要包含 ```json 标记：\n{e}"},
                        ],
                        max_tokens=4096,
                        enable_thinking=False,
                    )
                    report = parse_llm_json(resp)
                except ValueError as e2:
                    degrade(
                        "agentic_write.quality_gate",
                        "LLM 质检解析失败且带错重试仍失败，降级为通过（已登记 gate_skipped，"
                        "本章未经质量门禁，批末体检查漏）",
                        e2,
                    )
                    self._record_gate_skipped(ctx, f"九项质检解析失败：{e2}")
                    report = {"overall_pass": True, "rules": [], "suggestions": "门禁解析失败，重试后降级通过"}
        except Exception as e:  # noqa: BLE001 - 质检调用异常：重试一次后降级放行并显性登记
            try:
                resp = chat_utility(
                    self.llm,
                    messages=[
                        {"role": "system", "content": pm.get("m5.quality_check").system},
                        {"role": "user", "content": check_prompt},
                    ],
                    max_tokens=4096,
                    enable_thinking=False,
                )
                report = parse_llm_json(resp)
            except Exception as e2:  # noqa: BLE001
                degrade(
                    "agentic_write.quality_gate",
                    "LLM 质检调用异常且重试仍失败，降级为通过（已登记 gate_skipped）",
                    e2,
                )
                self._record_gate_skipped(ctx, f"九项质检调用异常：{e2}")
                report = {"overall_pass": True, "rules": [], "suggestions": "门禁异常，重试后降级通过"}
        passed = bool(report.get("overall_pass", True))

        # 文体卫生/登场连续性 warning 随报告透出（不阻断，供 Writer 复查与台账追溯）
        if _hygiene_warn:
            report["issues"] = list(report.get("issues") or []) + _hygiene_warn

        # F-11：D 多维审查（爽点/OOC/连贯/追读力，strict_review 开启时执行；
        # BLOCK 级视为未通过触发修订——与 M5 语义一致；失败降级不阻断九项质检）
        if self.strict_review:
            try:
                from agent.core.quality.scoring import LLMBackedChecker, QualityChecker, Severity

                _qc = QualityChecker(self.project_dir, self.llm)
                _checker = LLMBackedChecker(self.llm)
                _issues = _checker.run_rules(_qc.llm_rules, cleaned, ctx)
                _d = [
                    {
                        "rule_id": i.rule_id,
                        "severity": i.severity.value,
                        "description": i.description,
                    }
                    for i in _issues
                ]
                report["d_issues"] = _d
                report["d_blocking"] = any(
                    i.get("severity") == Severity.BLOCK.value for i in _d
                )
                if report["d_blocking"]:
                    passed = False
                    report["overall_pass"] = False
            except Exception as e:  # noqa: BLE001 - D 审查失败降级为空，不影响九项质检
                degrade("agentic_write.d_review", "D 多维审查失败，降级为空", e)

        # ---- 金三写时门禁（2026-09-12）：前三章吸引力六维不达标 → blocking，禁止落盘 ----
        # 此前金三只在批末评估，写时九项审稿无吸引力维度，低质量开局照样兜底落盘，
        # 批末金三评估必然熔断且回溯修不到开头（五灵破 19+7 次 escalated 实证）。
        if int(ctx.get("chapter_num", 0) or 0) <= GOLDEN_WRITE_GATE_FIRST_N:
            try:
                from agent.core.quality.scoring.reader_appeal import ReaderAppealScorer

                if getattr(self, "_golden_scorer", None) is None:
                    self._golden_scorer = ReaderAppealScorer(self.llm, self.console)
                try:
                    _gr = self._golden_scorer.score_chapter(cleaned)
                except Exception as e:  # 评分异常重试一次（网络/截断多为瞬时）
                    degrade("agentic_write.golden_gate_retry", "金三写时评分异常，重试一次", e, level=logging.DEBUG)
                    _gr = self._golden_scorer.score_chapter(cleaned)
            except Exception as e:  # noqa: BLE001 - 重试仍异常降级放行（G3），显性登记供批末查漏
                degrade(
                    "agentic_write.golden_gate",
                    "金三写时评分失败且重试仍失败，本轮放行（已登记 gate_skipped；"
                    "批末金三门禁仍会把关）",
                    e,
                )
                self._record_gate_skipped(ctx, f"金三写时评分失败：{e}")
                _gr = None
            if _gr is not None and _gr.llm_used:
                _failing = [
                    f"{k}={v}（触底线 {GOLDEN_WRITE_GATE_FLOOR}）"
                    for k, v in _gr.dimensions.items()
                    if v < GOLDEN_WRITE_GATE_FLOOR
                ]
                if _gr.total_score < GOLDEN_WRITE_GATE_TOTAL or _failing:
                    passed = False
                    report["overall_pass"] = False
                    report["golden_gate_failed"] = True
                    report.setdefault("issues", []).append(
                        {
                            "rule_id": "golden_gate",
                            "severity": "blocking",
                            "description": (
                                f"金三门禁未达标：读者吸引力综合分 {_gr.total_score}/"
                                f"{GOLDEN_WRITE_GATE_TOTAL}"
                                + (f"；触底维度：{'、'.join(_failing)}" if _failing else "")
                                + "。本章为开篇前三章，不达标不得落盘。"
                                "定向强化：世界观新颖度低→给设定独特记忆点/代价/异象；"
                                "人物弧光弱→给主角一次主动选择或小胜利，而非纯被动；"
                                "爽点密度低→压缩压抑段、提前兑现一个具体爽点节拍；"
                                "情绪曲线平→制造明显起伏；代入感弱→收紧视角、增加可感细节；"
                                "钩子弱→章末悬念更具体。保持情节/人物/设定不变，只提升写法。"
                            ),
                        }
                    )
                    report["suggestions"] = (
                        str(report.get("suggestions", ""))
                        + "\n金三门禁："
                        + ("；".join(_gr.suggestions[:3]) if _gr.suggestions else "见触底维度")
                    )
        return passed, report

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------
    def run(
        self,
        rewrite_hint: str | None = None,
        chapter_num: int | None = None,
    ) -> AgenticWriteResult:
        """写一章。

        Args:
            rewrite_hint: 针对性修正要求（评测回溯/门禁打回时传入）。
            chapter_num: 章号锚定（F-8，2026-09-07）。门禁打回重写时必须传
                原章号：默认 ``_load_context`` 按 ``total_written + 1`` 取新章号，
                而打回时上一章刚落盘 → 重写会溢出成"下一章"（五灵破
                ch9/ch11/ch13 实证：门禁每打回一次就多写一章）。评测回溯路径
                无需传（m10 回滚已先回退 total_written）。
        """
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
        # ---- F18.4 M18 未完成草稿检测（能力对账修复 2026-09-11）：与 M5 同位 ----
        # 只在已进入 WRITING（非首次进入）时检测；命中则告警不交互阻塞（自动流水线无人应答）。
        try:
            if m5.state_machine.state == State.WRITING:
                from agent.workflows.evaluation.m18_recovery import check_draft_on_startup

                _draft_decision = check_draft_on_startup(
                    self.project_dir, console=self.console, interactive=False
                )
                if _draft_decision.has_draft and _draft_decision.draft is not None:
                    self.console.print(
                        f"[yellow]⚠ 检测到未完成草稿（第 "
                        f"{_draft_decision.draft.chapter_num} 章），续写将覆盖之；"
                        f"如需恢复请先运行 novel-agent draft-status -d "
                        f"{self.project_dir}[/yellow]"
                    )
        except Exception:  # noqa: BLE001 - 检测失败不阻断写章
            pass  # noqa: SILENT_DEGRADE
        ctx = m5._load_context()

        # F-8：章号锚定（见 run() docstring）。覆盖 ctx 后同步重取前情
        # （_load_prev_summary 按 chapter_num-1 取上一章，重写同一章时前情不变）。
        if chapter_num is not None and int(chapter_num) > 0:
            ctx["chapter_num"] = int(chapter_num)
            try:
                ctx["prev_chapter_summary"] = m5._load_prev_summary(ctx["chapter_num"])
            except Exception:  # noqa: BLE001 - 前情重取失败保留原值，不阻断
                pass  # noqa: SILENT_DEGRADE

        task = self._build_task(ctx)

        # ---- F-E2.2 题材套路动态注入（能力对账修复 2026-09-11）----
        # 原实现只在 M5 路径读/清（m5_write_chapter.py:341/459）→ agentic 路径两者皆无，
        # CLI ``inject_genre`` 注入的题材套路对 autowrite 完全无效（P2-1 哑火）。
        # 此处补接线，与 M5 同源；读取失败降级不阻断。
        try:
            _tropes_text = m5._collect_injected_tropes(ctx)
        except Exception:  # noqa: BLE001 - 读取失败不影响写作
            _tropes_text = ""  # noqa: SILENT_DEGRADE
        if _tropes_text:
            task = (
                task
                + "\n\n# 题材套路（本次运行显式注入，请自然融入，不要生硬堆砌）\n"
                + _tropes_text
            )

        # 针对性重写提示（由 Pipeline 回溯闭环传入）：把全书体检未达标项
        # 编译成 Writer 可读的修正要求，避免盲目重写导致反复不达标。
        if rewrite_hint:
            task = (
                task
                + "\n\n# 针对性修正要求（全书体检回溯重写）\n"
                + rewrite_hint
                + "\n请在上文各项设定/风格要求不变的前提下，优先消除上述未达标项后重新提交。"
                + "\n\n# 重写多样性硬要求（防复刻上一稿）\n"
                + "上一稿已在质量门禁被拒，意味着「同样的写法」注定再次被拒。本次重写必须：\n"
                + "1. 更换切入视角或开场方式（禁止沿用上一稿的首段与场景切入顺序）；\n"
                + "2. 重排场景结构（改用插叙/场景切换/对话推进等不同组织方式）；\n"
                + "3. 只保留必须一致的硬事实（章号锚定/前情衔接/设定台账/人物状态），"
                + "其余表达层全部重写。逐字雷同或结构复刻上一稿将被再次拒绝。"
            )
            # 已用桥段禁用清单（2026-09-10）：重写时明确"上一版/相邻章写过什么"，
            # 防止同构桥段连灌（构造失败静默跳过，不阻断重写）。
            try:
                _beat_ban = self._build_beat_ban(ctx)
            except Exception:  # noqa: BLE001  # noqa: SILENT_DEGRADE
                _beat_ban = ""  # noqa: SILENT_DEGRADE
            if _beat_ban:
                task = task + "\n\n" + _beat_ban
        else:
            # 非重写路径：注入上一轮体检教训（反馈闭环·缺口2修复 2026-09-08）。
            # 重写路径已有 build_rewrite_hint 覆盖同一信息，不重复注入。
            try:
                from agent.core.quality.eval_lessons import load_eval_lessons_text

                _lessons = load_eval_lessons_text(self.project_dir)
            except Exception:  # noqa: BLE001 - 教训读取失败不影响写作
                _lessons = ""  # noqa: SILENT_DEGRADE
            if _lessons:
                task = (
                    task
                    + "\n\n# 上轮体检教训（避免重犯）\n"
                    + _lessons
                    + "\n请在上文各项设定/风格要求不变的前提下，规避上述问题后提交。"
                )
            # 书级质量漂移告警（2026-09-12）：最近 N 章通过率过低时显性告知
            # Writer——单章独立判过/挂时全书系统性劣化不可见（质量彩票问题）。
            try:
                from agent.core.quality.book_ledger import baseline_drift_text

                _drift = baseline_drift_text(self.project_dir)
            except Exception as e:  # noqa: BLE001 - 漂移告警失败不影响写作
                _drift = ""
                degrade("agentic_write.baseline_drift", "质量基线读取失败，跳过", e)
            if _drift:
                task = task + "\n\n" + _drift

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

        # ---- F18.4 M18 草稿保存（能力对账修复 2026-09-11）----
        # 原实现只在**废弃**的 M5 入口调用（m5_write_chapter.py:367）→ agentic 路径
        # 生成后崩溃/中断无草稿可恢复，长章（12~15 分钟）只能整章重跑。
        # 此处补接线，与 M5 同位（生成后、落盘前）；失败降级不阻断。
        draft_mgr = None
        try:
            from agent.workflows.evaluation.m18_recovery import DraftManager

            draft_mgr = DraftManager(self.project_dir)
            draft_mgr.save_draft(
                chapter_num=ctx["chapter_num"],
                subline_id=str(ctx.get("subline_id", "")),
                text=text,
            )
        except Exception as e:  # noqa: BLE001 - 草稿保存失败不阻断写章
            self.console.print(
                f"[yellow]⚠ 草稿保存失败（不影响本章产出）：{e}[/yellow]"
            )  # noqa: SILENT_DEGRADE

        # 落盘（复用 M5 方法，保证产物兼容）
        title = m5._extract_title(text, ctx)
        # 标题唯一性保障（与 M5 同源）：占位/重复标题就地重生，不依赖整章重写
        title = m5._ensure_unique_title(ctx["chapter_num"], title, text)

        # ---- P0 去AI味：质量门禁通过后、落盘前（轻度规则/中重 LLM；失败降级原文）----
        pre_deslop_text = text
        text = self._run_deslop(text, ctx)
        # ---- deslop 复检（2026-09-12）：deslop 的 LLM 改写发生在九项质检之后、
        # 落盘之前，改写结果此前不过任何门禁——可能把字数改穿硬门下限（其内置
        # 护栏只要求 ≥ 原文一半，远低于门禁下限），也可能引入新的文体卫生缺陷。
        # 确定性复检：① 字数跌破门禁下限；② hygiene 出现原文没有的 blocking 项
        # → 弃用改写稿、回退质检通过的原文落盘（显性告警，不阻断）。----
        _deslop_len = _count_cjk(M5WriteChapterWorkflow._clean_chapter_body(text))
        _deslop_floor = resolve_min_cjk_words(
            int(ctx["world_info"].get("chapter_length") or 3000)
        )
        _revert_reason = ""
        if _deslop_len < _deslop_floor:
            _revert_reason = (
                f"改写稿仅 {_deslop_len} 字（门禁下限 {_deslop_floor}）"
            )
        else:
            try:
                from agent.core.quality.text_hygiene import hygiene_issues, split_issues

                _pre_block = {
                    i.get("rule_id") for i in split_issues(hygiene_issues(
                        M5WriteChapterWorkflow._clean_chapter_body(pre_deslop_text)
                    ))[0]
                }
                _post_block = {
                    i.get("rule_id") for i in split_issues(hygiene_issues(
                        M5WriteChapterWorkflow._clean_chapter_body(text)
                    ))[0]
                }
                _new_block = _post_block - _pre_block
                if _new_block:
                    _revert_reason = f"改写稿新引入文体卫生 blocking：{sorted(_new_block)}"
            except Exception as e:  # noqa: BLE001 - 复检异常不回退，交批末体检
                degrade("agentic_write.deslop_recheck", "deslop 卫生复检失败，跳过", e)
        if _revert_reason:
            self.console.print(
                f"[yellow]⚠ deslop 复检未过：{_revert_reason}，"
                "回退为质检通过的原文落盘[/yellow]"
            )
            text = pre_deslop_text

        # ---- 缺口 B（2026-09-06）：canonical body 成文管线 ----
        # 与 M5 同链：落盘 / 门禁 / 指纹消费同一产物（_save_chapter 内部幂等兜底）。
        text = m5._finalize_chapter_text(text)

        word_count = len(re.sub(r"\s", "", text))
        evidence_chain = m5._build_evidence_chain(ctx)
        # ---- F-E4.3 证据链源校验（能力对账修复 2026-09-11）----
        # 原实现只在废弃 M5 入口调用（m5_write_chapter.py:404）→ agentic 路径
        # evidence_chain.missing_sources 恒空；frontmatter 引用不存在的源文件也无告警。
        try:
            evidence_chain = m5._validate_evidence(evidence_chain)
        except Exception as e:  # noqa: BLE001 - 校验失败不阻断落盘
            self.console.print(
                f"[yellow]⚠ 证据链校验失败（跳过，不影响落盘）：{e}[/yellow]"
            )  # noqa: SILENT_DEGRADE
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
        # ---- F18.4 M18 清除草稿（能力对账修复 2026-09-11）----
        # 章节已成功持久化 → 草稿使命完成；失败降级不阻断（draft-status 可手动清理）。
        if draft_mgr is not None:
            try:
                draft_mgr.clear_draft()
            except Exception as e:  # noqa: BLE001
                self.console.print(f"[yellow]⚠ 草稿清理失败：{e}[/yellow]")  # noqa: SILENT_DEGRADE
        # ---- F-E2.2 生成后清除运行时注入的套路（能力对账修复 2026-09-11）----
        # 与 M5 同源：套路是「运行期上下文」，用毕即清，避免污染后续章节。
        try:
            if m5._injected_store.get():
                m5._injected_store.clear()
        except Exception:  # noqa: BLE001 - 清理失败不影响产出
            pass  # noqa: SILENT_DEGRADE
        # ---- G15 章后归档 hook（能力对账修复 2026-09-11）：与 M5.run() 同位接线。
        # 此前唯一调用点在架构红线禁跑的废弃 M5 入口内 → ledger.json 永不落盘，
        # 上一章动态状态断供（五灵破 ch181/182 章间矛盾机制性根因）。
        # 本章交接归档进连续性账本 + 伏笔 beats 标记落地；失败降级不阻断。
        m5._archive_chapter(ctx, title, text)
        # ---- 书级台账 hook（2026-09-12）：登场登记 + 质量基线记录（与 M5 同位，
        # 能力对账要求两侧 run 链路同名调用）；失败降级不阻断。
        m5._record_book_ledger(ctx, title, text, quality_passed, revision_attempts)
        # M13 伏笔对账 hook（与 M5 同源；失败降级不阻断）
        try:
            from agent.workflows.evaluation.m13_foreshadow import sync_foreshadow_states

            sync_foreshadow_states(self.project_dir, console=self.console)
        except Exception:  # noqa: BLE001  # noqa: SILENT_DEGRADE
            pass  # noqa: SILENT_DEGRADE
        # 标题已发布 → 失效缓存，下一章查重可见本章标题
        m5._published_titles_cache = None

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
            # 缺口 B：返回 canonical artifact（标题 + 正文），与落盘形态一致，
            # 管线门禁（标题合规/查重/指纹）自此检查的是真正交付的成文。
            chapter_text=m5.compose_chapter_markdown(ctx["chapter_num"], title, text),
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
            pass  # noqa: SILENT_DEGRADE
