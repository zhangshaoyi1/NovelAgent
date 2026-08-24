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

# ---- G13：三类成书污染护栏（拍板：英文残留 / 占位标题 / 跨章重复）----
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
_TITLE_MAX_REPEAT = 2       # 标题与已发布标题重复即判违规
# 3) 跨章段落去重：全书指纹库比对（去空白+标点归一化 hash，≥40字长段落，相似度>0.85）
DUP_RULE_ID: str = "paragraph_dup"
_DUP_MIN_CHARS = 40         # 仅对 ≥40 字长段落比对，避免短句误杀
_DUP_SIMILARITY = 0.85      # 相似度阈值
# 默认配置路径
DEFAULT_GUARDRAIL_CONFIG_PATH = ".state/guardrails.json"
# 全书指纹库默认路径（决策③：存 .state/ 下）
DEFAULT_FINGERPRINT_PATH = ".state/chapter_fingerprints.json"


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
        # ---- G13：三类污染护栏配置 ----
        junk_whitelist: list[str] | None = None,     # 英文豁免词（人名/专有名词；默认空）
        check_junk: bool = True,                     # 英文/杂质残留检测开关
        check_title: bool = True,                    # 标题合规检测开关
        check_dup: bool = True,                      # 跨章段落去重开关
        published_titles: list[str] | None = None,   # 全书已发布标题（用于标题重复判定）
        fingerprint_db: dict[str, list[str]] | None = None,  # 全书指纹库 {章号: [段落hash]}
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
        # ---- G13 ----
        self.junk_whitelist = set(w.strip() for w in (junk_whitelist or []))
        self.check_junk = check_junk
        self.check_title = check_title
        self.check_dup = check_dup
        self.published_titles: list[str] = list(published_titles or [])
        # 指纹库：章号(str) -> 段落归一化 hash 列表；落盘后由调用方增量更新
        self.fingerprint_db: dict[str, list[str]] = dict(fingerprint_db or {})

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

        # ---- G13：三类成书污染护栏 ----
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

        return GuardrailResult(violations)

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
                parsed = None
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

    # ---------------------------------------------------------------- G13 辅助
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

    def register_fingerprints(self, chapter: str | int, text: str) -> None:
        """落盘后增量更新全书指纹库（仅收录 ≥40 字长段落的归一化文本）。"""
        body = re.sub(r"^---[\s\S]*?---", "", text, flags=re.MULTILINE)
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
        # ---- G13：三类污染护栏默认配置 ----
        "junk_whitelist": [],          # 英文豁免词（人名/专有名词）
        "check_junk": True,
        "check_title": True,
        "check_dup": True,
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
    # ---- G13 ----
    if isinstance(raw.get("junk_whitelist"), list):
        cfg["junk_whitelist"] = raw["junk_whitelist"]
    if isinstance(raw.get("check_junk"), bool):
        cfg["check_junk"] = raw["check_junk"]
    if isinstance(raw.get("check_title"), bool):
        cfg["check_title"] = raw["check_title"]
    if isinstance(raw.get("check_dup"), bool):
        cfg["check_dup"] = raw["check_dup"]
    return cfg


def build_guardrails(
    path: str | Path | None = None,
    *,
    published_titles: list[str] | None = None,
    fingerprint_db: dict[str, list[str]] | None = None,
) -> "Guardrails":
    """按配置构建 ``Guardrails`` 实例（含门禁模式与默认合规词表）。

    G13 扩展：``published_titles`` 注入全书标题用于标题重复判定；
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
        pass
