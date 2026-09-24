"""Guardrails 护栏（Phase 4 · 强化）

在章节输出进入 Memory / Evaluator 之前做**内容安全 + 形式合规**校验，防止明显失控输出
流入成书。覆盖四类检查（可配置、可注入）：

1. **空输出**：章节正文为空或纯空白 → error。
2. **禁用词 / 内容策略**：可配置词表（平台合规 / 敏感词）；命中 → error。
3. **长度边界**：单章字符数超 ``max_chars`` 或低于 ``min_chars`` → error / warn。
4. **占位残留**：草稿占位符（``[TODO]`` / ``XXX`` / ``未完待续（占位）`` 等）→ error
   （防止把未完成标记写进成书）。
5. **必需 schema**：结构化输出（dict）的必填字段缺失 → error（供结构化产出校验）。

设计：纯规则，零依赖、零网络；``check`` 返回结构化结果，``enforce`` 不通过抛异常；
默认配置保守安全、**不阻断正常创作**（正常网文不会命中占位符与空输出）。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

# 契约字段名的唯一真源（同层 core ⇒ 不违反 R6「core 不得反向依赖上层」）
from agent.core.story.chapter_contract import (
    CONTRACT_FIELDS,
    CONTRACT_LEAK_LABELS,
    find_contract_annotations,
)

# 默认占位符（草稿残留，绝不应出现在成书中）
_DEFAULT_PLACEHOLDERS: list[str] = [
    r"\[TODO\]", r"\[待补\]", r"\[占位\]", r"XXX", r"xxxx",
    r"未完待续（占位）", r"此处待写", r"placeholder",
]
# 默认禁用词（平台合规基线；实际项目应以配置文件覆盖为完整词表）
_DEFAULT_BANNED: list[str] = []

# 默认「内容护栏词表」——聚焦**结构完整性 + 作者残留标记**，非主题审查。
# 仅收录在正常成书中绝不应当出现的、无歧义的创作残留 / 序列化泄漏标记；
# 真实平台的合规词表应由部署方通过 ``.state/guardrails.json`` 自行配置。
# 注意：刻意不含 "null"/"undefined" 等常见英文词，避免误伤正常小说正文。
_DEFAULT_COMPLIANCE_WORDS: list[str] = [
    "{{", "}}",        # 未渲染的模板标签
    "[object Object]", # JSON 序列化泄漏
    "[REDACTED]",      # 脱敏占位残留
    "作者注", "作者按",  # 作者备注残留（未清理）
]

# ---- G6：AI 味规则（主理人拍板 #4：确定性词/句式表，默认 warn 标红不阻断）----
AI_FLAVOR_RULE_ID: str = "ai_flavor"
# 默认词表：只收「高置信 AI 腔」组合式（短语级），不收单字高频词（仿佛/缓缓/不禁 等），
# 防误杀古风/严肃文风。实际词表由部署方通过 .state/guardrails.json 的 ai_flavor_words 覆盖。
_DEFAULT_AI_FLAVOR_WORDS: list[str] = [
    "不禁微微一笑", "嘴角勾起一抹弧度", "嘴角微微上扬", "眼底闪过一丝",
    "眼中闪过一抹", "喃喃自语", "轻声呢喃", "心中一动", "微微一怔",
    "不由一愣", "勾唇一笑", "眸色一沉", "唇角微勾", "若有所思",
    "缓缓开口", "语气平静",
]

# ---- G14：三类成书污染护栏（拍板：英文残留 / 占位标题 / 跨章重复）----
# 1) 英文/杂质残留：正文混入工具返回（如 "Visualization failed. Cost 1 year of lifespan."）、
#    系统提示片段、序列化泄漏等。小说正文不应含连续英文单词。
JUNK_RULE_ID: str = "non_chinese_junk"
# 连续 ≥3 字母的英文单词（标题/人名等由 junk_whitelist 豁免）
_RE_ENGLISH_WORD = re.compile(r"[A-Za-z]{3,}")
# 工具/系统残留特征串（强信号，命中即 error，不依赖长度阈值）
_JUNK_SIGNATURES: list[str] = [
    "Visualization failed", "Cost", "lifespan", "failed. Cost",
    "[system]", "system prompt", "undefined", "null",
]
# 2) 标题合规：首个 # 第N章·... 标题为空 / 匹配 第N章·第N章 / 长度<阈值 / 与全书标题重复
TITLE_RULE_ID: str = "title_placeholder"
_TITLE_RE = re.compile(r"^#\s*第\s*(\d+)\s*章\s*·\s*(.*?)\s*$", re.MULTILINE)
_TITLE_MIN_LEN = 4          # 标题正文（·之后）最少字数

#: ★ G3（2026-09-21）：细纲契约的**字段名**出现在章节标题 = 写作元指令泄漏的
#: 标题形态（实测 ch004「档位=垫片」恰 4 字符躲过 _TITLE_MIN_LEN）。
#: 词表**由契约真源派生**（``chapter_contract.CONTRACT_FIELDS``，纪律 #19——
#: 不再手写第二份字面量），且只匹配「字段名 + 赋值号」形态：
#: 正文「他站在场边」「验收了药材」不含 ``字段名=`` ⇒ 不误杀（与既有红线
#: ``test_common_short_words_not_in_wordlist`` 的口径一致）。
_TITLE_CONTRACT_LABEL_RE = re.compile(
    "(?:" + "|".join(re.escape(f) for f in CONTRACT_FIELDS) + ")\\s*[=＝]"
)
_TITLE_MAX_REPEAT = 2       # 标题与已发布标题重复即判违规
# 3) 跨章段落去重：全书指纹库比对（去空白+标点归一化 hash，≥40字长段落，相似度>0.85）
DUP_RULE_ID: str = "paragraph_dup"
_DUP_MIN_CHARS = 40         # 仅对 ≥40 字长段落比对，避免短句误杀
_DUP_SIMILARITY = 0.85      # 相似度阈值
# 默认配置路径
DEFAULT_GUARDRAIL_CONFIG_PATH = ".state/guardrails.json"
# 全书指纹库默认路径（决策③：存 .state/ 下）
DEFAULT_FINGERPRINT_PATH = ".state/chapter_fingerprints.json"

# ---- G14（补充）：第四类成书污染——写作元指令泄漏 ----
# agent 下发给 LLM 的「章末悬念 / 章节钩子 / 内部章节号」等写作元指令被模型原样写进正文
# （如 ch026 结尾的 `【章末悬念】…`、ch002 的 `章末悬念：…`），属 prompt 污染，写时拦截。
META_LEAK_RULE_ID: str = "meta_instruction_leak"
# 命中即判 error 的强信号标记（仅在正文中检测，已剥离 frontmatter）：
# - 章末悬念 / 留下悬念：agent→LLM 的钩子指令被写进正文
# - 悬念[：:] / 本章要求[：:] / 写作指令 / 作者指令 / 系统指令 / 写作提示：写作元指令残留
# - ch\d+章 / 第\d+章里 / 第\d+段[：:]：内部章节号/段号泄漏到叙事
# - 修订说明 / 修订笔记 / 改稿说明：LLM 自我修订尾注混入交付正文
#   （2026-09-08 五灵破归档 ch4 实证：整块修订笔记附于章末漏网）
# - 2026-09-13 无灵 ch166/176/206/260 实证回填：LLM 扩写/质检自查报告混入章末，
#   表现为编号清单（"1. 开场钩子…5. 英文污染…"）或字数汇报
#   （"正文从约1619字扩展至约2450字，符合目标字数要求"）。
#   模式库按事故样本持续回填，新增模式须带实测样本回归（tests/test_scope_and_meta_boundary.py）。
_CONTRACT_LABEL_ALT = "|".join(re.escape(_x) for _x in CONTRACT_LEAK_LABELS)

_META_LEAK_RE = re.compile(
    # 契约字段名（**由契约唯一真源派生**，2026-09-18）：prompt v4 把字段名定为
    # 「章首钩子/章尾钩子/爽点/目标情绪/在场/禁/验收」后，本词表曾未同步 ⇒
    # 写手照抄字段名被误判为「元指令泄漏」（ch003.md:254 实测）。
    # 判据词表与提示词语言锚必须同源，否则改名就是一次双向破裂。
    _CONTRACT_LABEL_ALT
    + r"|留下悬念|悬念[：:]|本章要求[：:]|写作指令|作者指令|系统指令|写作提示"
    r"|修订说明|修订笔记|改稿说明"
    r"|ch\d+章|第\d+章里|第\d+段[：:]"
    # 2026-09-13 无灵实测泄漏模式（质检自查报告 / 扩写汇报）
    r"|开场总评|开场钩子|场景占比|英文污染"
    r"|(?:正文|本文|原文|本章)(?:原?文字数)?从?约?\d+字(?:现)?扩[写展]至约?\d+字"
    r"|扩[写展]至约?\d+字|达到目标字数|符合目标字数|目标字数要求"
    r"|本章原文字数|扩写了.{0,20}场景"
)

# ---- G14 结构层（2026-09-13 类级升级）：章末结构化清单块 ----
# 词表只覆盖已知措辞；编号/要点清单是元信息的结构指纹，与措辞无关。
# 阈值偏保守（≥3 行）防误杀：叙事正文偶见的"其一…其二…"不用行首编号格式。
_META_LEAK_TAIL_LINES = 12
_META_LEAK_LIST_MIN_LINES = 3
_NUMBERED_LIST_RE = re.compile(r"^\d{1,2}[.、）)]\s*\S")
_BULLET_LIST_RE = re.compile(r"^(?:[-*•]|-\s*\*\*)\s*\S")

# ---- P2（竞品优化方案 2.2）：第五类确定性检查——叙事越界（narrative_tell，对标 inkos
# post-write-validator）。三类高置信 AI 腔/旁白模式，warn 级标红不阻断（防误杀，全禁
# 是 inkos 的反面教训）；实际模式可经 .state/guardrails.json 覆盖。
NARRATIVE_TELL_RULE_ID: str = "narrative_tell"
DENSITY_RULE_ID: str = "paragraph_density"
HARD_POLLUTION_RULE_ID: str = "hard_pollution"  # L2：标题重复/AI指令泄漏/占位符/AI承接词（2026-09-14）
# P2-2.2：叙事越界模式（元叙事旁白/分析报告腔/集体反应）——warn 级标红不阻断。
# 补缺（2026-09-14）：_NARRATIVE_TELL_PATTERNS 此前有引用无定义，check_narrative_tell=True
# 时直接 NameError——写章路径因显式关闭未暴露，rewrite/其他路径会崩。保守词表，仅标红。
_NARRATIVE_TELL_PATTERNS: list[tuple[str, "re.Pattern[str]"]] = [
    ("元叙事旁白", re.compile(r"（此处|（注：|本段|正如前文|后文将|这里埋下|此处埋下")),
    ("分析报告腔", re.compile(r"综上|由此可见|换言之|值得注意的是")),
    ("集体反应", re.compile(r"众人皆是一震|所有人都屏住了呼吸|在场的所有人都")),
]
_DEFAULT_MAX_PARAGRAPH_CHARS = 300   # 单自然段（\n 分隔）超过即 warn
_DEFAULT_MAX_SENTENCE_CHARS = 120    # 单句（。！？…分隔）超过即 warn


class GateMode(str, Enum):
    """护栏门禁模式。

    - ADVISORY（建议）：仅报告违规，不阻断（默认，保持创作流畅）。
    - BLOCK（硬门禁）：命中 error 级违规则**拒绝发布**，要求修订后重提。
    """

    ADVISORY = "advisory"
    BLOCK = "block"


@dataclass
class GuardrailViolation:
    """单条违规。"""

    rule_id: str
    severity: str            # error | warn
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "severity": self.severity,
            "message": self.message,
        }


@dataclass
class GuardrailResult:
    """校验结果。"""

    violations: list[GuardrailViolation] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        """无 error 级违规即通过（warn 不阻断）。"""
        return not any(v.severity == "error" for v in self.violations)

    @property
    def errors(self) -> list[GuardrailViolation]:
        return [v for v in self.violations if v.severity == "error"]

    @property
    def warnings(self) -> list[GuardrailViolation]:
        return [v for v in self.violations if v.severity == "warn"]

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "violations": [v.to_dict() for v in self.violations],
        }


class GuardrailViolationError(Exception):
    """``enforce`` 失败抛出的异常，携带结构化结果。"""

    def __init__(self, result: GuardrailResult) -> None:
        self.result = result
        msgs = "; ".join(v.message for v in result.errors)
        super().__init__(f"Guardrail 未通过：{msgs}")


class Guardrails:
    """内容 / 形式护栏。

    Args:
        banned_words: 禁用词表（默认 ``_DEFAULT_BANNED``）。
        placeholder_patterns: 占位残留正则（默认 ``_DEFAULT_PLACEHOLDERS``）。
        max_chars / min_chars: 单章字符边界（None 表示不限制）。
        allow_warnings: warn 级是否算通过（默认 True：仅 error 阻断）。
    """

    def __init__(
        self,
        banned_words: list[str] | None = None,
        placeholder_patterns: list[str] | None = None,
        max_chars: int | None = None,
        min_chars: int | None = None,
        allow_warnings: bool = True,
        ai_flavor_words: list[str] | None = None,   # G6：AI 味词表（默认 _DEFAULT_AI_FLAVOR_WORDS）
        ai_flavor_severity: str = "warn",           # G6：命中 severity（warn 标红 / error 阻断）
        # ---- G14：三类污染护栏配置 ----
        junk_whitelist: list[str] | None = None,     # 英文豁免词（人名/专有名词；默认空）
        check_junk: bool = True,                     # 英文/杂质残留检测开关
        check_title: bool = True,                    # 标题合规检测开关
        check_dup: bool = True,                      # 跨章段落去重开关
        check_meta_leak: bool = True,                # 写作元指令泄漏检测开关（G14 补充）
        published_titles: list[str] | None = None,   # 全书已发布标题（用于标题重复判定）
        fingerprint_db: dict[str, list[str]] | None = None,  # 全书指纹库 {章号: [段落hash]}
        check_narrative_tell: bool = True,           # P2-2.2：元叙事旁白/报告腔/集体反应开关
        check_density: bool = True,                  # P2-2.1：段级密度开关
        max_paragraph_chars: int = _DEFAULT_MAX_PARAGRAPH_CHARS,
        max_sentence_chars: int = _DEFAULT_MAX_SENTENCE_CHARS,
        # ---- L2（2026-09-14）：生成残留硬污染开关（标题重复/AI指令泄漏/占位符/AI承接词）----
        # 定义下沉 core（agent.core.story.text_hygiene），本护栏统一消费——
        # 任何走 guardrails.check() 的写/改路径（rewrite/写章护栏等）自动获得拦截。
        check_hard_pollution: bool = True,
    ) -> None:
        self.banned_words = list(banned_words if banned_words is not None else _DEFAULT_BANNED)
        self.placeholder_patterns = [
            re.compile(p, re.IGNORECASE)
            for p in (placeholder_patterns if placeholder_patterns is not None else _DEFAULT_PLACEHOLDERS)
        ]
        self.max_chars = max_chars
        self.min_chars = min_chars
        self.allow_warnings = allow_warnings
        # G6：AI 味词表与 severity（拍板 #4：默认 warn 标红不阻断）
        self.ai_flavor_words = list(ai_flavor_words if ai_flavor_words is not None else _DEFAULT_AI_FLAVOR_WORDS)
        self.ai_flavor_severity = ai_flavor_severity if ai_flavor_severity in ("warn", "error") else "warn"
        # ---- G14 ----
        self.junk_whitelist = set(w.strip() for w in (junk_whitelist or []))
        self.check_junk = check_junk
        self.check_title = check_title
        self.check_dup = check_dup
        self.check_meta_leak = check_meta_leak
        self.published_titles: list[str] = list(published_titles or [])
        # 指纹库：章号(str) -> 段落归一化 hash 列表；落盘后由调用方增量更新
        self.fingerprint_db: dict[str, list[str]] = dict(fingerprint_db or {})
        # ---- P2：narrative_tell / 段级密度 ----
        self.check_narrative_tell = check_narrative_tell
        self.check_density = check_density
        self.max_paragraph_chars = max(int(max_paragraph_chars), 1)
        self.max_sentence_chars = max(int(max_sentence_chars), 1)
        # ---- L2：生成残留硬污染（2026-09-14，core 定义统一消费）----
        self.check_hard_pollution = check_hard_pollution

    # ---------------------------------------------------------------- 文本校验
    def check_text(
        self,
        text: str,
        *,
        max_chars: int | None = None,
        min_chars: int | None = None,
    ) -> GuardrailResult:
        violations: list[GuardrailViolation] = []
        max_chars = self.max_chars if max_chars is None else max_chars
        min_chars = self.min_chars if min_chars is None else min_chars

        # 1) 空输出
        if text is None or not str(text).strip():
            violations.append(GuardrailViolation("empty", "error", "章节正文为空或纯空白"))
            return GuardrailResult(violations)

        t = str(text)

        # 2) 禁用词
        for w in self.banned_words:
            if w and w in t:
                violations.append(
                    GuardrailViolation("banned_word", "error", f"命中禁用词：{w}")
                )

        # 3) 长度边界
        n = len(t)
        if max_chars is not None and n > max_chars:
            violations.append(
                GuardrailViolation(
                    "too_long", "error",
                    f"章节超长：{n} > 上限 {max_chars}",
                )
            )
        if min_chars is not None and n < min_chars:
            violations.append(
                GuardrailViolation(
                    "too_short", "warn",
                    f"章节偏短：{n} < 下限 {min_chars}",
                )
            )

        # 4) 占位残留
        for pat in self.placeholder_patterns:
            m = pat.search(t)
            if m:
                violations.append(
                    GuardrailViolation(
                        "placeholder", "error",
                        f"检测到草稿占位残留：{m.group(0)}",
                    )
                )

        # 5) AI 味（G6）：命中组合式 AI 腔词句 → 默认 warn（advisory 标红不阻断）；
        #    同一词多次命中合并为一条（附次数），防报告刷屏。
        if self.ai_flavor_words:
            hits: dict[str, int] = {}
            for w in self.ai_flavor_words:
                if w and w in t:
                    hits[w] = t.count(w)
            for w, cnt in sorted(hits.items(), key=lambda kv: -kv[1]):
                violations.append(GuardrailViolation(
                    AI_FLAVOR_RULE_ID, self.ai_flavor_severity,
                    f"命中 AI 腔词句「{w}」（{cnt} 次）",
                ))

        # ---- G14：三类成书污染护栏 ----
        # 6) 英文/杂质残留（non_chinese_junk）：正文混入工具返回/系统提示/序列化泄漏。
        if self.check_junk:
            violation_msg = self._check_junk(t)
            if violation_msg:
                violations.append(GuardrailViolation(
                    JUNK_RULE_ID, "error", violation_msg,
                ))

        # 7) 标题合规（title_placeholder）：首个 # 第N章·... 标题为空 / 占位 / 重复。
        if self.check_title:
            violation_msg = self._check_title(t)
            if violation_msg:
                violations.append(GuardrailViolation(
                    TITLE_RULE_ID, "error", violation_msg,
                ))

        # 8) 跨章段落去重（paragraph_dup）：与全书指纹库比对，相似度 > 阈值判违规。
        if self.check_dup:
            dup_hits = self._check_dup(t)
            for msg in dup_hits:
                violations.append(GuardrailViolation(
                    DUP_RULE_ID, "error", msg,
                ))

        # 9) 写作元指令泄漏（meta_instruction_leak）：agent→LLM 的钩子/规划指令被写进正文。
        if self.check_meta_leak:
            leak = self._check_meta_leak(t)
            if leak:
                violations.append(GuardrailViolation(
                    META_LEAK_RULE_ID, "error", leak,
                ))

        # ---- L2（2026-09-14）：生成残留硬污染（标题重复/AI指令泄漏/占位符/AI承接词）----
        # 与写章门禁/落盘兜底同源（core/story/text_hygiene 单一定义）——任何走
        # guardrails.check() 的写/改路径自动获得该硬关卡（灵荒薪传 ch001 复盘）。
        if self.check_hard_pollution:
            try:
                from agent.core.story.text_hygiene import scan_hard_pollutions

                _hp = scan_hard_pollutions(t)
                for _msg in _hp:
                    violations.append(GuardrailViolation(
                        HARD_POLLUTION_RULE_ID, "error", _msg,
                    ))
            except Exception:  # noqa: BLE001 - L2 扫描失败不阻断（G3 降级哲学）
                pass  # noqa: SILENT_DEGRADE

        # 10) 叙事越界（narrative_tell，P2-2.2）：元叙事旁白/分析报告腔/集体反应。
        #     warn 级标红不阻断（对齐 ai_flavor 的"确定性词表默认 warn"拍板）。
        if self.check_narrative_tell:
            for label, pat in _NARRATIVE_TELL_PATTERNS:
                m = pat.search(t)
                if m:
                    violations.append(GuardrailViolation(
                        NARRATIVE_TELL_RULE_ID, "warn",
                        f"疑似{label}：「{m.group(0)}」（叙述越出故事世界，建议改为具体动作/反应）",
                    ))

        # 11) 段级密度（paragraph_density，P2-2.1）：超长自然段 / 超长单句，warn 标红。
        if self.check_density:
            violations.extend(self._check_density(t))

        return GuardrailResult(violations)

    def _check_density(self, text: str) -> list[GuardrailViolation]:
        """段级密度确定性检查：超长段落 / 超长单句（对话段豁免）。"""
        out: list[GuardrailViolation] = []
        for para in text.split("\n"):
            para = para.strip()
            if not para or para.startswith(("「", '"', "“")):
                continue  # 空行/对话段豁免（对白连排属正常排版）
            if len(para) > self.max_paragraph_chars:
                out.append(GuardrailViolation(
                    DENSITY_RULE_ID, "warn",
                    f"自然段过长：{len(para)} 字 > {self.max_paragraph_chars}（按句号/动作转折拆段）",
                ))
            for sent in re.split(r"[。！？…]+", para):
                sent = sent.strip()
                if len(sent) > self.max_sentence_chars:
                    out.append(GuardrailViolation(
                        DENSITY_RULE_ID, "warn",
                        f"单句过长：{len(sent)} 字 > {self.max_sentence_chars}（拆短句）",
                    ))
                    break  # 每段只报一次，防刷屏
        return out

    # ---------------------------------------------------------------- 结构化校验
    def check_schema(
        self, obj: Any, required_fields: list[str]
    ) -> GuardrailResult:
        violations: list[GuardrailViolation] = []
        if not isinstance(obj, dict):
            violations.append(
                GuardrailViolation("schema_type", "error", "结构化产出不是 dict")
            )
            return GuardrailResult(violations)
        for f in required_fields:
            if f not in obj or obj[f] in (None, "", []):
                violations.append(
                    GuardrailViolation(
                        "missing_field", "error", f"缺少必需字段：{f}"
                    )
                )
        return GuardrailResult(violations)

    # ---------------------------------------------------------------- 便捷入口
    def check(
        self,
        text: str,
        *,
        required_fields: list[str] | None = None,
        max_chars: int | None = None,
        min_chars: int | None = None,
    ) -> GuardrailResult:
        """先校验文本，再（可选）校验其解析后的结构化字段。

        若 ``required_fields`` 给定且 ``text`` 可被解析为 JSON，则同时做 schema 校验。
        """
        result = self.check_text(text, max_chars=max_chars, min_chars=min_chars)
        if required_fields:
            try:
                parsed = json.loads(text)
            except Exception:  # noqa: BLE001
                parsed = None  # noqa: SILENT_DEGRADE
            if parsed is not None:
                schema_res = self.check_schema(parsed, required_fields)
                result.violations.extend(schema_res.violations)
        return result

    def enforce(
        self,
        text: str,
        *,
        required_fields: list[str] | None = None,
        max_chars: int | None = None,
        min_chars: int | None = None,
    ) -> GuardrailResult:
        """不通过则抛 ``GuardrailViolationError``（含结构化结果）。"""
        result = self.check(
            text, required_fields=required_fields,
            max_chars=max_chars, min_chars=min_chars,
        )
        if not result.passed:
            raise GuardrailViolationError(result)
        return result

    # ------------------------------------------------------------------
    # 硬门禁：配置化门禁模式（advisory / block）
    # ------------------------------------------------------------------
    def gate(
        self,
        text: str,
        *,
        mode: GateMode | str = GateMode.ADVISORY,
        required_fields: list[str] | None = None,
        max_chars: int | None = None,
        min_chars: int | None = None,
        auto_clean_placeholders: bool = True,
    ) -> "GateReport":
        """门禁入口。

        - ADVISORY：仅报告，``passed`` 反映是否存在 error 级违规。
        - BLOCK：命中 error 级违规（空 / 禁用词 / 超长 / 缺字段）**拒绝发布**；
          占位残留（placeholder）可在 ``auto_clean_placeholders`` 下自动剥离后通过，
          其余硬错需修订后重新提交。

        Returns:
            GateReport：含 passed / mode / violations / cleaned（处理后文本）。
        """
        mode = GateMode(mode) if not isinstance(mode, GateMode) else mode
        current = text
        cleaned = None

        # 占位残留可自动清理（不要求重写）
        if auto_clean_placeholders:
            new_text = self._strip_placeholders(current)
            if new_text != current:
                cleaned = new_text
                current = new_text

        result = self.check(
            current, required_fields=required_fields,
            max_chars=max_chars, min_chars=min_chars,
        )

        if mode is GateMode.BLOCK:
            # G6：block 模式下 AI 味命中（默认 warn）提升为 error，纳入拒绝发布判定（拍板 #4）
            for v in result.violations:
                if v.rule_id == AI_FLAVOR_RULE_ID and v.severity == "warn":
                    v.severity = "error"
            passed = result.passed  # error 级（空/禁用词/超长/缺字段/AI 味）一律拒绝
        else:
            passed = result.passed

        return GateReport(
            passed=passed,
            mode=mode,
            violations=[v.to_dict() for v in result.violations],
            cleaned=cleaned,
            text=current,
        )

    def _strip_placeholders(self, text: str) -> str:
        out = text
        for pat in self.placeholder_patterns:
            out = pat.sub("", out)
        return out

    # ---------------------------------------------------------------- G14 辅助
    @staticmethod
    def _normalize_paragraph(p: str) -> str:
        """段落归一化：去空白 + 标点，供指纹 hash 与相似度比对。

        保留中文、英文、数字；去除空白与所有 Unicode 标点（含中文标点）。
        （标准库 re 不支持 \p{P}，用 Unicode 范围显式排除。）
        """
        # 去除空白
        s = re.sub(r"\s+", "", p)
        # 去除 Unicode 标点/符号/分隔符（保留 Letter/Number 类别）
        # 中文字符范围 + 拉丁字母数字 之外的标点统一删掉
        s = re.sub(
            r"[\u0000-\u0020\u0021-\u002f\u003a-\u0040\u005b-\u0060\u007b-\u007e"
            r"\u2000-\u206f\u3000-\u303f\uff00-\uffef]",
            "",
            s,
        )
        return s

    @staticmethod
    def _paragraph_similarity(a: str, b: str) -> float:
        """基于字符集合 Jaccard 的段落相似度（0~1）。"""
        sa, sb = set(a), set(b)
        if not sa and not sb:
            return 1.0
        if not sa or not sb:
            return 0.0
        inter = len(sa & sb)
        union = len(sa | sb)
        return inter / union if union else 0.0

    def _check_junk(self, text: str) -> str | None:
        """英文/杂质残留检测：连续英文单词（豁免白名单）+ 工具特征串。

        先剥离 YAML frontmatter（含 chapter/created_at 等英文字段，非正文），避免误伤。
        """
        body = re.sub(r"^---[\s\S]*?---", "", text, flags=re.MULTILINE)  # 去 frontmatter
        # 工具/系统残留特征串（强信号，直接判；仅扫正文）
        for sig in _JUNK_SIGNATURES:
            if sig.lower() in body.lower():
                return f"检测到工具/系统残留特征串：{sig!r}（正文不应混入英文/系统返回）"
        # 连续英文单词（≥3 字母），排除白名单
        for m in _RE_ENGLISH_WORD.finditer(body):
            word = m.group(0)
            if word.lower() not in self.junk_whitelist:
                return f"检测到正文混入英文单词：{word!r}（成书应为纯中文，白名单豁免：{sorted(self.junk_whitelist) or '无'}）"
        return None

    def _check_title(self, text: str) -> str | None:
        """标题合规检测：首个 # 第N章·... 标题。"""
        m = _TITLE_RE.search(text)
        if not m:
            return "未检测到合规章节标题（应为「# 第N章 · <有信息量的标题>」）"
        title_body = m.group(2).strip()
        if not title_body:
            return "章节标题为空（必须给出一句有信息量的标题）"
        if len(title_body) < _TITLE_MIN_LEN:
            return f"章节标题过短（{len(title_body)} 字 < 下限 {_TITLE_MIN_LEN}），疑似占位"
        # 占位标题：标题正文等于/包含「第N章」自身（如「第5章·第5章」）
        if title_body == f"第{m.group(1)}章" or title_body.startswith(f"第{m.group(1)}章"):
            return f"章节标题为占位（「第{m.group(1)}章·第{m.group(1)}章」），必须改写为场景化标题"
        # ★ G3（2026-09-21 灵荒工坊实验实锤）：写手把细纲契约标签当标题
        #   （ch004 落盘「第 4 章 · 档位=垫片」）——「档位=垫片」恰 4 字符
        #   躲过 _TITLE_MIN_LEN。与正文元指令泄漏（G14）同级：细纲字段名
        #   （档位/章首钩子/章尾钩子/爽点/在场/禁/验收）是给写手的**指令**，
        #   不是小说文本，出现在标题即判失败打回重写。
        if _TITLE_CONTRACT_LABEL_RE.search(title_body):
            return (
                f"章节标题含细纲契约标签（「{title_body}」）——字段名是写作指令"
                "不是正文，必须改写为场景化标题"
            )
        # 与全书已发布标题重复
        if title_body in self.published_titles:
            return f"章节标题与已发布章节重复：{title_body!r}"
        return None

    def _check_dup(self, text: str) -> list[str]:
        """跨章段落去重：提取 ≥40 字长段落，与全书指纹库比对相似度。"""
        # 按空行分段，剥离 frontmatter
        body = re.sub(r"^---[\s\S]*?---", "", text, flags=re.MULTILINE)  # 去 frontmatter
        paras = [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip()]
        hits: list[str] = []
        seen_chapters: set[str] = set()
        for para in paras:
            if len(para) < _DUP_MIN_CHARS:
                continue
            norm = self._normalize_paragraph(para)
            if not norm:
                continue
            phash = hash(norm)
            # 与全书指纹库比对（调用方应已排除自身章；此处仅比对已有库）
            for ch, hlist in self.fingerprint_db.items():
                if ch in seen_chapters:
                    continue
                matched_msg: str | None = None
                for eh in hlist:
                    if isinstance(eh, tuple):
                        sim = self._paragraph_similarity(norm, eh[1])
                        if sim >= _DUP_SIMILARITY:
                            matched_msg = (
                                f"第 {ch} 章存在高度相似段落"
                                f"（相似度 {sim:.2f} ≥ {_DUP_SIMILARITY}），疑似跨章重复"
                            )
                            break
                    elif eh == phash:
                        matched_msg = f"第 {ch} 章存在完全相同段落，疑似跨章复制"
                        break
                if matched_msg:
                    hits.append(matched_msg)
                    seen_chapters.add(ch)
                    break
        return hits

    def _check_meta_leak(self, text: str) -> str | None:
        """写作元指令泄漏检测：章末悬念/章节钩子/内部章节号等指令被写入正文。

        两层检测（2026-09-13 无灵 ch166/176/206/260 事故复盘后升级）：
          1. 词表层：``_META_LEAK_RE``——已知泄漏短语/句式，命中即报；
          2. 结构层（类级，词表追不上的变体兜底）：章末出现**结构化清单块**
             （连续编号行 / 连续 Markdown 要点行）。小说正文以叙事段落组织，
             章末挂编号/要点清单几乎必然是 LLM 的自查报告/扩写汇报/修订
             尾注——与具体措辞无关，故能拦住词表外的未来变体。
        先剥离 YAML frontmatter（含 chapter/created_at 等非正文字段），仅扫正文。
        """
        body = re.sub(r"^---[\s\S]*?---", "", text, flags=re.MULTILINE)  # 去 frontmatter

        # 2.5) 契约批注块（**全文**扫，不只章末）：2026-09-18 新增。
        # 词表层曾能抓到「章末悬念」，但写手会把**整块规划批注**带进正文（实测
        # ch003.md:254 `- *钩子：章末悬念从笼统的…*`，位于章中而非章末）；
        # 类级指纹认「行首列表标记 + 契约语义标签 + 冒号」，与措辞无关。
        annotations = find_contract_annotations(body)
        if annotations:
            return (
                f"检测到规划批注块泄漏：正文出现契约批注行「{annotations[0][:40]}」"
                "（细纲字段名/批注是 agent→LLM 的指令，不得写入交付正文）"
            )

        m = _META_LEAK_RE.search(body)
        if m:
            return (
                f"检测到写作元指令泄漏：正文出现标记「{m.group(0)}」"
                f"（章末悬念/钩子/内部章节号是 agent→LLM 的指令，不得写入交付正文）"
            )
        return self._check_tail_structured_list(body)

    def _check_tail_structured_list(self, body: str) -> str | None:
        """结构层：正文末尾的结构化清单块检测（与措辞无关的类级指纹）。"""
        lines = [ln.strip() for ln in body.rstrip().splitlines() if ln.strip()]
        if not lines:
            return None
        tail = lines[-_META_LEAK_TAIL_LINES:]
        numbered = sum(1 for ln in tail if _NUMBERED_LIST_RE.match(ln))
        bullets = sum(1 for ln in tail if _BULLET_LIST_RE.match(ln))
        if numbered >= _META_LEAK_LIST_MIN_LINES:
            return (
                f"章末出现编号清单块（末 {_META_LEAK_TAIL_LINES} 行内 {numbered} 行编号列表）"
                "——小说正文不应以编号清单收尾，疑似 LLM 自查报告/扩写汇报/修订"
                "尾注混入交付正文（无灵 ch166/176/260 同类事故），请删除或改写。"
            )
        if bullets >= _META_LEAK_LIST_MIN_LINES:
            return (
                f"章末出现要点列表块（末 {_META_LEAK_TAIL_LINES} 行内 {bullets} 行要点列表）"
                "——疑似 LLM 修改记录混入交付正文（无灵 ch206 同类事故），请删除或改写。"
            )
        return None

    def check_cross_chapter_dup(self, text: str) -> list[str]:
        """跨章段落重复公开入口（写时门禁用，2026-09-12 风险 1 前置）。

        fingerprint_db 由调用方经 ``load_fingerprints`` 注入（并剔除本章自身
        旧指纹，打回重写时不误伤）。返回命中描述列表，空 = 通过。
        """
        return self._check_dup(text)

    def register_fingerprints(self, chapter: str | int, text: str) -> None:
        """落盘后增量更新全书指纹库（仅收录 ≥40 字长段落的归一化文本）。

        2026-09-06 补缺：同时把本章标题收进 ``published_titles``——该列表此前
        只在 autowrite 启动时从磁盘加载一次，运行中发布的新标题不入库，
        ``_check_title`` 的 G14 标题查重对本次运行内发布的章节形同虚设
        （无灵 ch234《凡骨出鞘》与 ch223 同名过审实证）。canonical 文本
        （`# 第 N 章 · 标题` 开头）由调用方传入，直接解析标题行即可。
        """
        body = re.sub(r"^---[\s\S]*?---", "", text, flags=re.MULTILINE)
        m = _TITLE_RE.search(body)
        if m:
            title_body = m.group(2).strip()
            if title_body and title_body not in self.published_titles:
                self.published_titles.append(title_body)
        paras = [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip()]
        entries: list[tuple[int, str]] = []
        for para in paras:
            if len(para) < _DUP_MIN_CHARS:
                continue
            norm = self._normalize_paragraph(para)
            if norm:
                entries.append((hash(norm), norm))
        self.fingerprint_db[str(chapter)] = entries


