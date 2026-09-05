"""M5 章节创作工作流（编排主干）

单章生成闭环的编排入口：run() 驱动 上下文装配 → 生成 → 质量闸 → 净化 → 落盘。
具体职责已按 Mixin 拆分：
    m5_context.py      上下文装配（M5ContextMixin）
    m5_quality_gate.py 质量校验/修订/多维审查（M5QualityGateMixin）
    m5_text_hygiene.py 文本净化/去重（M5TextHygieneMixin + 模块级净化函数）
    m5_persist.py      依据链/持久化/归档/进度/呈现（M5PersistMixin）
类名与导入路径保持不变，旧代码/测试无需改动。
"""

from __future__ import annotations

import json as _json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from rich.console import Console

from agent.core.base.exceptions import PreValidationBlocked
from agent.core.engine.state_machine import Event, State, StateMachine
from agent.core.engine.workflow_registry import workflow
from agent.core.infra.prompt_manager import pm
from agent.core.llmops.trace import usage_snapshot as _usage_snapshot  # LLMOps 章级用量
from agent.core.quality.consistency import ConflictArbiter
from agent.core.quality.guardrails import is_architecture_confirmed
from agent.core.quality.scoring import QualityChecker
from agent.core.registry.genre_pack import GenrePackRegistry
from agent.core.story.evidence_chain import EvidenceChain
from agent.core.story.injected_trope_store import InjectedTropeStore
from agent.core.story.setting_manager import SettingManager
from agent.client.gateway_adapter import create_gateway, chat_creative
from llmagent.gateway import Gateway

from agent.workflows.writing.m5_context import M5ContextMixin
from agent.workflows.writing.m5_persist import M5PersistMixin, PreValidationResult  # noqa: F401 - re-export
from agent.workflows.writing.m5_quality_gate import MAX_REVISIONS, M5QualityGateMixin  # noqa: F401 - re-export
from agent.workflows.writing.m5_text_hygiene import (  # noqa: F401 - re-export 兼容旧导入
    M5TextHygieneMixin,
    _auto_split_paragraphs,
    _collapse_cjk_spaces,
    _strip_frontmatter,
    hard_replace_english,
    scan_english_contamination,
)

logger = logging.getLogger(__name__)




@dataclass
class M5Result:
    """M5 执行结果"""

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
    d_issues: list[dict[str, Any]] = field(default_factory=list)  # D 多维审查问题（仅 strict_review 时填充）
    # LLMOps：本章 LLM 用量（tokens_in/tokens_out/llm_calls，窗口差值统计；None=未启用追踪）
    usage: dict[str, Any] | None = None


