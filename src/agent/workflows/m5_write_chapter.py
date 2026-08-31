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

from agent.core.infra.prompt_manager import pm
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import frontmatter
from rich.console import Console
from rich.panel import Panel

from agent.core.quality.consistency import ConflictArbiter, ConflictReport
from agent.core.story.evidence_chain import EvidenceChain, EvidenceRef
from agent.core.base.exceptions import PreValidationBlocked
from agent.core.registry.genre_pack import GenrePackRegistry, first_genre, first_genre_label
from agent.core.engine.workflow_registry import workflow
from agent.core.quality.scoring import QualityChecker, LLMBackedChecker, Severity
from agent.core.story.injected_trope_store import InjectedTropeStore
from agent.client import LLMClient
from agent.core.story.method_style import load_style_guide  # G11：风格指引读取
from agent.core.story.setting_manager import SettingManager
from agent.core.story.volume import estimate_chapters  # B 方案：压力曲线回落用真实预计总章数
from agent.core.engine.state_machine import Event, State, StateMachine
from agent.core.quality.guardrails import is_architecture_confirmed
from agent.utils import parse_llm_json
from agent.base.validation import ValidationSpec
logger = logging.getLogger(__name__)

MAX_REVISIONS = 2

# ===== G-EN：正文纯中文硬关卡（确定性扫描，不依赖 LLM 自觉）=====
# 正文出现 2+ 连续「拉丁字母或下划线」即视为英文污染（单字母如 X光/S级 暂放行，避免过度纠偏）。
# 注意：必须至少含一个拉丁字母，避免把纯下划线占位符（如 ________）误判为英文。
_ENGLISH_RUN_RE = re.compile(r"[A-Za-z][A-Za-z_]*[A-Za-z]|[A-Za-z]{2,}")

# 中文连续区间判定用的正则（区间包含 CJK 及常见中文标点）
_CJK_RANGE_RE = re.compile(r"[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]")


def _collapse_cjk_spaces(text: str) -> str:
    """剔除中文字符之间的孤立空格。

    拉丁残留剔除（hard_replace_english 第 2 步）会把原词删掉，却可能在中文字符之间
    留下一个空格（如「发出 的摩擦声」）。这里将两侧都是中文/中文标点的空格直接删除，
    避免成稿出现本应被清理掉、却残留在中文间隙的空白。
    """
    return re.sub(
        r"(?<=" + _CJK_RANGE_RE.pattern + r") +(?=" + _CJK_RANGE_RE.pattern + r")",
        "",
        text,
    )
# 常见英文 token → 中文等价（仅作落盘前确定性兜底；主修复靠 LLM 修订把整句理顺）。
# 注意：值尽量取「独立名词」避免与原句已有中文叠词（如原句已有『认证』就不写『贵宾认证』）。
_ENGLISH_REPLACE_MAP = {
    "VIP": "贵宾", "KPI": "绩效指标", "CEO": "掌权者", "BUG": "漏洞", "bug": "漏洞",
    "IP": "网络地址", "ID": "身份标识", "logo": "标识", "log": "日志",
    "Plan": "备选方案", "NGOs": "国际非政府组织", "allocation_weight": "分配权重",
    "shoulders": "肩背", "loys": "洛城", "kreisel": "陀螺状", "thirty": "三十",
    "Lv": "级", "Lv2": "二级", "Lv3": "三级", "XH": "玄霄", "ZG": "天工", "API": "接口", "AI": "人工智能", "debug": "调试",
    "cache": "缓存", "buffer": "缓冲", "token": "令牌", "node": "节点",
    "DL": "地灵", "JY": "九幽", "LF": "灵链", "TM": "商标", "Street": "街道",
    # 叙事英文泄漏（无歧义内容词，确定性替换）
    "frowned": "皱眉", "already": "已经", "rejected": "拒绝", "please": "请",
    "swallowed": "咽下", "tomorrow": "明日", "darkness": "黑暗", "widest": "最宽",
    "dozens": "数十", "shook": "摇头", "chewing": "咀嚼", "clamp": "夹紧",
    "murmured": "低语", "platinum": "铂金", "slowly": "缓缓", "desperate": "绝望",
    "desperately": "拼命", "flicker": "闪烁", "shake": "晃动", "suddenly": "突然",
    "gossip": "闲言", "tied": "系住", "crimson": "绯红", "temporary": "临时",
    "weakly": "虚弱", "shrugged": "耸肩", "smiling": "微笑", "traps": "陷阱",
    "mixed": "混杂", "whispered": "低声", "screaming": "尖叫", "cheap": "廉价",
    "undergoing": "正经历", "faces": "脸庞", "raises": "抬起", "stabilizing": "稳住",
    "nodded": "点头", "verdict": "裁决", "waiting": "等待",
    "Instead": "反而", "cold": "冰冷", "sharp": "锐利", "interesting": "有趣",
    "shaky": "颤抖", "deeper": "更深", "stolen": "被夺", "lower": "压低",
    "seconds": "秒", "flick": "轻弹", "forward": "向前", "stepping": "迈步",
    "tokens": "令牌", "trembling": "颤抖", "eyes": "眼睛", "reborn": "重生",
    "report": "报告", "reverberating": "回荡", "funnel": "汇聚", "itself": "自身",
    "leverage": "利用", "painpoint": "痛点", "light": "灯光", "nobody": "无人",
    "shadows": "阴影", "IDs": "身份标识",
    "fifty": "五十", "AND": "而且", "silently": "沉默地", "twitch": "抽动",
    "brow": "眉心", "formula": "公式", "auctions": "拍卖会", "payload": "载荷",
    "Leveraged": "杠杆", "fingers": "手指", "distant": "远处", "progress": "进度",
    "Audit": "审计", "grant_token": "授权令牌", "ip_in_whitelist": "白名单内地址",
    "in_maintenance_window": "维护窗口期", "xxxx": "某某",
    "nodded": "点头", "auditorium": "礼堂", "CRC": "校验码",
}
# 修订时给 LLM 的中文等价参考（与上面映射保持一致口径）
_ENGLISH_REPLACE_GUIDE = (
    "正文存在英文单词/变量名/缩写，必须全部改写为自然的中文叙事，不得保留任何拉丁字母词。"
    "中文等价参考：VIP→贵宾认证；CEO→掌权者/总裁；KPI→绩效指标；bug/BUG→漏洞/差错；"
    "IP→网络地址；ID→身份标识；Plan B→备选方案；allocation_weight→分配权重的后门代码；"
    "NGOs→国际非政府组织；logo→标识；shoulders→肩背；loys→（改为中文地名，如洛城）；"
    "kreisel→（德文，改为中文描述，如陀螺状）；thirty→三十；Lv2→二级；XH→玄霄；ZG→天工。"
    "代码/变量名（如 allocation_weight）严禁直接写进正文，必须译为叙事化中文"
    "（如『分配权重的后门代码』）。仅替换英文部分，保持情节/人物/对话/结构完全不变，直接输出完整正文。"
)