@dataclass
class GateReport:
    """门禁结果。"""

    passed: bool
    mode: GateMode
    violations: list[dict[str, Any]] = field(default_factory=list)
    cleaned: str | None = None   # 被自动清理的内容（占位残留）摘要，None 表示无
    text: str = ""               # 处理后（可能已剥离占位）的文本

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "mode": self.mode.value,
            "violations": self.violations,
            "cleaned": self.cleaned,
            "text": self.text,
        }


# ----------------------------------------------------------------------
# 配置加载（.state/guardrails.json）
# ----------------------------------------------------------------------
def load_guardrail_config(path: str | Path | None = None) -> dict[str, Any]:
    """读取护栏配置；文件不存在 / 解析失败时返回默认配置（含默认合规词表）。

    配置键：mode（advisory|block）、banned_words、max_chars、min_chars、
    allow_warnings。``banned_words`` 缺省时填入 ``_DEFAULT_COMPLIANCE_WORDS``。
    """
    cfg: dict[str, Any] = {
        "mode": GateMode.ADVISORY.value,
        "banned_words": list(_DEFAULT_COMPLIANCE_WORDS),
        "max_chars": None,
        "min_chars": None,
        "allow_warnings": True,
        "ai_flavor_words": list(_DEFAULT_AI_FLAVOR_WORDS),   # G6
        "ai_flavor_severity": "warn",                        # G6
        # ---- G14：三类污染护栏默认配置 ----
        "junk_whitelist": [],          # 英文豁免词（人名/专有名词）
        "check_junk": True,
        "check_title": True,
        "check_dup": True,
        "check_meta_leak": True,       # G14 补充：写作元指令泄漏检测
        # ---- P2：narrative_tell / 段级密度 ----
        "check_narrative_tell": True,
        "check_density": True,
        "max_paragraph_chars": _DEFAULT_MAX_PARAGRAPH_CHARS,
        "max_sentence_chars": _DEFAULT_MAX_SENTENCE_CHARS,
    }
    if path is None:
        return cfg
    p = Path(path)
    if not p.exists():
        return cfg
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - 配置损坏也降级为默认，不阻断写作
        return cfg
    if isinstance(raw.get("banned_words"), list):
        cfg["banned_words"] = raw["banned_words"] or list(_DEFAULT_COMPLIANCE_WORDS)
    if raw.get("mode") in (GateMode.ADVISORY.value, GateMode.BLOCK.value):
        cfg["mode"] = raw["mode"]
    if "max_chars" in raw:
        cfg["max_chars"] = raw["max_chars"]
    if "min_chars" in raw:
        cfg["min_chars"] = raw["min_chars"]
    if "allow_warnings" in raw:
        cfg["allow_warnings"] = bool(raw["allow_warnings"])
    if isinstance(raw.get("ai_flavor_words"), list):
        cfg["ai_flavor_words"] = raw["ai_flavor_words"] or list(_DEFAULT_AI_FLAVOR_WORDS)
    if raw.get("ai_flavor_severity") in ("warn", "error"):
        cfg["ai_flavor_severity"] = raw["ai_flavor_severity"]
    # ---- G14 ----
    if isinstance(raw.get("junk_whitelist"), list):
        cfg["junk_whitelist"] = raw["junk_whitelist"]
    if isinstance(raw.get("check_junk"), bool):
        cfg["check_junk"] = raw["check_junk"]
    if isinstance(raw.get("check_title"), bool):
        cfg["check_title"] = raw["check_title"]
    if isinstance(raw.get("check_dup"), bool):
        cfg["check_dup"] = raw["check_dup"]
    if isinstance(raw.get("check_meta_leak"), bool):
        cfg["check_meta_leak"] = raw["check_meta_leak"]
    if isinstance(raw.get("check_narrative_tell"), bool):
        cfg["check_narrative_tell"] = raw["check_narrative_tell"]
    if isinstance(raw.get("check_density"), bool):
        cfg["check_density"] = raw["check_density"]
    for k in ("max_paragraph_chars", "max_sentence_chars"):
        v = raw.get(k)
        if isinstance(v, int) and v > 0:
            cfg[k] = v
    return cfg


