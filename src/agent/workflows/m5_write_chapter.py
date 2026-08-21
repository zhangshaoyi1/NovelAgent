"""M5 章节创作工作流

基于 PRD F5.1-F5.4，实现单章生成闭环：
    1. 7 步上下文加载（world→subline→route node→relations→characters→foreshadows→题材规则）
    2. LLM 生成章节正文（创作模型，高温度）
    3. LLM 质量校验（校验模型，低温度，9 项通用层规则）
    4. 未通过则自动修订（≤ MAX_REVISIONS 次）
    5. 持久化章节文件 chapters/ch<NNN>.md（含 frontmatter 依据链）
    6. 更新进度指针（state.json progress）

状态转换：CHARACTER_DESIGN → WRITING（首次）/ WRITING → WRITING（后续）
门禁：architecture.confirmed == true
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import frontmatter
from rich.console import Console
from rich.panel import Panel

from agent.core.conflict_service import ConflictArbiter, ConflictReport
from agent.core.evidence_chain import EvidenceChain, EvidenceRef
from agent.core.exceptions import PreValidationBlocked
from agent.core.genre_pack import GenrePackRegistry
from agent.core.quality_checker import QualityChecker, LLMBackedChecker, Severity
from agent.core.injected_trope_store import InjectedTropeStore
from agent.core.llm_client import LLMClient
from agent.core.method_style import load_style_guide  # G11：风格指引读取
from agent.core.setting_manager import SettingManager
from agent.core.state_machine import Event, State, StateMachine
from agent.core.confirmation import is_architecture_confirmed
from agent.prompts import (
    M5_GENERATE_SYSTEM_PROMPT,
    M5_GENERATE_USER_TEMPLATE,
    M5_QUALITY_CHECK_SYSTEM_PROMPT,
    M5_QUALITY_CHECK_USER_TEMPLATE,
    M5_REVISE_SYSTEM_PROMPT,
    M5_REVISE_USER_TEMPLATE,
    G8_ENDING_INSTRUCTION_TEMPLATE,  # G8（补充边界 4）：结局阶段指令（含架构 ending）
    G8_ENDING_FALLBACK_INSTRUCTION,  # G8（补充边界 4）：ending 为空降级「收尾」通用指令
    G11_STYLE_INSTRUCTION_TEMPLATE,  # G11：风格指引（project/style.md 注入）
    G12_PAYOFF_INSTRUCTION_TEMPLATE,  # G12：爽点剧本（.state/payoff_script.json 注入）
    G12_EMOTION_INSTRUCTION_TEMPLATE,  # G12：情绪目标（本章节奏落点）
    G12_READER_FEEDBACK_TEMPLATE,  # G12：读者反馈（reader_feedback 债务注入）
)
from agent.utils import parse_llm_json

MAX_REVISIONS = 2


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


@dataclass
class PreValidationResult:
    """E3 前置冲突检测结论"""

    decision: str  # "continue" | "interrupt"
    report: ConflictReport
    auto_resolved: list[str] = field(default_factory=list)


class M5WriteChapterWorkflow:
    """M5 章节创作工作流"""

    def __init__(
        self,
        project_dir: Path,
        llm_client: LLMClient | None = None,
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
    ) -> None:
        self.project_dir = Path(project_dir)
        self.llm = llm_client or LLMClient()
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

    @property
    def mode_controller(self) -> "ModeController":
        """懒加载 ModeController（M8）"""
        if self._mode_controller is None:
            from agent.workflows.m8_mode import ModeController

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
            from agent.workflows.m18_recovery import check_draft_on_startup

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
        chapter_text = self._generate_chapter(
            ctx, injected_tropes_text=injected_tropes_text
        )

        # ------ 2.5 M18 保存草稿（F18.4）------
        # 生成后、持久化前保存草稿，中断时可恢复
        from agent.workflows.m18_recovery import DraftManager

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

        # ------ 4. 提取章节标题 ------
        chapter_title = self._extract_title(final_text, ctx)

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
        )

    # ============================================================
    # 1. 上下文加载（7 步读取）
    # ============================================================
    def _load_context(self) -> dict[str, Any]:
        """F5.1 七步上下文加载"""
        # Step 1: world.md
        world_data = self.sm.load_world()
        if not world_data["exists"]:
            raise RuntimeError("world.md 不存在，请先运行 M1 配置")
        world_info = self._extract_world_info(world_data)

        # Step 2: 当前 subline.md
        progress = self.state_machine.progress or {}
        subline_id = progress.get("current_subline", "")
        if not subline_id:
            # 首次：取第一个支线
            sublines = self.sm.list_sublines()
            if not sublines:
                raise RuntimeError("没有支线，请先运行 /outline 生成大纲")
            subline_id = sublines[0]
        subline_data = self.sm.load_subline(subline_id)
        if not subline_data["exists"]:
            raise RuntimeError(f"支线 {subline_id} 的 subline.md 不存在")

        # Step 3: 主角路线当前节点
        route_info = self._load_route_node(progress)

        # Step 4: 关系网
        relations_info = self._load_relations()

        # Step 5: 本章涉及角色
        characters_info, characters_fingerprint = self._load_characters(subline_data)

        # Step 6: 伏笔任务
        foreshadow_task = self._load_foreshadow_task(progress)

        # Step 7: 题材层质量规则（MVP 内置修仙）
        # — 已在 prompt 中编码

        # 章节号
        chapter_num = progress.get("total_written", 0) + 1

        # 前情提要
        prev_summary = self._load_prev_summary(chapter_num)

        # 压力曲线阶段
        pressure_stage, tension_level = self._determine_pressure_stage(
            subline_data, chapter_num
        )

        # A：RAG 语义召回（仅当 .state/rag/ 已建立；否则空，绝不阻断写章）
        rag_dir = self.project_dir / ".state" / "rag"
        rag_context: list = []
        if rag_dir.exists():
            try:
                from agent.core.rag.retriever import Retriever

                subline_goal = self._extract_section(subline_data["content"], "支线目标")
                query = (
                    f"第{chapter_num}章 "
                    f"{subline_data['metadata'].get('subline_name', subline_id)} "
                    f"{subline_goal} {pressure_stage}"
                )
                rag_context = Retriever(self.project_dir).retrieve(query, top_k=5)
            except Exception:  # noqa: BLE001 - RAG 失败降级为空，不影响写章
                rag_context = []

        # C：追读力账本中的开放债务（缺账本则空，不阻断写章）
        open_debts: list = []
        reader_signals: list = []  # G12：读者反馈信号（kind=reader_feedback 分离，其余维持既有行为）
        try:
            from agent.core.pacing_store import PacingStore

            all_debts = PacingStore(self.project_dir).get_open_debts(n=50)
            open_debts = [
                {"id": d.id, "desc": d.desc, "kind": d.kind, "planted_ch": d.planted_ch}
                for d in all_debts
            ]
            reader_signals = [
                {
                    "desc": d.desc,
                    "planted_ch": d.planted_ch,
                    "id": d.id,
                }
                for d in all_debts
                if d.kind == "reader_feedback"  # G12：kind 字面量扩展（结构零改动）
            ]
        except Exception:  # noqa: BLE001 - 账本读取失败降级为空
            open_debts = []

        # E：项目学习记忆（长期保留，注入生成 prompt；缺则空，不阻断写章）
        learnings: list = []
        learnings_text = "（暂无已沉淀的写法记忆）"
        try:
            from agent.core.learning_store import LearningStore
            from agent.prompts import format_learnings

            # 限额注入（避免 prompt 膨胀；按存储顺序取前 20 条）
            capped = LearningStore(self.project_dir).load()[:20]
            learnings = [
                {
                    "id": x.id,
                    "category": x.category,
                    "text": x.text,
                    "source_chapters": x.source_chapters,
                }
                for x in capped
            ]
            learnings_text = format_learnings(capped)
        except Exception:  # noqa: BLE001 - 学习记忆读取失败降级为空
            learnings = []
            learnings_text = "（暂无已沉淀的写法记忆）"

        # ---- G12：本章爽点剧本 + 情绪目标（缺失/损坏/关闭 → ""）----
        _payoff_task, _emotion_target = "", ""
        if getattr(self, "payoff_enabled", True):  # 默认开；--no-payoff 关闭
            try:
                from agent.core.payoff_script import chapter_payoff, load_payoff_script

                _script = load_payoff_script(self.project_dir, enabled=True)
                _payoff_task, _emotion_target = chapter_payoff(_script, chapter_num)
            except Exception:  # noqa: BLE001 - 剧本读取失败降级为空
                pass

        return {
            "world_info": world_info,
            "subline_id": subline_id,
            "subline_name": subline_data["metadata"].get("subline_name", subline_id),
            "subline_goal": self._extract_section(subline_data["content"], "支线目标"),
            "pressure_stage": pressure_stage,
            "tension_level": tension_level,
            "chapter_num": chapter_num,
            "route_node_id": route_info["node_id"],
            "route_milestone": route_info["milestone"],
            "route_main_title": route_info["main_title"],
            "route_main_result": route_info["main_result"],
            "route_main_growth": route_info["main_growth"],
            "characters_info": characters_info,
            "characters_fingerprint": characters_fingerprint,
            "relations_info": relations_info,
            "foreshadow_task": foreshadow_task,
            "prev_chapter_summary": prev_summary,
            "rag_context": rag_context,
            "open_debts": open_debts,
            "learnings": learnings,
            "learnings_text": learnings_text,
            # ---- G8（补充边界 4）：结局/主线上下文注入 ----
            "ending": self._load_architecture_ending(),  # architecture.md frontmatter（空串=降级）
            "ending_mode": bool(progress.get("ending_mode", False)),  # 是否结局模式
            "mainline": list(progress.get("mainline_visited", []) or []),  # 已访问支线
            # ---- G11：风格指引（project/style.md；--no-style 或缺失 → ""）----
            "style_guide": load_style_guide(
                self.project_dir, self.style_enabled, self.style_file
            ),
            # ---- G12（读者反馈闭环）：爽点剧本 / 情绪目标 / 读者反馈 ----
            "payoff_task": _payoff_task,
            "emotion_target": _emotion_target,
            "reader_signals": reader_signals,
        }

    def _load_architecture_ending(self) -> str:
        """读 architecture.md frontmatter 的 architecture.ending（m14 行 447/460 写入）。

        读失败/缺失 → 返回 ""（降级不阻断，拍板 2/补充边界 4）。
        """
        try:
            f = self.project_dir / "architecture.md"
            if not f.exists():
                return ""
            post = frontmatter.load(f)
            arch = post.metadata.get("architecture", {}) or {}
            return str(arch.get("ending", "") or "").strip()
        except Exception:  # noqa: BLE001 - 读失败降级为空
            return ""

    def _extract_world_info(self, world_data: dict[str, Any]) -> dict[str, Any]:
        metadata = world_data.get("metadata", {}) or {}
        content = world_data.get("content", "")
        style = metadata.get("style", {}) or {}

        # 故事简介
        synopsis = self._extract_section(content, "故事简介") or ""

        # 境界体系
        realm_system = self._extract_section(content, "境界体系") or ""

        # 金手指
        golden_finger = self._extract_section(content, "金手指登记") or ""

        return {
            "title": metadata.get("title", ""),
            "scope": metadata.get("scope", ""),
            "genre": metadata.get("genre", ""),
            "tone": style.get("tone", ""),
            "pov": style.get("pov", ""),
            "rhythm": style.get("rhythm", ""),
            "chapter_length": style.get("chapter_length", 3000),
            "info_density": style.get("info_density", ""),
            "banned_elements": style.get("banned_elements", []),
            "synopsis": synopsis,
            "realm_system": realm_system,
            "golden_finger_info": golden_finger,
        }

    def _load_route_node(self, progress: dict[str, Any]) -> dict[str, str]:
        """从 protagonist_route.md 读取当前章节对应的节点"""
        route_file = self.project_dir / "protagonist_route.md"
        if not route_file.exists():
            return {"node_id": "", "milestone": "", "main_title": "", "main_result": "", "main_growth": ""}

        text = route_file.read_text(encoding="utf-8")
        chapter_num = progress.get("total_written", 0) + 1

        # 按 ## NXX 分段
        blocks = re.split(r"\n## (N\d+)", text)
        # blocks = ["前置", "N01", "N01内容", "N02", "N02内容", ...]
        for i in range(1, len(blocks), 2):
            node_id = blocks[i]
            block = blocks[i + 1] if i + 1 < len(blocks) else ""
            # 提取章节范围
            range_match = re.search(r"章节范围[：:]\s*(\d+)[-~](\d+)", block)
            if range_match:
                lo = int(range_match.group(1))
                hi = int(range_match.group(2))
                if lo <= chapter_num <= hi:
                    milestone = re.search(r"## N\d+ · (.+)", block)
                    milestone_str = milestone.group(1).strip() if milestone else ""
                    # 主分支
                    main_title = ""
                    main_result = ""
                    main_growth = ""
                    main_match = re.search(r"### 主分支 · (.+)", block)
                    if main_match:
                        main_title = main_match.group(1).strip()
                    result_match = re.search(r"\*\*结果\*\*[：:]\s*(.+)", block)
                    if result_match:
                        main_result = result_match.group(1).strip()
                    growth_match = re.search(r"\*\*成长\*\*[：:]\s*(.+)", block)
                    if growth_match:
                        main_growth = growth_match.group(1).strip()
                    return {
                        "node_id": node_id,
                        "milestone": milestone_str,
                        "main_title": main_title,
                        "main_result": main_result,
                        "main_growth": main_growth,
                    }
        # 没匹配到范围，取第一个节点
        if len(blocks) >= 3:
            block = blocks[2]
            milestone = re.search(r"## N\d+ · (.+)", block)
            return {
                "node_id": blocks[1],
                "milestone": milestone.group(1).strip() if milestone else "",
                "main_title": "",
                "main_result": "",
                "main_growth": "",
            }
        return {"node_id": "", "milestone": "", "main_title": "", "main_result": "", "main_growth": ""}

    def _load_relations(self) -> str:
        """读取 relations/graph.md"""
        graph_file = self.project_dir / "relations" / "graph.md"
        if not graph_file.exists():
            return "（关系网未生成）"
        return graph_file.read_text(encoding="utf-8")[:1500]

    def _load_characters(
        self, subline_data: dict[str, Any]
    ) -> tuple[str, str]:
        """读取本章涉及角色的 character.md"""
        # 从 subline.md 的出场角色字段获取角色列表
        raw_chars = subline_data["metadata"].get("characters") or subline_data["metadata"].get("出场角色")
        if not raw_chars:
            # 从 content 提取
            content = subline_data.get("content", "")
            section = self._extract_section(content, "出场角色")
            if section:
                raw_chars = section

        names: list[str] = []
        if isinstance(raw_chars, list):
            names = [str(n) for n in raw_chars]
        elif isinstance(raw_chars, str):
            # 去括号、引号、逗号
            cleaned = raw_chars.strip("[]'\" ")
            names = [n.strip("'\" ") for n in cleaned.split(",") if n.strip()]

        chars_dir = self.project_dir / "characters"
        info_parts: list[str] = []
        fingerprint_parts: list[str] = []

        # 如果没提取到角色名，加载所有角色
        if not names and chars_dir.exists():
            names = [p.stem for p in chars_dir.glob("*.md")]

        for name in names[:6]:  # 最多 6 个
            # 尝试加载
            char_data = self.sm.load_character(name)
            if not char_data["exists"]:
                # 模糊匹配
                if chars_dir.exists():
                    for p in chars_dir.glob("*.md"):
                        if name in p.stem or p.stem in name:
                            char_data = self.sm.load_character(p.stem)
                            break
            if char_data["exists"]:
                content = char_data["content"]
                # 提取内核摘要
                motivation = self._extract_section(content, "核心动机") or ""
                identity = char_data["metadata"].get("identity", "")
                info_parts.append(f"- **{name}**（{char_data['metadata'].get('role','')}）：{identity}。动机：{motivation[:80]}")

                # 语言指纹
                catchphrase = self._extract_field(content, "口头禅")
                sentence_style = self._extract_field(content, "句式偏好")
                fingerprint_parts.append(f"- {name}：口头禅「{catchphrase}」| 句式：{sentence_style}")
            else:
                info_parts.append(f"- **{name}**（角色档案未找到）")

        return (
            "\n".join(info_parts) if info_parts else "（无角色信息）",
            "\n".join(fingerprint_parts) if fingerprint_parts else "（无语言指纹）",
        )

    def _load_foreshadow_task(self, progress: dict[str, Any]) -> str:
        """读取 foreshadows.md，检查本章是否需埋/回收伏笔"""
        f_file = self.project_dir / "foreshadows.md"
        if not f_file.exists():
            return "（伏笔表未生成）"

        text = f_file.read_text(encoding="utf-8")
        chapter_num = progress.get("total_written", 0) + 1

        # ---- G8（拍板 5）：结局段「回收优先 + 禁新埋长线」 ----
        if progress.get("ending_mode"):
            open_items: list[str] = []
            for line in text.splitlines():
                if line.startswith("| F-"):
                    parts = [p.strip() for p in line.split("|")]
                    # 同源解析：状态列 = parts[5]（split 后第 6 个元素），
                    # 与 evaluator_agent._metric_foreshadow_recycle（cells[4]）同表同列语义（共享知识 #12）
                    if len(parts) >= 7 and parts[5] in ("未埋", "已埋"):
                        open_items.append(
                            f"  可回收 {parts[1]}：{parts[2]}（预期回收：{parts[4]}）"
                        )
            tasks = ["  ★ 结局阶段：本章强制回收 ≥1 条未回收伏笔"]
            tasks.extend(open_items[:3])  # 最多列 3 条，避免 prompt 膨胀
            tasks.append("  ★ 结局阶段：禁止新埋长线伏笔；短线（1-2 章内可自然回收）允许。")
            return "本章伏笔任务：\n" + "\n".join(tasks)

        # ---- 非结局段：既有逻辑原样（每 10 章强制埋/回收）----
        # 找本章应埋的伏笔（planted_at 包含当前章节号）
        tasks: list[str] = []
        for line in text.splitlines():
            if line.startswith("| F-"):
                parts = [p.strip() for p in line.split("|")]
                if len(parts) >= 7:
                    fid, content, planted_at, expected, state, related = parts[1:7]
                    # 检查 planted_at 是否匹配本章
                    if f"ch{chapter_num:03d}" in planted_at or f"ch{chapter_num}" in planted_at:
                        if state == "未埋":
                            tasks.append(f"  埋设 {fid}：{content}（预期回收：{expected}）")
                    # 每 10 章强制埋 1 条 + 回收 1 条
                    if chapter_num % 10 == 0 and state == "已埋":
                        tasks.append(f"  可回收 {fid}：{content}")

        # 每 10 章强制提示
        if chapter_num % 10 == 0:
            tasks.append("  ★ 本章为第 {}0 章，强制埋 ≥1 长线伏笔、回收 ≥1 旧伏笔".format(chapter_num // 10))

        if not tasks:
            return "本章无强制伏笔任务。自然写作即可，如有合适时机可埋设新伏笔。"
        return "本章伏笔任务：\n" + "\n".join(tasks)

    def _load_prev_summary(self, chapter_num: int) -> str:
        """读取上一章的摘要（从 chapter 文件提取前 200 字）"""
        if chapter_num <= 1:
            return "（第一章，无前情）"
        prev_file = self.chapters_dir / f"ch{chapter_num - 1:03d}.md"
        if not prev_file.exists():
            return "（上一章文件未找到）"
        text = prev_file.read_text(encoding="utf-8")
        # 去掉 frontmatter
        if text.startswith("---"):
            parts = text.split("---", 2)
            if len(parts) >= 3:
                text = parts[2]
        return text.strip()[:300] + "..."

    def _determine_pressure_stage(
        self, subline_data: dict[str, Any], chapter_num: int
    ) -> tuple[str, str]:
        """从 subline.md 的压力曲线表确定当前阶段"""
        content = subline_data.get("content", "")
        # 查找压力曲线表
        section = self._extract_section(content, "剧集压力曲线")
        if not section:
            return "铺垫", "低"

        # 解析表格行 | 阶段 | 章节 | 张力等级 |
        for line in section.splitlines():
            if line.startswith("|") and "阶段" not in line and "---" not in line:
                parts = [p.strip() for p in line.split("|")]
                if len(parts) >= 4:
                    stage = parts[1]
                    range_str = parts[2]
                    tension = parts[3]
                    # 解析范围 "1-50" 或 "51-200"
                    range_match = re.match(r"(\d+)[-~](\d+)", range_str)
                    if range_match:
                        lo = int(range_match.group(1))
                        hi = int(range_match.group(2))
                        if lo <= chapter_num <= hi:
                            return stage, tension
        return "铺垫", "低"

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
        from agent.prompts import format_open_debts, format_rag_context

        rag_context_text = format_rag_context(ctx.get("rag_context", []))
        open_debts_text = format_open_debts(ctx.get("open_debts", []))
        user_prompt = M5_GENERATE_USER_TEMPLATE.format(
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
        system_prompt = M5_GENERATE_SYSTEM_PROMPT
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

        # ---- G8（补充边界 4）：结局模式指令注入（ending 为空降级「收尾」通用指令，不阻断）----
        if ctx.get("ending_mode"):
            ending = (ctx.get("ending") or "").strip()
            if ending:
                system_prompt = system_prompt + G8_ENDING_INSTRUCTION_TEMPLATE.format(
                    subline_id=ctx.get("subline_id", ""),
                    mainline="、".join(ctx.get("mainline", []) or []) or "—",
                    ending=ending,
                )
            else:
                system_prompt = system_prompt + G8_ENDING_FALLBACK_INSTRUCTION

        # ---- G11：风格指引注入（style.md 存在即注入；缺失/关闭 → 与 G10 输出逐字节一致）----
        style_guide = (ctx.get("style_guide") or "").strip()
        if style_guide:
            system_prompt = system_prompt + G11_STYLE_INSTRUCTION_TEMPLATE.format(
                style_guide=style_guide
            )

        # ---- G12：爽点剧本 + 情绪目标 + 读者反馈注入（追加顺序：爽点 → 情绪 → 反馈）----
        payoff_task = (ctx.get("payoff_task") or "").strip()
        if payoff_task:
            system_prompt = system_prompt + G12_PAYOFF_INSTRUCTION_TEMPLATE.format(
                payoff_task=payoff_task
            )
        emotion_target = (ctx.get("emotion_target") or "").strip()
        if emotion_target:
            system_prompt = system_prompt + G12_EMOTION_INSTRUCTION_TEMPLATE.format(
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
                system_prompt = system_prompt + G12_READER_FEEDBACK_TEMPLATE.format(
                    reader_signals="\n".join(lines)
                )

        resp = self.llm.chat_creative(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.85,
            max_tokens=4096,
            enable_thinking=False,
        )
        return resp.text.strip()

    # ============================================================
    # 3. 质量校验 + 自动修订
    # ============================================================
    def _quality_check_and_revise(
        self, ctx: dict[str, Any], chapter_text: str
    ) -> tuple[dict[str, Any], int, str]:
        """质量校验 + ≤MAX_REVISIONS 次自动修订

        Returns:
            (final_quality_report, revision_attempts, final_text)
        """
        wi = ctx["world_info"]
        is_climax = ctx["pressure_stage"] == "高潮"
        report: dict[str, Any] = {}
        attempts = 0
        text = chapter_text
        # D：多维 LLM 审查问题（仅 strict_review 时填充，随最后一次校验落入 report）
        last_d_issues: list[dict[str, Any]] = []

        for attempt in range(MAX_REVISIONS + 1):
            # 校验
            check_prompt = M5_QUALITY_CHECK_USER_TEMPLATE.format(
                tone=wi["tone"],
                chapter_length=wi["chapter_length"],
                characters_fingerprint=ctx["characters_fingerprint"],
                is_climax="是" if is_climax else "否",
                chapter_text=text,
            )

            # T-3：追加题材层质量规则（取自题材包 quality-rules.md），强化题材专属校验
            genre = wi.get("genre", "")
            if genre:
                if self._genre_registry is None:
                    self._genre_registry = GenrePackRegistry()
                try:
                    genre_rules_text = self._genre_registry.load(genre).quality_rules
                except ValueError:
                    genre_rules_text = ""
                if genre_rules_text:
                    check_prompt = (
                        check_prompt
                        + "\n\n【题材层质量规则（" + genre + "）】\n"
                        + genre_rules_text
                    )
            resp = self.llm.chat_utility(
                messages=[
                    {"role": "system", "content": M5_QUALITY_CHECK_SYSTEM_PROMPT},
                    {"role": "user", "content": check_prompt},
                ],
                max_tokens=1500,
                enable_thinking=False,
            )
            try:
                report = parse_llm_json(resp.text)
            except ValueError:
                report = {"overall_pass": True, "rules": [], "suggestions": "校验解析失败，默认通过"}

            # D：多维 LLM 质量审查（仅当 strict_review 开启；并入同一 revise_loop 预算）
            # 合并为单次 chat_utility 调用，维度 blocking 视为本章未通过、触发既有修订循环。
            if self.strict_review:
                last_d_issues = self._run_d_review(text, ctx)
                report["d_issues"] = last_d_issues
                d_blocking = any(
                    i.get("severity") == Severity.BLOCK.value for i in last_d_issues
                )
                report["d_blocking"] = d_blocking
                if d_blocking:
                    report["overall_pass"] = False

            if report.get("overall_pass", False):
                break

            # 未通过 → 修订
            if attempt < MAX_REVISIONS:
                # ---- G9：章内子阶段事件（修订）----
                self._emit_substage("revise", ctx["chapter_num"])
                self.console.print(
                    f"  [yellow]质量校验未通过（第 {attempt + 1} 次修订）...[/yellow]"
                )
                revise_prompt = M5_REVISE_USER_TEMPLATE.format(
                    quality_report=resp.text,
                    chapter_text=text,
                )
                rev_resp = self.llm.chat_creative(
                    messages=[
                        {"role": "system", "content": M5_REVISE_SYSTEM_PROMPT},
                        {"role": "user", "content": revise_prompt},
                    ],
                    temperature=0.6,
                    max_tokens=4096,
                    enable_thinking=False,
                )
                text = rev_resp.text.strip()
                attempts = attempt + 1

        # T-5：可选启用结构化质量校验（仅补充，不阻断主路径 LLM 校验）
        if getattr(self, "enable_structured_qc", False):
            try:
                checker = QualityChecker(self.project_dir, self.llm)
                structured = checker.check(text, ctx)
                report["structured_issues"] = [
                    {
                        "rule_id": issue.rule_id,
                        "severity": issue.severity.value,
                        "description": issue.description,
                    }
                    for issue in structured.issues
                ]
            except Exception:  # noqa: BLE001 - 结构化校验失败不影响主路径
                report.setdefault("structured_issues", [])

        return report, attempts, text

    # ============================================================
    # 3.5 D：多维 LLM 质量审查（并入 revise_loop）
    # ============================================================
    def _run_d_review(self, text: str, ctx: dict[str, Any]) -> list[dict[str, Any]]:
        """D：用 LLMBackedChecker 合并评审 4 个网文维度（爽点/OOC/连贯性/追读力）

        合并为单次 ``chat_utility`` 调用；LLM 不可用 / 调用异常 / 超时均降级为空
        （放行 + 记录，绝不阻断写章）。返回可序列化的 issue 字典列表。

        Args:
            text: 当前章节正文
            ctx: 上下文

        Returns:
            ``[{"rule_id", "severity", "description"}, ...]``；降级时为空列表。
        """
        try:
            checker = LLMBackedChecker(self.llm)
            issues = checker.run_rules(self._qc.llm_rules, text, ctx)
        except Exception:  # noqa: BLE001 - D 审查失败降级为空，不阻断主路径
            return []
        return [
            {
                "rule_id": i.rule_id,
                "severity": i.severity.value,
                "description": i.description,
            }
            for i in issues
        ]

    # ============================================================
    # 4. 章节标题
    # ============================================================
    def _extract_title(self, text: str, ctx: dict[str, Any]) -> str:
        """从正文第一行提取标题，或生成默认"""
        first_line = text.strip().split("\n")[0].strip()
        # 去掉 markdown 标题标记
        first_line = re.sub(r"^#+\s*", "", first_line)
        # 如果太长（>20字），截取
        if len(first_line) > 30:
            first_line = first_line[:30] + "..."
        if not first_line or first_line.startswith("第"):
            return f"第{ctx['chapter_num']}章"
        return first_line

    # ============================================================
    # 5. 依据链
    # ============================================================
    def _build_evidence_chain(self, ctx: dict[str, Any]) -> EvidenceChain:
        """构建本章引用的设定条目（E4 结构化分类引用）

        分类：
            - settings：世界观 / 境界 / 金手指 / 支线 / 路线 / 关系网
            - characters：本章涉及角色档案
            - foreshadows：伏笔登记表 + 本章伏笔任务涉及的 F-ID
        """
        wi = ctx["world_info"]
        settings: list[EvidenceRef] = [
            EvidenceRef(name=wi.get("title", ""), field="世界观/故事简介", source="world.md"),
        ]
        if wi.get("realm_system"):
            settings.append(
                EvidenceRef(name="境界体系", field="境界体系（冻结）", source="world.md")
            )
        if wi.get("golden_finger_info"):
            settings.append(EvidenceRef(name="金手指", field="金手指登记", source="world.md"))
        settings.append(
            EvidenceRef(
                name=ctx["subline_name"],
                field="支线目标",
                source=f"sublines/{ctx['subline_id']}/subline.md",
            )
        )
        settings.append(
            EvidenceRef(
                name=ctx["route_node_id"],
                field="主角路线节点",
                source="protagonist_route.md",
            )
        )
        settings.append(
            EvidenceRef(name="关系网", field="关系当前状态", source="relations/graph.md")
        )

        characters: list[EvidenceRef] = []
        for line in ctx["characters_info"].splitlines():
            m = re.search(r"\*\*(.+?)\*\*", line)
            if m:
                name = m.group(1).strip()
                characters.append(
                    EvidenceRef(name=name, field="身份/动机", source=f"characters/{name}.md")
                )

        foreshadows: list[EvidenceRef] = [
            EvidenceRef(name="伏笔登记表", field="全局伏笔", source="foreshadows.md"),
        ]
        for fid in re.findall(r"F-\d+", ctx.get("foreshadow_task", "")):
            foreshadows.append(
                EvidenceRef(ref_id=fid, field="本章伏笔任务", source="foreshadows.md")
            )

        return EvidenceChain(characters=characters, foreshadows=foreshadows, settings=settings)

    # ============================================================
    # 6. 持久化
    # ============================================================
    def _save_chapter(
        self,
        ctx: dict[str, Any],
        text: str,
        title: str,
        word_count: int,
        quality_passed: bool,
        revision_attempts: int,
        evidence_chain: EvidenceChain,
    ) -> Path:
        """保存章节文件（frontmatter 含 E4 结构化证据链）"""
        self.chapters_dir.mkdir(parents=True, exist_ok=True)
        file = self.chapters_dir / f"ch{ctx['chapter_num']:03d}.md"

        metadata = {
            "chapter": ctx["chapter_num"],
            "subline": ctx["subline_id"],
            "route_node": ctx["route_node_id"],
            "pressure_stage": ctx["pressure_stage"],
            "title": title,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "word_count": word_count,
            "quality_passed": quality_passed,
            "revision_attempts": revision_attempts,
            "evidence_chain": evidence_chain.to_dict(),
        }
        body = f"# 第 {ctx['chapter_num']} 章 · {title}\n\n{text}"
        post = frontmatter.Post(body, **metadata)
        file.write_text(frontmatter.dumps(post), encoding="utf-8")
        return file

    # ============================================================
    # E2 题材动态注入
    # ============================================================
    def _collect_injected_tropes(self, ctx: dict[str, Any]) -> str:
        """读取运行时注入的套路列表，提取对应套路模板文本（F-E2.2）

        套路列表来自独立的 ``.state/injected_tropes.json``（运行期上下文），
        不污染持久化状态。返回拼接后的套路文本；无注入时返回空字符串。
        """
        trope_names = self._injected_store.get()
        if not trope_names:
            return ""

        if self._genre_registry is None:
            self._genre_registry = GenrePackRegistry()

        genre = self.sm.load_world()["metadata"].get("genre", "") or ctx["world_info"].get(
            "genre", ""
        )
        parts: list[str] = []
        for name in trope_names:
            try:
                trope = self._genre_registry.load_trope(genre, name)
                parts.append(f"### {trope.name}\n{trope.text}")
            except ValueError as e:
                self.console.print(f"[yellow]⚠ 注入套路失败（{name}）：{e}[/yellow]")
        return "\n\n".join(parts)

    # ============================================================
    # E3 前置式冲突检测与仲裁
    # ============================================================
    def _build_planned_setting(self, ctx: dict[str, Any]) -> str:
        """构建本章"计划设定变更"文本，供冲突检测门禁使用"""
        wi = ctx["world_info"]
        return (
            "【本章计划设定变更】\n"
            f"支线：{ctx['subline_name']}（{ctx['subline_goal']}）\n"
            f"主角路线节点：{ctx['route_node_id']}｜里程碑：{ctx['route_milestone']}\n"
            f"主线结果预期：{ctx['route_main_result']}\n"
            f"成长预期：{ctx['route_main_growth']}\n"
            f"涉及角色：{wi.get('title', '')}\n"
            f"伏笔任务：{ctx['foreshadow_task']}\n"
            f"题材：{wi.get('genre', '')}"
        )

    def _pre_validation(self, ctx: dict[str, Any]) -> PreValidationResult:
        """E3 前置冲突检测门禁

        Returns:
            PreValidationResult：
                - 无冲突 → continue
                - 高严重度冲突 → interrupt（需用户仲裁）
                - 低/中冲突 → 自动仲裁（写入 world.md 修订日志）后 continue
        """
        planned = self._build_planned_setting(ctx)
        report = self.conflict_arbiter.check_new_setting(  # type: ignore[union-attr]
            planned, subline_id=ctx["subline_id"]
        )

        if not report.has_conflict:
            return PreValidationResult("continue", report)

        if report.needs_arbitration:
            # 高严重度：记录到 world.md 修订日志并中断生成
            high_fields = ", ".join(
                c.field for c in report.conflicts if c.severity == "high"
            )
            self.sm.append_revision_log(
                f"[仲裁-高] 前置冲突检测拦截生成：{report.summary}"
                f"（高严重度字段：{high_fields}）"
            )
            return PreValidationResult("interrupt", report)

        # 低/中严重度：自动采用新设定，记录仲裁结果
        for c in report.conflicts:
            self.sm.append_revision_log(
                f"[仲裁-自动] 字段 {c.field}（{c.severity}）："
                f"{c.suggestion or '自动采用新设定，继续生成'}"
            )
        return PreValidationResult(
            "continue", report, auto_resolved=[c.field for c in report.conflicts]
        )

    # ============================================================
    # E4 证据链校验
    # ============================================================
    def _validate_evidence(self, chain: EvidenceChain) -> EvidenceChain:
        """F-E4.3 落盘前校验所有引用源文件是否存在

        缺失的源仅记录告警，不阻断落盘（引用源本就来自已加载文件）。
        """
        missing: list[str] = []
        for r in chain.all_refs():
            if r.source and not (self.project_dir / r.source).exists():
                missing.append(r.source)
        chain.missing_sources = missing
        if missing:
            self.console.print(
                f"[yellow]⚠ 证据链中有 {len(missing)} 个引用源不存在："
                f"{', '.join(missing)}[/yellow]"
            )
        return chain

    # ============================================================
    # 7. 更新进度
    # ============================================================
    def _update_progress(self, ctx: dict[str, Any]) -> None:
        """更新 state.json 的 progress 字段。

        G8（拍板 4/补充边界 1）关键兼容点：**合并写入**（progress.update），
        保留 mainline_visited / ending_mode / mainline_* / ending_* 等既有键；
        禁止全新 dict 覆盖（否则 G8 状态每次写章被抹掉）。
        """
        self.state_machine.load()
        progress = dict(self.state_machine.progress or {})
        progress.update({
            "current_subline": ctx["subline_id"],
            "current_chapter": ctx["chapter_num"],
            "total_written": ctx["chapter_num"],
            "last_written_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        })
        # G8：mainline_visited 双保险初始化（未记录时以当前支线打底；已存在则保留）
        visited = progress.get("mainline_visited")
        if not isinstance(visited, list):
            visited = []
        if ctx["subline_id"] and ctx["subline_id"] not in visited:
            visited.append(ctx["subline_id"])
        progress["mainline_visited"] = visited
        self.state_machine.progress = progress
        self.state_machine.save()

    # ============================================================
    # 8. 呈现
    # ============================================================
    def _present(
        self,
        chapter_file: Path,
        ctx: dict[str, Any],
        word_count: int,
        quality_passed: bool,
        revision_attempts: int,
    ) -> None:
        """展示章节摘要"""
        status = "[green]✓ 通过[/green]" if quality_passed else "[yellow]△ 未完全通过[/yellow]"
        self.console.print(
            Panel(
                f"第 {ctx['chapter_num']} 章 · {ctx['pressure_stage']}阶段\n"
                f"字数：{word_count} | 质量：{status} | 修订：{revision_attempts} 次\n"
                f"文件：{chapter_file.relative_to(self.project_dir)}",
                title=f"ch{ctx['chapter_num']:03d}.md",
                border_style="green" if quality_passed else "yellow",
                expand=False,
            )
        )

    # ============================================================
    # 工具
    # ============================================================
    @staticmethod
    def _extract_section(content: str, section_name: str) -> str:
        """从 markdown 内容提取 ## 段落"""
        pattern = rf"## {re.escape(section_name)}\s*\n(.*?)(?=\n## |\Z)"
        m = re.search(pattern, content, re.DOTALL)
        return m.group(1).strip() if m else ""

    @staticmethod
    def _extract_field(content: str, field_name: str) -> str:
        """从 markdown 提取 - **字段**：值"""
        pattern = rf"\*\*{re.escape(field_name)}\*\*[：:]\s*(.+)"
        m = re.search(pattern, content)
        return m.group(1).strip() if m else ""