# ===== 章节元信息清理（去除 LLM 误输出的标题/批注，保证字数统计与成稿干净）=====
# 开头的 markdown 标题行（模型常自报标题，落盘时由 _save_chapter 统一补「# 第N章 · 书名」）
_HEADING_RE = re.compile(r"^[ \t]*#+\s*.*$")
# 标题提取：『第X章 · 书名』『第X章 书名』等
_TITLE_SPLIT_SEPS = ("·", "．", "。", ":")
_CHAPTER_ORDINAL_RE = re.compile(r"^第\s*(?:\d+|[一二三四五六七八九十百千万]+)\s*章\s*")
# 结尾/任意位置的元信息行：『原文标题：…』『原标题：…』『章节名：…』
_TITLE_META_RE = re.compile(r"^(?:原文标题|原标题|题目|章节名|章节标题)\s*[:：]\s*.*$")
# 整行被（）包裹且含执导/批注词的可疑注记（如『（此处为快节奏场景，黑袍修士……）』）
_EDITOR_NOTE_WHOLE_LINE_RE = re.compile(r"^（[^）]*）$")
# 执导/批注关键词（命中即视为模型自陈述/编辑注记，非正文叙事）
_EDITOR_HINT_WORDS_RE = re.compile(
    r"(此处|本段|这里|这段|应当|应该|避免|为了|符合|压力曲线|铺垫|指导|批注|不要|"
    r"采用|营造|采用短促|禁用词|埋下伏笔|作为揭示|核心)"
)
# 章节收尾标注：『（本章完）』『（全文完）』『（本章结束）』等，LLM 常直接贴在末句/末行后
_END_MARK_RE = re.compile(r"[（(]\s*(?:本章完|全文完|本章结束)\s*[）)]")


def _strip_frontmatter(text: str) -> str:
    """去掉可能存在的 YAML frontmatter（保险起见，正文本身不应带，但修订回传可能夹带）"""
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            rest = text[end + 4:]
            return rest.lstrip("\n")
    return text


def scan_english_contamination(text: str) -> list[str]:
    """确定性扫描正文英文污染：返回去重后的 2+ 连续拉丁字母 token 列表（单字母如 X光 放行）。"""
    if not text:
        return []
    body = _strip_frontmatter(text)
    tokens = _ENGLISH_RUN_RE.findall(body)
    seen: set[str] = set()
    uniq: list[str] = []
    for t in tokens:
        if t not in seen:
            seen.add(t)
            uniq.append(t)
    return uniq


def hard_replace_english(text: str) -> tuple[str, list[str]]:
    """落盘前确定性兜底：把已知英文 token 替换为中文；任何残留拉丁串直接剔除。

    关键保证：返回的 out 正文一定零英文（未知 token 宁可删除也不留英文）。
    返回 (清理后文本, 仍残留的 token 列表[正常应为空])。

    设计要点：
    - 大小写不敏感（Bugs/BUG/Bug 都命中 bug 映射），避免『已知词却因大小写漏替换』。
    - 长词优先（ip_in_whitelist 先于 ip；VIP 先于 IP），防止子串误中。
    - 任何仍残留的拉丁串（未知词）一律剔除，作为最后保险，绝不让英文落盘。
    """
    # 1) 已知 token 大小写不敏感替换（长词优先，避免 bug 误中 debug 等）
    out = text
    for tok in sorted(_ENGLISH_REPLACE_MAP, key=len, reverse=True):
        repl = _ENGLISH_REPLACE_MAP[tok]
        out = re.sub(
            r"(?<![A-Za-z])" + re.escape(tok) + r"(?![A-Za-z])",
            repl, out, flags=re.IGNORECASE,
        )
    # 2) 任何仍残留的拉丁串（未知词）直接剔除（宁可丢词也不留英文）
    residual = scan_english_contamination(out)
    if residual:
        out = _ENGLISH_RUN_RE.sub("", out)
        # 只压缩同行内连续空格，绝不动换行——否则 \n\n 段落分隔会被压成单空格，
        # 导致整章正文变成一长段、丢失段落格式（历史 ch20 单段正文根因）。
        out = re.sub(r"[ \t]+", " ", out)
        # 行首尾空白清除（纯空白行保留为段落分隔的空行，不当作内容删除）
        out = re.sub(r"[ \t]+(?=\n)", "", out)
        out = re.sub(r"\n[ \t]+", "\n", out)
        # 压缩过密空行：连同残留的连续换行，恢复为单一空行分隔
        out = re.sub(r"\n{3,}", "\n\n", out)
        out = out.strip()
        out = _collapse_cjk_spaces(out)
        residual = scan_english_contamination(out)
    return out, residual


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