def build_guardrails(
    path: str | Path | None = None,
    *,
    published_titles: list[str] | None = None,
    fingerprint_db: dict[str, list[str]] | None = None,
) -> "Guardrails":
    """按配置构建 ``Guardrails`` 实例（含门禁模式与默认合规词表）。

    G14 扩展：``published_titles`` 注入全书标题用于标题重复判定；
    ``fingerprint_db`` 注入全书指纹库用于跨章去重（决策③：存 .state/ 下）。
    """
    cfg = load_guardrail_config(path)
    return Guardrails(
        banned_words=cfg["banned_words"],
        max_chars=cfg["max_chars"],
        min_chars=cfg["min_chars"],
        allow_warnings=cfg["allow_warnings"],
        ai_flavor_words=cfg["ai_flavor_words"],        # G6
        ai_flavor_severity=cfg["ai_flavor_severity"],  # G6
        junk_whitelist=cfg["junk_whitelist"],
        check_junk=cfg["check_junk"],
        check_title=cfg["check_title"],
        check_dup=cfg["check_dup"],
        check_meta_leak=cfg["check_meta_leak"],
        check_narrative_tell=cfg["check_narrative_tell"],
        check_density=cfg["check_density"],
        max_paragraph_chars=cfg["max_paragraph_chars"],
        max_sentence_chars=cfg["max_sentence_chars"],
        published_titles=published_titles,
        fingerprint_db=fingerprint_db,
    )