@workflow("m5_write_chapter")
class M5WriteChapterWorkflow(
    M5ContextMixin, M5QualityGateMixin, M5TextHygieneMixin, M5PersistMixin
):
    """M5 章节创作工作流"""

    def __init__(
        self,
        project_dir: Path,
        llm_client: Gateway | None = None,
        setting_manager: SettingManager | None = None,
        state_machine: StateMachine | None = None,
        console: Console | None = None,
        mode_controller: "ModeController | None" = None,
        conflict_arbiter: ConflictArbiter | None = None,
        pre_validate: bool = True,
        genre_registry: GenrePackRegistry | None = None,
        enable_structured_qc: bool = False,
        strict_review: bool = False,
        # ---- G9 新增参数：章内子阶段事件（默认 None 零开销；由 pipeline 注入）----
        event_emitter: Callable[[dict[str, Any]], None] | None = None,
        # ---- G11 新增参数：风格模仿（默认开：project/style.md 存在即注入）----
        style_enabled: bool = True,
        style_file: str | None = None,
        # ---- G12 新增参数：爽点剧本/情绪目标注入（默认开：.state/payoff_script.json 存在即注入）----
        payoff_enabled: bool = True,
        # ---- P0 新增参数：去AI味（默认开；--no-deslop 关闭）----
        deslop_enabled: bool = True,
    ) -> None:
        self.project_dir = Path(project_dir)
        self.llm = llm_client or create_gateway()
        self.sm = setting_manager or SettingManager(self.project_dir)
        self.state_machine = state_machine or StateMachine(self.project_dir)
        self.console = console or Console()
        self.chapters_dir = self.project_dir / "chapters"
        # M8 介入频率控制（懒加载避免循环导入）
        self._mode_controller = mode_controller
        # E3 前置冲突检测
        self.conflict_arbiter = conflict_arbiter
        self.pre_validate = pre_validate
        # E2 题材动态注入（运行期上下文，独立存储，不污染 state.json）
        self._genre_registry = genre_registry
        self._injected_store = InjectedTropeStore(self.project_dir)
        # T-5：可选启用结构化质量校验（仅补充，不替换主路径 LLM 校验）
        self.enable_structured_qc = enable_structured_qc
        # D：多维 LLM 质量审查（默认关；开启后把爽点/OOC/连贯性/追读力并入 revise_loop）
        self.strict_review = strict_review
        # D：质量校验器实例（惰性持有 LLM 维度规则，供 LLMBackedChecker 合并驱动）
        self._qc = QualityChecker(self.project_dir, self.llm)
        # G9：章内子阶段事件发射器（pipeline 注入；None 时零开销）
        self.event_emitter = event_emitter
        # G11：风格模仿（project/style.md 存在即注入；--no-style 关闭）
        self.style_enabled = style_enabled
        self.style_file = style_file
        # G12：爽点剧本/情绪目标注入（.state/payoff_script.json 存在即注入；--no-payoff 关闭）
        self.payoff_enabled = payoff_enabled
        # P0：去AI味开关（质量门禁通过后、落盘前执行；--no-deslop 关闭）
        self.deslop_enabled = deslop_enabled
        # 提速：确定性关卡快速复审（上轮失败全部来自英文污染/字数等纯扫描关卡时，
        # 下一轮跳过 LLM 复检，仅重跑确定性扫描；置 False 恢复每轮全量 LLM 复检）
        self.fast_deterministic_recheck = True

    def _emit_substage(self, substage: str, chapter: int) -> None:
        """G9：章内子阶段事件（真实阶段边界，M5 精确）；未注入 emitter 时零开销。

        Args:
            substage: generate / quality_check / revise。
            chapter: 当前章节号。
        """
        if self.event_emitter is not None:
            try:
                self.event_emitter({
                    "type": "chapter_substage",
                    "chapter": chapter,
                    "substage": substage,
                })
            except Exception:  # noqa: BLE001 - 子阶段事件异常不阻断写章（拍板 3）
                pass

    def _maybe_deslop(self, text: str, ctx: dict[str, Any]) -> str:
        """P0 去AI味：质量门禁通过后、落盘前执行（轻度规则/中重 LLM）。

        与 agentic_write 共用策略：轻度走规则后处理（零 LLM），中/重度走 LLM 改写
        （6 Gate + 三遍法）。任何失败降级返回原文，绝不阻断写章（G3 哲学）。
        输入应为已 ``_clean_chapter_body`` 的正文（无标题行/元信息）。
        """
        if not self.deslop_enabled:
            return text
        try:
            from agent.core.anti_ai.rewriter import DeslopRewriter

            rewriter = DeslopRewriter(
                self.llm, project_dir=self.project_dir, console=self.console
            )
            result = rewriter.rewrite(text, level="auto")
            self._emit_substage(f"deslop:{result.level}", ctx["chapter_num"])
            if result.changed and result.text.strip():
                return result.text
            return text
        except Exception:  # noqa: BLE001 - 去AI味失败降级原文，不阻断写章
            return text

    @property
    def mode_controller(self) -> "ModeController":
        """懒加载 ModeController（M8）"""
        if self._mode_controller is None:
            from agent.workflows.writing.m8_mode import ModeController

            self._mode_controller = ModeController(
                project_dir=self.project_dir,
                state_machine=self.state_machine,
                console=self.console,
            )
        return self._mode_controller

    # ============================================================
    # 入口
    # ============================================================
    def run(self) -> M5Result:
        """运行 M5 章节创作工作流

        Raises:
            RuntimeError: 状态不符 / 架构未确认 / 必要文件缺失
        """
        self.state_machine.load()
        if self.state_machine.state not in (State.CHARACTER_DESIGN, State.WRITING):
            raise RuntimeError(
                f"当前状态 {self.state_machine.state.value} 不允许章节创作，"
                f"需先运行 /design-characters 进入 CHARACTER_DESIGN"
            )

        # ★门禁 F14
        if not is_architecture_confirmed(self.project_dir):
            raise RuntimeError("故事架构尚未确认，无法开始章节创作")

        # ------ 0. M18 草稿检测（F18.4）------
        # 进入 WRITING 时检测是否有未完成草稿（非首次进入才检测）
        if self.state_machine.state == State.WRITING:
            from agent.workflows.evaluation.m18_recovery import check_draft_on_startup

            draft_decision = check_draft_on_startup(
                self.project_dir, console=self.console, interactive=False
            )
            if draft_decision.has_draft and draft_decision.draft is not None:
                self.console.print(
                    f"[yellow]⚠ 检测到未完成草稿（第 {draft_decision.draft.chapter_num} 章），"
                    f"请先运行 novel-agent draft-status -d {self.project_dir} 处理[/yellow]"
                )

        # ------ 1. 7 步上下文加载 ------
        ctx = self._load_context()

        # ------ 1.5 M8 介入：章节前询问方向（heavy 模式） ------
        user_direction = self.mode_controller.ask_chapter_direction(ctx)
        if user_direction:
            ctx["user_direction"] = user_direction

        # ------ 1.6 E2 题材动态注入：收集运行时指定的套路文本 ------
        injected_tropes_text = self._collect_injected_tropes(ctx)

        # ------ 1.7 E3 前置式冲突检测门禁（生成前拦截） ------
        if self.pre_validate and self.conflict_arbiter is not None:
            pv = self._pre_validation(ctx)
            if pv.decision == "interrupt":
                raise PreValidationBlocked(pv.report)

        # ------ 2. 生成章节 ------
        self.console.print(
            f"\n[cyan]正在生成第 {ctx['chapter_num']} 章"
            f"（{ctx['subline_id']} · {ctx['pressure_stage']}）...[/cyan]"
        )
        # ---- G9：章内子阶段事件（生成）----
        self._emit_substage("generate", ctx["chapter_num"])
        # LLMOps：章级用量窗口起点（写章前快照，与收尾差值 = 本章真实用量）
        _u0 = _usage_snapshot()
        chapter_text = self._generate_chapter(
            ctx, injected_tropes_text=injected_tropes_text
        )

        # ------ 2.5 M18 保存草稿（F18.4）------
        # 生成后、持久化前保存草稿，中断时可恢复
        from agent.workflows.evaluation.m18_recovery import DraftManager

        draft_mgr = DraftManager(self.project_dir)
        draft_mgr.save_draft(
            chapter_num=ctx["chapter_num"],
            subline_id=ctx["subline_id"],
            text=chapter_text,
        )

        # ------ 3. 质量校验 + 自动修订 ------
        # ---- G9：章内子阶段事件（质量校验）----
        self._emit_substage("quality_check", ctx["chapter_num"])
        quality_report, revision_attempts, final_text = self._quality_check_and_revise(
            ctx, chapter_text
        )
        quality_passed = bool(quality_report.get("overall_pass", False))

        # ------ 4. 提取章节标题 + 清理正文元信息 ------
        chapter_title = self._extract_title(final_text, ctx)
        # 落盘/计数前去掉模型误输出的标题行、原文标题、编辑批注，保证字数统计正确、无双标题
        final_text = self._clean_chapter_body(final_text)

        # ------ 4.5 P0 去AI味：质量门禁通过后、落盘前（轻度规则/中重 LLM；失败降级原文）------
        final_text = self._maybe_deslop(final_text, ctx)

        # ------ 5. 依据链（E4 结构化） ------
        evidence_chain = self._build_evidence_chain(ctx)
        # F-E4.3 落盘前校验引用源是否存在
        evidence_chain = self._validate_evidence(evidence_chain)

        # ------ 6. 持久化 ------
        word_count = len(final_text.replace("\n", "").replace(" ", ""))
        chapter_file = self._save_chapter(
            ctx, final_text, chapter_title, word_count,
            quality_passed, revision_attempts, evidence_chain,
        )

        # ---- G15 章后归档 hook：本章 deltas 归档进连续性账本 + 伏笔 beats 标记落地。
        # 缺账本/失败一律 try/except 降级不阻断（对齐 `_maybe_advance_mainline` hook 位置）。
        self._archive_chapter(ctx, chapter_title)

        # A：增量索引（仅当 .state/rag/ 已建立；否则跳过，绝不阻断写章）
        rag_context_len = len(ctx.get("rag_context", []))
        rag_dir = self.project_dir / ".state" / "rag"
        if rag_dir.exists():
            try:
                from agent.core.rag.indexer import Indexer

                Indexer(self.project_dir).index_chapter(chapter_file, final_text)
            except Exception:  # noqa: BLE001 - 索引失败不影响章节产出
                self.console.print(
                    "[yellow]⚠ RAG 增量索引失败，已跳过（不影响本章产出）[/yellow]"
                )

        # ------ 6.5 M18 清除草稿（F18.4）------
        # 章节已成功持久化，清除草稿
        draft_mgr.clear_draft()

        # ------ 6.6 E2 生成后清除运行时注入的套路（独立存储文件）------
        if self._injected_store.get():
            self._injected_store.clear()

        # ------ 7. 更新进度 ------
        self._update_progress(ctx)

        # ------ 8. 状态转换 ------
        if self.state_machine.state == State.CHARACTER_DESIGN:
            self.state_machine.transition(Event.WRITE)
            self.state_machine.save()

        # ------ 9. 呈现 ------
        self._present(chapter_file, ctx, word_count, quality_passed, revision_attempts)

        # ------ 9.5 M8 介入：章节后等待反馈（heavy 模式） ------
        # 非 heavy 模式直接返回；heavy 模式由 CLI 层处理交互
        # 此处仅记录用户决策到 result，不阻塞流程
        feedback = self.mode_controller.ask_chapter_feedback(
            ctx,
            {
                "word_count": word_count,
                "quality_passed": quality_passed,
            },
        )
        # feedback: accept / revise / rewrite / continue
        # 当前实现：accept/continue 正常返回；revise/rewrite 需用户手动重跑
        # （未来可扩展为循环修订）

        # ------ 9.5 LLMOps：本章用量统计（窗口差值）+ 落盘 ------
        _u1 = _usage_snapshot()
        usage = {
            "chapter": ctx["chapter_num"],
            "llm_calls": max(0, _u1["calls"] - _u0["calls"]),
            "tokens_in": max(0, _u1["tokens_in"] - _u0["tokens_in"]),
            "tokens_out": max(0, _u1["tokens_out"] - _u0["tokens_out"]),
        }
        usage["tokens_total"] = usage["tokens_in"] + usage["tokens_out"]
        if usage["llm_calls"] > 0:
            self.console.print(
                f"[cyan]📊 第 {ctx['chapter_num']} 章用量："
                f"in {usage['tokens_in']:,} / out {usage['tokens_out']:,} tokens"
                f"（{usage['llm_calls']} 次调用）[/cyan]"
            )
        try:
            _llmops_dir = self.project_dir / ".state" / "llmops"
            _llmops_dir.mkdir(parents=True, exist_ok=True)
            (_llmops_dir / "usage_last_chapter.json").write_text(
                _json.dumps(usage, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception:  # noqa: BLE001 - 统计落盘失败不影响章节产出
            pass

        return M5Result(
            chapter_file=chapter_file,
            chapter_num=ctx["chapter_num"],
            chapter_title=chapter_title,
            chapter_text=final_text,
            word_count=word_count,
            quality_passed=quality_passed,
            revision_attempts=revision_attempts,
            quality_report=quality_report,
            evidence_chain=evidence_chain,
            rag_context_len=rag_context_len,
            d_issues=quality_report.get("d_issues", []),
            usage=usage,
        )

    # ============================================================
    # 2. 章节生成
    # ============================================================
    def _generate_chapter(
        self, ctx: dict[str, Any], injected_tropes_text: str = ""
    ) -> str:
        """调 LLM 生成章节正文

        Args:
            ctx: 上下文
            injected_tropes_text: E2 运行时注入的题材套路文本（追加到 system prompt）
        """
        wi = ctx["world_info"]
        from agent.core.infra.prompt_helpers import format_open_debts, format_rag_context

        rag_context_text = format_rag_context(ctx.get("rag_context", []))
        open_debts_text = format_open_debts(ctx.get("open_debts", []))
        user_prompt = pm.get("m5.generate").render_user(
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

        # E2 题材动态注入：将选中套路以 System Prompt 片段注入
        system_prompt = pm.get("m5.generate").render_system(genre=wi.get("genre_label", ""))
        if injected_tropes_text:
            system_prompt = (
                system_prompt
                + "\n\n【本章注入套路（运行时指定，请自然融入章节结构与标志性要素，"
                "不要生硬堆砌）】\n"
                + injected_tropes_text
            )

        # E：项目学习记忆注入 System Prompt（长期保留、不清空；类似 injected tropes）
        learnings_text = ctx.get("learnings_text", "")
        if learnings_text and learnings_text != "（暂无已沉淀的写法记忆）":
            system_prompt = (
                system_prompt
                + "\n\n【本项目已沉淀的写法记忆（长期积累，请自然融入本章，"
                "不要生硬堆砌）】\n"
                + learnings_text
            )

        # ---- G15：连续性账本投影注入（写前输入；缺账本 → 跳过，不阻断）----
        continuity_projection = (ctx.get("continuity_projection") or "").strip()
        if continuity_projection:
            system_prompt = (
                system_prompt
                + "\n\n【连续性账本投影（已定事实/未闭环/上章交接，请遵守，"
                "不要与之冲突）】\n"
                + continuity_projection
            )

        # ---- B1：写章防模板注入（本卷已用手段清单 + 灭门回忆计数；只约束字数/花式，不硬删）----
        reuse_guard_text = (ctx.get("reuse_guard_text") or "").strip()
        if reuse_guard_text:
            system_prompt = (
                system_prompt
                + "\n\n【本节为防模板的运行时提醒（参考，若与情节冲突以情节为准）】\n"
                + reuse_guard_text
            )

        # ---- G8（补充边界 4）：结局模式指令注入（ending 为空降级「收尾」通用指令，不阻断）----
        if ctx.get("ending_mode"):
            ending = (ctx.get("ending") or "").strip()
            if ending:
                system_prompt = system_prompt + pm.get("g8.ending_instruction").render_user(
                    subline_id=ctx.get("subline_id", ""),
                    mainline="、".join(ctx.get("mainline", []) or []) or "—",
                    ending=ending,
                )
            else:
                system_prompt = system_prompt + pm.get("g8.ending_fallback_instruction").render_user()

        # ---- G11：风格指引注入（style.md 存在即注入；缺失/关闭 → 与 G10 输出逐字节一致）----
        style_guide = (ctx.get("style_guide") or "").strip()
        if style_guide:
            system_prompt = system_prompt + pm.get("g11.style_instruction").render_user(
                style_guide=style_guide
            )

        # ---- G12：爽点剧本 + 情绪目标 + 读者反馈注入（追加顺序：爽点 → 情绪 → 反馈）----
        payoff_task = (ctx.get("payoff_task") or "").strip()
        if payoff_task:
            system_prompt = system_prompt + pm.get("g12.payoff_instruction").render_user(
                payoff_task=payoff_task
            )
        emotion_target = (ctx.get("emotion_target") or "").strip()
        if emotion_target:
            system_prompt = system_prompt + pm.get("g12.emotion_instruction").render_user(
                emotion_target=emotion_target
            )
        signals = ctx.get("reader_signals") or []
        if signals:
            lines = []
            for s in signals:
                desc = str(s.get("desc", "") or "")
                planted = int(s.get("planted_ch", 0) or 0)
                marker = "（位于本章之前，请针对此反馈强化本章）" if planted and planted < ctx.get("chapter_num", 0) else ""
                lines.append(f"- {desc}{marker}")
            if lines:
                system_prompt = system_prompt + pm.get("g12.reader_feedback").render_user(
                    reader_signals="\n".join(lines)
                )

        # ---- 角色状态硬约束（P-C 修复）：把 characters/*.md 的生死/时间线真源注入为不可违背规则 ----
        character_constraints = (ctx.get("character_constraints") or "").strip()
        if character_constraints:
            system_prompt = system_prompt + pm.get("g.character_state_constraint").render_user(
                character_constraints=character_constraints
            )

        resp = chat_creative(
            self.llm,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.85,
            max_tokens=4096,
            enable_thinking=False,
        )
        # 后处理：规范化段落格式（安全网，即使 LLM 遗漏规则 15 也兜底）
        raw = resp.strip()
        return self._format_chapter_body(raw)