@workflow("m5_write_chapter")
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
        # ---- P0 新增参数：去AI味（默认开；--no-deslop 关闭）----
        deslop_enabled: bool = True,
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
        # P0：去AI味开关（质量门禁通过后、落盘前执行；--no-deslop 关闭）
        self.deslop_enabled = deslop_enabled

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
        # P-C 修复：角色生死/时间线硬约束（来自 characters/*.md 真源）
        character_constraints = self._build_character_constraints(subline_data)

        # Step 6: 伏笔任务
        foreshadow_task = self._load_foreshadow_task(progress)

        # Step 7: 题材层质量规则（MVP 内置修仙）
        # — 已在 prompt 中编码

        # 章节号
        chapter_num = progress.get("total_written", 0) + 1

        # 前情提要
        prev_summary = self._load_prev_summary(chapter_num)

        # 压力曲线阶段（B 方案：曲线缺失/退化时按体量估算的真实总章数推导）
        pressure_stage, tension_level = self._determine_pressure_stage(
            subline_data, chapter_num, default_hi=world_info.get("expected_chapters", 200)
        )

        # A：RAG 语义召回（仅当 .state/rag/ 已建立；否则空，绝不阻断写章）
        rag_dir = self.project_dir / ".state" / "rag"
        rag_context: list = []
        if rag_dir.exists():
            try:
                from agent.core.rag.retriever import Retriever

                retriever = Retriever(self.project_dir)
                subline_goal = self._extract_section(subline_data["content"], "支线目标")
                subline_name = subline_data["metadata"].get("subline_name", subline_id)
                # 主线 query（推进本章情节）
                main_query = (
                    f"第{chapter_num}章 {subline_name} {subline_goal} {pressure_stage}"
                )
                # 易漂移维度保底 query（保证影响前后一致性的关键片段被带进上下文）
                guard_queries = [
                    f"第{chapter_num}章 {subline_name} {subline_goal} {pressure_stage}",
                    "角色生死、是否死亡、是否复活、下落、身份",
                    "金手指规则、能力上限、境界、修炼体系",
                    "已揭开的真相、秘密、往事、身世",
                    "人物关系、恩怨、敌对、师徒、盟友当前状态",
                ]
                rag_context = retriever.retrieve_multi(
                    guard_queries, top_k_each=5, max_total=12
                ) or retriever.retrieve(main_query, top_k=5)
            except Exception:  # noqa: BLE001 - RAG 失败降级为空，不影响写章
                rag_context = []

        # C：追读力账本中的开放债务（缺账本则空，不阻断写章）
        open_debts: list = []
        reader_signals: list = []  # G12：读者反馈信号（kind=reader_feedback 分离，其余维持既有行为）
        try:
            from agent.core.story.pacing_store import PacingStore

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
            from agent.core.story.learning_store import LearningStore
            from agent.core.infra.prompt_helpers import format_learnings

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

        # ---- G15：连续性账本投影（写前注入；缺账本 → 降级为空，不阻断写章）----
        continuity_projection = ""
        continuity_loops: list = []
        try:
            from agent.core.continuity import ContinuityLedgerStore, project, project_to_text

            _ledger = ContinuityLedgerStore(self.project_dir)
            _ledger.load()
            if _ledger.has_any():
                _proj = project(_ledger)
                continuity_projection = project_to_text(_proj)
                continuity_loops = [
                    {
                        "loop_id": lo.loop_id,
                        "kind": lo.kind,
                        "status": lo.status,
                        "detail": lo.detail,
                    }
                    for lo in _proj.open_loops
                ]
        except Exception:  # noqa: BLE001 - 账本投影失败降级为空
            continuity_projection = ""
            continuity_loops = []

        # ---- G12：本章爽点剧本 + 情绪目标（缺失/损坏/关闭 → ""）----
        _payoff_task, _emotion_target = "", ""
        if getattr(self, "payoff_enabled", True):  # 默认开；--no-payoff 关闭
            try:
                from agent.core.story.payoff_script import chapter_payoff, load_payoff_script

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
            "character_constraints": character_constraints,  # P-C 修复：角色生死/时间线硬约束
            "relations_info": relations_info,
            "foreshadow_task": foreshadow_task,
            "prev_chapter_summary": prev_summary,
            "rag_context": rag_context,
            "open_debts": open_debts,
            "learnings": learnings,
            "learnings_text": learnings_text,
            # ---- G15：连续性账本投影（写前输入，有界；缺 → 空降级）----
            "continuity_projection": continuity_projection,
            "continuity_open_loops": continuity_loops,
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
            # ---- B1：写章防模板注入（本卷已用手段清单 + 灭门回忆计数；缺则降级为空）----
            "reuse_guard_text": self._build_reuse_guard(chapter_num),
        }

    def _build_reuse_guard(self, chapter_num: int) -> str:
        """生成写章时防模板注入文本；读失败→"" 降级不阻断（B1）。"""
        try:
            from agent.core.story.reuse_guard import build_reuse_guard

            return build_reuse_guard(self.project_dir, chapter_num)
        except Exception:  # noqa: BLE001 - 降级不阻断
            return ""

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

        # B 方案：由体量估算全书总章数（曲线缺失/退化时按真实跨度推导压力阶段）
        scope_key = metadata.get("scope", "medium")
        scope_total_words = metadata.get("scope_total_words")
        scope_cl = (
            metadata.get("scope_chapter_length")
            or (style.get("chapter_length") if isinstance(style, dict) else None)
        )
        expected_chapters = estimate_chapters(scope_key, scope_total_words, scope_cl)

        return {
            "title": metadata.get("title", ""),
            "scope": metadata.get("scope", ""),
            "genre": first_genre(metadata),
            "genre_label": first_genre_label(metadata),
            "genres": list(metadata.get("genres") or []),
            "tone": style.get("tone", ""),
            "pov": style.get("pov", ""),
            "rhythm": style.get("rhythm", ""),
            "chapter_length": style.get("chapter_length", 3000),
            "info_density": style.get("info_density", ""),
            "banned_elements": style.get("banned_elements", []),
            "synopsis": synopsis,
            "realm_system": realm_system,
            "golden_finger_info": golden_finger,
            "expected_chapters": expected_chapters,
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
        # 没匹配到范围 → P-B 修复：均匀分配节点使路线随章节推进，而非恒为 N01
        node_ids = [blocks[i] for i in range(1, len(blocks), 2)]
        if node_ids:
            # 用节点范围的最大 hi 作为全书跨度（无范围则退化为本章号）
            his: list[int] = []
            for i in range(1, len(blocks), 2):
                m = re.search(r"章节范围[：:]\s*(\d+)[-~](\d+)", blocks[i + 1] if i + 1 < len(blocks) else "")
                if m:
                    his.append(int(m.group(2)))
            total = max(his) if his else chapter_num
            idx = min(len(node_ids) - 1, int((chapter_num - 1) / max(1, total) * len(node_ids)))
            node_id = node_ids[idx]
            block = ""
            for i in range(1, len(blocks), 2):
                if blocks[i] == node_id:
                    block = blocks[i + 1] if i + 1 < len(blocks) else ""
                    break
            milestone = re.search(r"## N\d+ · (.+)", block)
            logger.warning(
                "[route] 第 %d 章未被任何路线节点范围覆盖（全书跨度 %d），"
                "按位置分配到节点 %s",
                chapter_num, total, node_id,
            )
            return {
                "node_id": node_id,
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

    def _extract_character_names(self, subline_data: dict[str, Any]) -> list[str]:
        """从 subline.md 解析本章出场角色名列表（供角色信息加载与状态硬约束复用）。"""
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

        # 如果没提取到角色名，加载所有角色
        if not names:
            chars_dir = self.project_dir / "characters"
            if chars_dir.exists():
                names = [p.stem for p in chars_dir.glob("*.md")]
        return names

    def _load_characters(
        self, subline_data: dict[str, Any]
    ) -> tuple[str, str]:
        """读取本章涉及角色的 character.md"""
        names = self._extract_character_names(subline_data)

        chars_dir = self.project_dir / "characters"
        info_parts: list[str] = []
        fingerprint_parts: list[str] = []

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

    def _build_character_constraints(self, subline_data: dict[str, Any]) -> str:
        """P-C 修复：把 characters/*.md 的生死/状态/时间线真源抽取为「不可违背」硬约束。

        直接喂给 writer 的系统提示，防止出现 ch049「周伯十年前便已故去」这类与前文
        角色档案矛盾的内容。纯规则提取，零网络；缺失档案则跳过该角色。
        """
        names = self._extract_character_names(subline_data)
        chars_dir = self.project_dir / "characters"
        if not names or not chars_dir.exists():
            return ""

        parts: list[str] = []
        for name in names[:6]:  # 与 _load_characters 取前 6 个保持一致
            char_data = self.sm.load_character(name)
            if not char_data["exists"]:
                fuzzy = False
                for p in chars_dir.glob("*.md"):
                    if name in p.stem or p.stem in name:
                        char_data = self.sm.load_character(p.stem)
                        fuzzy = True
                        break
                if not fuzzy:
                    continue
            content = char_data["content"]
            # 1) 优先取结构化状态段落
            status = (
                self._extract_section(content, "状态")
                or self._extract_section(content, "当前状态")
                or self._extract_section(content, "生死")
                or self._extract_section(content, "存活状态")
            )
            # 2) 关键词兜底（无结构化段落时）
            if not status:
                if re.search(r"已故|去世|死亡|牺牲|阵亡|陨落|辞世", content):
                    status = "（档案正文提及已故/牺牲，按已故处理）"
                elif re.search(r"在世|存活|健在", content):
                    status = "（档案正文提及在世/存活）"
            # 3) 关键时间线
            timeline = (
                self._extract_section(content, "时间线")
                or self._extract_section(content, "关键时间线")
                or self._extract_section(content, "生平")
            )
            bits: list[str] = []
            if status:
                bits.append(f"权威状态：{status.strip()[:120]}")
            if timeline:
                bits.append(f"关键时间线：{timeline.strip()[:160]}")
            if bits:
                parts.append(
                    f"- {name}：{'；'.join(bits)}。"
                    f"本章正文不可与上述状态/时间线矛盾（尤其角色生死、所处年代须一致）。"
                )
        return "\n".join(parts)

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
        """读取上一章的摘要（本章必须从这里无缝续写）。

        连续性问题根治：旧实现只取上一章「开头 300 字」做前情，writer 无法得知上一章
        「结尾」的真实状态，导致误把上一章开头场景重演、或自创与既定设定冲突的并行背景
        （历史 ch6 时间回环、ch7「青阳/青门/婉儿死而复生」漂移根因）。
        现改为：短章给全文；长章给出「开头 + 结尾」两段，【结尾】作为本章必须接续的
        权威状态，并附严禁重演上一章已发生场景/对话的硬约束。
        """
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
        body = text.strip()
        body = re.sub(r"\s*\n\s*", "\n", body)
        if len(body) <= 600:
            return (
                body
                + "\n\n（上一章全文较短。本章必须从上一章结尾处无缝续写，"
                "严禁重演上一章已出现的场景/对话。如果上一章已解决的冲突（如灭掉追兵、"
                "抓住刺客），本章不得让同一事件再次原样发生。）"
            )
        head = body[:160].rstrip()
        tail = body[-300:].rstrip()
        return (
            "【上一章开头（情景氛围）】"
            + head
            + "\n\n【上一章结尾 · 权威接续状态】本章必须从该状态之后无缝续写，不得回退、"
            "不得重演上一章已经发生的场景与对话：\n"
            + tail
            + "\n\n【续写硬约束】1) 严格从上述「上一章结尾」的真实状态继续推进，不得重演/倒退。"
            "2) 涉及无名身世、宗门、角色关系、金手指等既有设定，必须全线沿用前文与角色档案，"
            "严禁凭空发明并行背景（如改名换姓、换师门、已死角色无故复活）。"
        )

    def _determine_pressure_stage(
        self, subline_data: dict[str, Any], chapter_num: int, default_hi: int = 200
    ) -> tuple[str, str]:
        """从 subline.md 的压力曲线表确定当前阶段。

        B 方案：default_hi 为体量估算的预计总章数（默认回退 200），
        在「曲线缺失/退化/未命中」时按真实全书跨度推导压力阶段，
        避免百万字（如 500 章）项目被 200 字面量上限误判阶段。
        """
        content = subline_data.get("content", "")
        section = self._extract_section(content, "剧集压力曲线")
        if not section:
            logger.warning(
                "[pacing] subline「剧集压力曲线」缺失，第 %d 章按位置推导压力阶段",
                chapter_num,
            )
            return self._position_based_stage(chapter_num, 1, default_hi)

        bands: list[tuple[str, int, int, str]] = []
        for line in section.splitlines():
            if line.startswith("|") and "阶段" not in line and "---" not in line:
                parts = [p.strip() for p in line.split("|")]
                if len(parts) >= 4:
                    range_match = re.match(r"(\d+)[-~](\d+)", parts[2])
                    if range_match:
                        bands.append(
                            (parts[1], int(range_match.group(1)), int(range_match.group(2)), parts[3])
                        )
        if not bands:
            logger.warning(
                "[pacing] subline「剧集压力曲线」无可解析区间，第 %d 章按位置推导",
                chapter_num,
            )
            return self._position_based_stage(chapter_num, 1, default_hi)

        # 整体跨度
        min_lo = min(b[1] for b in bands)
        max_hi = max(b[2] for b in bands)
        # 命中区间优先
        for stage, lo, hi, tension in bands:
            if lo <= chapter_num <= hi:
                # 退化检测：铺垫段过长且存在后续阶段 → 视为平坦曲线，改按位置推导
                setup_bands = [b for b in bands if b[0] == "铺垫"]
                has_later = any(b[0] != "铺垫" for b in bands)
                if (
                    stage == "铺垫"
                    and has_later
                    and setup_bands
                    and (setup_bands[0][2] - setup_bands[0][1] + 1)
                    > 0.5 * max(1, max_hi - min_lo + 1)
                ):
                    logger.warning(
                        "[pacing] 检测到退化压力曲线（铺垫段覆盖 %d-%d，占全书 %d%%），"
                        "第 %d 章改按位置推导压力阶段",
                        setup_bands[0][1], setup_bands[0][2],
                        round(100 * (setup_bands[0][2] - setup_bands[0][1] + 1) / max(1, max_hi - min_lo + 1)),
                        chapter_num,
                    )
                    return self._position_based_stage(chapter_num, min_lo, max_hi)
                return stage, tension
        # 未命中任何区间 → 按位置推导
        logger.warning(
            "[pacing] 第 %d 章未被任何压力曲线区间覆盖（全书 %d-%d），按位置推导",
            chapter_num, min_lo, max_hi,
        )
        return self._position_based_stage(chapter_num, min_lo, max_hi)

    @staticmethod
    def _position_based_stage(chapter_num: int, lo: int, hi: int) -> tuple[str, str]:
        """按章节在 [lo, hi] 跨度中的位置推导爬升压力阶段（铺垫→冲突→高潮→舒缓）。"""
        if hi <= lo:
            return "铺垫", "低"
        frac = (chapter_num - lo) / (hi - lo)
        if frac < 0.15:
            return "铺垫", "低"
        if frac < 0.5:
            return "冲突", "中"
        if frac < 0.85:
            return "高潮", "高"
        return "舒缓", "低"

    # ============================================================
    # 1.5 章节正文后格式化（安全网，兜底 LLM 段落格式遗漏）
    # ============================================================
    @staticmethod
    def _format_chapter_body(text: str) -> str:
        """规范化章节正文的段落格式。

        1. 去除每行首尾空白字符
        2. 将连续 3+ 空行压缩为 1 个空行
        3. 确保最后没有多余空行
        4. 如果全文没有任何段落分隔，自动按句分段（兜底 LLM 完全不分段的情况）
        """
        import re as _re

        # 1) 按行处理，去除行首尾空白
        lines = text.split("\n")
        stripped = [line.strip() for line in lines]

        # 2) 压缩连续空行：连续空白行 → 一个空行
        result: list[str] = []
        blank_count = 0
        for line in stripped:
            if line == "":
                blank_count += 1
                if blank_count == 1:
                    result.append("")
            else:
                blank_count = 0
                result.append(line)

        # 3) 去除末尾多余空行
        while result and result[-1] == "":
            result.pop()

        body = "\n".join(result)

        # 4) 如果全文没有任何段落分隔（无空行），自动按句分段
        if "\n" not in body and len(body) > 200:
            body = _auto_split_paragraphs(body)

        return body

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

        resp = self.llm.chat_creative(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.85,
            max_tokens=4096,
            enable_thinking=False,
            validators=[ValidationSpec.not_empty()],
        )
        # 后处理：规范化段落格式（安全网，即使 LLM 遗漏规则 15 也兜底）
        raw = resp.text.strip()
        return self._format_chapter_body(raw)

    # ============================================================
    # 3. 质量校验 + 自动修订
    # ============================================================
    def _extra_english_revise(
        self, text: str, tokens: list[str], ctx: dict[str, Any], max_extra: int = 2
    ) -> str:
        """落盘前追加的英文专门修订：把残留英文 token 明确告诉 LLM，要求改纯中文。
        最多 max_extra 次，避免无限循环；仍残留则交给 hard_replace_english 兜底。"""
        for _ in range(max_extra):
            toks = scan_english_contamination(text)
            if not toks:
                break
            instr = (
                f"本章正文仍残留英文（必须全部改为纯中文叙事）：{', '.join(toks[:20])}。"
                + _ENGLISH_REPLACE_GUIDE
                + " 仅替换这些英文，保持情节/人物/对话/结构完全不变，直接输出完整正文。"
            )
            try:
                rev_resp = self.llm.chat_creative(
                    messages=[
                        {"role": "system", "content": pm.get("m5.revise").system},
                        {
                            "role": "user",
                            "content": pm.get("m5.revise").render_user(
                                quality_report=instr, chapter_text=text
                            ),
                        },
                    ],
                    temperature=0.4,
                    max_tokens=4096,
                    enable_thinking=False,
                    validators=[ValidationSpec.not_empty()],
                )
                text = rev_resp.text.strip()
            except Exception:  # noqa: BLE001 - 修订调用异常不阻断，交由兜底清理
                logger.warning("[no_english] 追加英文修订调用异常，交由确定性清理")
                break
        return text

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
            check_prompt = pm.get("m5.quality_check").render_user(
                tone=wi["tone"],
                chapter_length=wi["chapter_length"],
                characters_fingerprint=ctx["characters_fingerprint"],
                is_climax="是" if is_climax else "否",
                chapter_text=text,
            )

            # T-3：追加题材层质量规则（取自题材包 quality-rules.md），强化题材专属校验
            # 多题材：逐个题材包加载并拼接（world.md 元数据为 genres 列表，兼容旧 genre 单值）
            genre_list = wi.get("genres") or (
                [wi["genre"]] if wi.get("genre") else []
            )
            if genre_list:
                if self._genre_registry is None:
                    self._genre_registry = GenrePackRegistry()
                rules_parts: list[str] = []
                for g in genre_list:
                    try:
                        genre_rules_text = self._genre_registry.load(g).quality_rules
                    except ValueError:
                        genre_rules_text = ""
                    if genre_rules_text:
                        rules_parts.append(
                            f"【题材层质量规则（{g}）】\n{genre_rules_text}"
                        )
                if rules_parts:
                    check_prompt = check_prompt + "\n\n" + "\n\n".join(rules_parts)
            resp = self.llm.chat_utility(
                messages=[
                    {"role": "system", "content": pm.get("m5.quality_check").system},
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

            # ---- G-EN：正文纯中文硬关卡（确定性扫描，叠加在 LLM 质检之上，不依赖 LLM 自觉）----
            english_tokens = scan_english_contamination(text)
            quality_report_text = resp.text
            if english_tokens:
                report["overall_pass"] = False
                report.setdefault("rules", []).append(
                    {
                        "rule": "no_english",
                        "pass": False,
                        "issue": "正文含英文污染（必须改为纯中文）："
                        + "、".join(english_tokens[:20]),
                    }
                )
                report["suggestions"] = (
                    report.get("suggestions", "") + "\n" + _ENGLISH_REPLACE_GUIDE
                )
                # 把明确的中文替换指令直接塞进修订提示词，确保 LLM 知道改什么
                quality_report_text = (
                    resp.text
                    + "\n\n# 硬性修订指令（必须执行，否则本章不通过）\n"
                    + "本章检出英文污染 token："
                    + "、".join(english_tokens[:20])
                    + "\n"
                    + _ENGLISH_REPLACE_GUIDE
                )

            if report.get("overall_pass", False):
                break

            # 未通过 → 修订
            if attempt < MAX_REVISIONS:
                # ---- G9：章内子阶段事件（修订）----
                self._emit_substage("revise", ctx["chapter_num"])
                self.console.print(
                    f"  [yellow]质量校验未通过（第 {attempt + 1} 次修订）...[/yellow]"
                )
                revise_prompt = pm.get("m5.revise").render_user(
                    quality_report=quality_report_text,
                    chapter_text=text,
                )
                rev_resp = self.llm.chat_creative(
                    messages=[
                        {"role": "system", "content": pm.get("m5.revise").system},
                        {"role": "user", "content": revise_prompt},
                    ],
                    temperature=0.6,
                    max_tokens=4096,
                    enable_thinking=False,
                    validators=[ValidationSpec.not_empty()],
                )
                text = rev_resp.text.strip()
                attempts = attempt + 1

        # ---- G-EN：落盘前最终英文兜底（主循环修订后若仍有英文，追加专门修订 + 确定性清理）----
        residual = scan_english_contamination(text)
        if residual:
            text = self._extra_english_revise(text, residual, ctx, max_extra=6)
            residual = scan_english_contamination(text)
            if residual:
                text, still = hard_replace_english(text)
                if still:
                    logger.warning(
                        "[no_english] 落盘前仍存在英文残留，已做确定性清理: %s",
                        still[:20],
                    )
                else:
                    logger.info("[no_english] 落盘前确定性清理完成，无英文残留")
            # 英文已清干净 → 把 no_english 规则移出，避免影响整体通过判定
            if not scan_english_contamination(text):
                report.setdefault("rules", [])
                report["rules"] = [
                    r for r in report["rules"] if r.get("rule") != "no_english"
                ]
                other_fail = any(
                    (not r.get("pass", True)) for r in report.get("rules", [])
                )
                if not other_fail:
                    report["overall_pass"] = True

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
        """提取章节名：优先取 markdown 标题行『第X章 · 书名』中的书名，否则取默认。

        修正：模型常在正文首行自报标题（如『# 第二章 · 幼犬噬主』），
        旧逻辑只判断首行是否以「第」开头而退回『第N章』占位，导致成稿双标题
        （落盘模板『# 第 2 章 · 第2章』 + 正文残留『# 第二章 · 幼犬噬主』）。
        现改为从标题行中提取真正的书名（『·』后 / 去『第N章』前缀）。
        """
        for line in text.split("\n"):
            s = line.strip()
            if not s:
                continue
            if not s.startswith("#"):
                break  # 已到正文，未命中标题行 → 走默认
            head = re.sub(r"^#+\s*", "", s).strip()
            for sep in _TITLE_SPLIT_SEPS:
                if sep in head:
                    after = head.split(sep, 1)[1].strip()
                    if after:
                        return after[:30]
            # 无分隔符：去掉『第N章』前缀后再取（如『第5章 鬼市』→『鬼市』）
            after_no = _CHAPTER_ORDINAL_RE.sub("", head).strip()
            if after_no:
                return after_no[:30]
            if not re.match(r"^第", head):
                return head[:30]
            # 命中『第X章』但无书名 → 继续，最终走默认
        return f"第{ctx['chapter_num']}章"

    @staticmethod
    def _clean_chapter_body(text: str) -> str:
        """去掉 LLM 误输出的元信息，只保留可读正文。

        处理：
        1. 开头连续的 markdown 标题行（模型自报标题，落盘统一补标题，避免双标题）。
        2. 任意位置的『原文标题：…』『原标题：…』等元信息行。
        3. 整行被（）包裹且含执导/批注词的编辑注记（如『（此处为快节奏场景……）』）。
        """
        lines = text.split("\n")
        # 1) 去掉开头空行与 markdown 标题行（模型自报标题，落盘统一补标题，避免双标题）
        while lines and (not lines[0].strip() or _HEADING_RE.match(lines[0])):
            lines.pop(0)
        # 2) 过滤元信息行（保留空行结构，便于后续段落压缩）
        out: list[str] = []
        for ln in lines:
            s = ln.strip()
            if not s:
                out.append(ln)
                continue
            if _TITLE_META_RE.match(s):
                continue
            if (
                _EDITOR_NOTE_WHOLE_LINE_RE.match(s)
                and _EDITOR_HINT_WORDS_RE.search(s)
            ):
                continue
            out.append(ln)
        # 3) 去掉末尾多余空行
        while out and not out[-1].strip():
            out.pop()
        # 4) 去掉行尾/任意位置的章节收尾标注『（本章完）』等（LLM 常贴在末句后）
        out = [_END_MARK_RE.sub("", ln).rstrip() for ln in out]
        return "\n".join(out)

    @staticmethod
    def _dedup_repeated_chapter(text: str) -> str:
        """去掉 LLM 把整章正文重复输出两遍的情况（正文中途再次出现『# 第N章·…』标题）。

        复现：LLM 自报标题 + 完整正文后，又重复了一遍『# 第一章 · …』+ 正文，
        导致落盘出现重复内容、字数虚增。本方法在 ``_clean_chapter_body`` 之后、
        字数统计之前调用：若正文中段再次出现章节标题行（非开头），则在中段标题处截断，
        只保留第一遍正文（去掉后续重复内容）。
        """
        import re as _re

        # 章节标题形如：# 第 1 章 · 标题 / # 第一章 · 标题 / # 第1章· 标题。
        # 关键：LLM 重复正文时该标题常被直接拼在一段末尾（无换行），因此**不**锚定行首，
        # 用 `#` 前置即可（小说正文里 # 与『第N章』连写几乎不会合法出现）。
        title_re = _re.compile(r"#\s*第\s*[0-9一二三四五六七八九十百千]+\s*章\s*[·:：,，、\-\s]")
        matches = list(title_re.finditer(text))
        if not matches:
            return text
        # 正文开头正常应无章节标题（落盘统一由 _save_chapter 生成 `# 第 N 章 · 标题`），
        # 所以出现的首个标题视为重复起点，截断到它之前即可。
        first = matches[0]
        # 保险：若标题恰在正文开头（极端情形 _clean_chapter_body 未剔除干净），跳过去重
        if first.start() <= 1:
            return text
        head = text[: first.start()].rstrip()
        return head

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
        # ---- G-EN：落盘前绝对零英文关卡（单一写盘点，任何写章路径都过此门）----
        # 同时做元信息清理兜底（剔除模型误输出的标题/原文标题/编辑批注），
        # 保证不同写章入口（M5 / agentic_write）成稿都干净、字数统计准确。
        text = self._clean_chapter_body(text)
        # P-DEDUP：剔除 LLM 把整章正文重复输出两遍的情况（正文中途再次出现章节标题）。
        # 必须在字数统计之前，保证字数基于去重后的最终文本。
        text = self._dedup_repeated_chapter(text)
        # P-FMT：统一写盘点强制段落格式化（M5 / agentic_write 共用本方法）。
        # 兜底 LLM 完全不输出段落分隔的情况（正文被压成单段），按句自动分段。
        # 注意：这段必须在 word_count 计算之前，保证字数统计基于最终落盘文本。
        text = self._format_chapter_body(text)
        # 不论上游 _quality_check_and_revise 的 G-EN 块是否生效，这里都再做一次确定性兜底，
        # 保证写到磁盘的正文一定零英文（已知词翻译、未知串剔除）。
        clean_text, _still = hard_replace_english(text)
        if clean_text != text:
            logger.warning(
                "[no_english] _save_chapter 落盘前确定性清理英文残留(上游兜底未生效)"
            )
        text = clean_text
        word_count = len(text.replace("\n", "").replace(" ", ""))
        metadata["word_count"] = word_count
        body = f"# 第 {ctx['chapter_num']} 章 · {title}\n\n{text}"
        post = frontmatter.Post(body, **metadata)
        file.write_text(frontmatter.dumps(post), encoding="utf-8")
        return file

    def _archive_chapter(self, ctx: dict[str, Any], chapter_title: str) -> None:
        """G15 章后归档 hook：本章最小交接归档进连续性账本 + 伏笔 beats 标记落地。

        - 向 `ContinuityLedgerStore.commit` 写入本章交接（source_commit_id=本章 ID），
          `latest_handoff()` 即成为下一章投影的「上一章交接」来源。
        - 把规划锚指向本章（``anchor_chapter == 本章``）的伏笔 beat 标记为 committed，
          由纯函数 `derive_status` 自动推进线程状态。
        - 缺账本 / 任何异常 → 静默降级，绝不阻断写章（与「降级不阻断」一致）。
        """
        try:
            from agent.core.continuity import ContinuityHandoff, ContinuityLedgerStore
            from agent.core.story.foresight import ForesightBeat, ForesightStore, mark_committed

            chapter_num = ctx["chapter_num"]
            commit_id = f"ch{chapter_num:03d}"

            ledger = ContinuityLedgerStore(self.project_dir)
            ledger.load()
            ledger.commit(
                chapter=chapter_num,
                facts=[],
                knowledge=[],
                open_loops=[],
                handoff=ContinuityHandoff(
                    chapter=chapter_num,
                    summary=f"第{chapter_num}章《{chapter_title}》",
                    must_carry=[],
                    next_chapter_constraints=[],
                    source_commit_id=commit_id,
                ),
            )

            store = ForesightStore(self.project_dir)
            threads = store.load()
            changed = False
            for t in threads:
                for b in t.beats:
                    if b.anchor_chapter == chapter_num and b.exec_status != "committed":
                        mark_committed(t, ForesightBeat.model_validate(b), commit_id)
                        changed = True
            if changed:
                store.save(threads)
        except Exception:  # noqa: BLE001 - 归档失败降级不阻断
            logger.debug("[continuity] 章后归档失败，已降级（不影响本章产出）", exc_info=True)

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

        # 多题材：world_info.genres 为列表；兼容旧 world_info.genre 单值与元数据兜底
        wi = ctx["world_info"]
        genres: list[str] = list(wi.get("genres") or [])
        if not genres and wi.get("genre"):
            genres = [wi["genre"]]
        if not genres:
            md = self.sm.load_world()["metadata"]
            genres = list(md.get("genres") or [])
            if not genres and md.get("genre"):
                genres = [md["genre"]]

        parts: list[str] = []
        for name in trope_names:
            found = False
            for g in genres:
                try:
                    trope = self._genre_registry.load_trope(g, name)
                    parts.append(f"### {trope.name}\n{trope.text}")
                    found = True
                    break
                except ValueError:
                    continue
            if not found:
                self.console.print(
                    f"[yellow]⚠ 注入套路失败（{name}）：未在题材 {genres or ['(未声明)']} 中找到[/yellow]"
                )
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
            f"题材：{' / '.join(wi.get('genres') or ([wi['genre']] if wi.get('genre') else ['未声明']))}"
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


# ============================================================
# 工具函数：自动按句分段（兜底 LLM 完全不分段的情况）
# ============================================================
def _auto_split_paragraphs(text: str) -> str:
    """自动将无段落分隔的长文本按句分段，每 2-4 句组成一个段落。

    仅作为安全网，当 LLM 完全遗漏段落分隔时使用。
    """
    import re as _re

    # 按中文句子结束符分割（保留分隔符）
    parts = _re.split(r"(?<=[。！？」])", text)
    sentences = [s.strip() for s in parts if s.strip()]

    if len(sentences) <= 1:
        return text

    # 分组为段落（每段 2-4 句，优先 3 句）
    paragraphs: list[str] = []
    current: list[str] = []
    for s in sentences:
        current.append(s)
        if len(current) >= 3:
            paragraphs.append("".join(current))
            current = []
    if current:
        paragraphs.append("".join(current))

    return "\n\n".join(paragraphs)