# ----------------------------------------------------------------------
# 全书指纹库持久化（决策③：存 .state/ 下，随章节落盘增量更新）
# ----------------------------------------------------------------------
def load_fingerprints(path: str | Path | None = None) -> dict[str, list[str]]:
    """读取全书指纹库。结构：{章号(str): [[hash, 归一化文本], ...]}。

    文件不存在 / 解析失败 → 返回空库（降级，不阻断写作）。
    """
    p = Path(path) if path else Path(DEFAULT_FINGERPRINT_PATH)
    if not p.exists():
        return {}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        db = raw.get("fingerprints", {}) if isinstance(raw, dict) else {}
        # 兼容存储格式：tuple 在 JSON 中序列化为 [hash, norm]
        out: dict[str, list[str]] = {}
        for ch, entries in db.items():
            if isinstance(entries, list):
                # 还原为 (hash, norm) 元组列表（register_fingerprints 内部用 tuple）
                out[ch] = [tuple(e) if isinstance(e, list) else e for e in entries]  # type: ignore[arg-type]
        return out
    except Exception:  # noqa: BLE001 - 损坏降级为空
        return {}


def save_fingerprints(
    db: dict[str, list[str]], path: str | Path | None = None
) -> None:
    """写入全书指纹库（原子写）。db 的 value 为 (hash, norm) 元组列表。"""
    p = Path(path) if path else Path(DEFAULT_FINGERPRINT_PATH)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        # 元组序列化为 [hash, norm] 以便 JSON 存储
        serializable = {
            ch: [list(e) if isinstance(e, tuple) else e for e in entries]
            for ch, entries in db.items()
        }
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(
            json.dumps({"fingerprints": serializable}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp.replace(p)
    except Exception:  # noqa: BLE001 - 持久化失败不影响主流程
        pass  # noqa: SILENT_DEGRADE


# ----------------------------------------------------------------------
# G14 指纹库自校验（写时门禁的唯一正确入口）
# ----------------------------------------------------------------------
def canonical_chapter_key(stem: str) -> str:
    """章文件名 stem（``ch036`` / ``036``）→ 规范键（``36``）。

    历史数据里同一章存在 ``36`` / ``ch036`` / ``ch36`` 三种写法
    （见 ``core/story/chapter_invalidation._chapter_keys``），而写时门禁按
    ``str(chapter_num)`` 剔除本章自身 ⇒ **只有规范键才能被正确剔除**，否则
    本章旧段落与自身旧指纹比对，相似度恒 1.0 的假阳性会打回重写（振荡）。
    """
    m = re.fullmatch(r"(?:ch)?0*(\d+)", stem.strip(), flags=re.IGNORECASE)
    return m.group(1) if m else stem.strip()


def rebuild_fingerprints(
    project_dir: str | Path,
    *,
    chapters_dir: str | Path | None = None,
    save: bool = True,
) -> dict[str, list]:
    """从**章节文件**重建全书指纹库（唯一真源），并回写缓存。

    为什么不能盲信缓存：``.state/chapter_fingerprints.json`` 只在「写章 / 改写 /
    回滚」等少数路径增量更新，任何**带外改动**（回滚后重生成、批量重写、人工
    编辑）都会让它与成书脱节，而写时去重门禁对缓存是盲信的 ⇒ 真重复漏检、
    又与已不存在的旧文本比对。实证（2026-09-24 灵荒工坊）：45 章里 14 章的
    缓存指纹与章文件**零重叠**（内容已被换掉），ch021 与 ch036 相似度 0.99 的
    重复段落在 ``rewrite --gate block`` 下照常落盘；离线用「现读章文件」重建
    指纹库时同一段立即可检出——缺陷在缓存，不在判定规则。
    """
    project_dir = Path(project_dir)
    chapters = Path(chapters_dir) if chapters_dir else project_dir / "chapters"
    gr = Guardrails(
        check_junk=False, check_title=False, check_dup=False,
        check_meta_leak=False, check_narrative_tell=False,
        check_density=False, check_hard_pollution=False,
    )
    db: dict[str, list] = {}
    if chapters.exists():
        for f in sorted(chapters.glob("ch*.md")):
            try:
                text = f.read_text(encoding="utf-8")
            except OSError:
                continue  # noqa: SILENT_DEGRADE reason=expected-skip - 单章不可读则跳过该章
            gr.register_fingerprints(canonical_chapter_key(f.stem), text)
            db.update(gr.fingerprint_db)
    if save:
        save_fingerprints(db, project_dir / ".state" / "chapter_fingerprints.json")
    return db


def load_book_fingerprints(
    project_dir: str | Path,
    *,
    exclude: int | str | None = None,
) -> dict[str, list]:
    """加载**已按章文件校验**的全书指纹库（重建后可用，可选剔除一章）。

    这是写时跨章去重的正确入口，替代直接 ``load_fingerprints(缓存路径)``：
    后者返回的是可能已过期的缓存（见 ``rebuild_fingerprints`` 的实证说明）。
    ``exclude`` 用于剔除本章自身（``str(chapter_num)`` 口径）。
    """
    db = rebuild_fingerprints(project_dir)
    if exclude is not None:
        db.pop(str(exclude), None)
    return db


# ----------------------------------------------------------------------
# G14 全量段落去重扫描（完本关卡，由 compose 体检经依赖注入触发）
# ----------------------------------------------------------------------
def fullbook_dup_scan(project_dir: str | Path) -> None:
    """全书跨章重复段落扫描（G14）。

    遍历 ``<project>/chapters/ch*.md``，逐章检测与已登记指纹的重复，
    命中时写 ``<project>/.state/dup_scan_report.md``；扫描完成后同步刷新
    全书指纹库（决策③：持久化到 .state/chapter_fingerprints.json）。

    归属 quality/（复用 Guardrails 指纹机制），由调用方（如 compose 体检）
    注入触发，infra/compose_runner 不直接依赖本模块。
    """
    project_dir = Path(project_dir)
    gr = Guardrails(check_junk=False, check_title=False, check_dup=True, check_meta_leak=True)
    db: dict[str, list] = {}
    dup_report: list[str] = []
    chapters_dir = project_dir / "chapters"
    if chapters_dir.exists():
        for f in sorted(chapters_dir.glob("ch*.md")):
            try:
                text = f.read_text(encoding="utf-8")
            except Exception:  # noqa: BLE001
                continue  # noqa: SILENT_DEGRADE
            ch_num = canonical_chapter_key(f.stem)
            hits = gr._check_dup(text)
            if hits:
                dup_report.append(f"### {ch_num}\n" + "\n".join(f"- {h}" for h in hits))
            gr.register_fingerprints(ch_num, text)
            db.update(gr.fingerprint_db)
    if dup_report:
        report_path = project_dir / ".state" / "dup_scan_report.md"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            "# 完本全量段落去重扫描报告\n\n" + "\n\n".join(dup_report) + "\n",
            encoding="utf-8",
        )
        print(f"⚠ 检测到跨章重复内容，报告见：{report_path}")
    else:
        print("✅ 全量段落去重扫描：未检测到跨章重复内容。")
    # 同步刷新指纹库（决策③：全书指纹库持久化）
    save_fingerprints(db, project_dir / ".state" / "chapter_fingerprints.json")
