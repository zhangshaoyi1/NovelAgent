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

# 默认配置路径
DEFAULT_GUARDRAIL_CONFIG_PATH = ".state/guardrails.json"


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
    ) -> None:
        self.banned_words = list(banned_words if banned_words is not None else _DEFAULT_BANNED)
        self.placeholder_patterns = [
            re.compile(p, re.IGNORECASE)
            for p in (placeholder_patterns if placeholder_patterns is not None else _DEFAULT_PLACEHOLDERS)
        ]
        self.max_chars = max_chars
        self.min_chars = min_chars
        self.allow_warnings = allow_warnings

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
            passed = result.passed  # error 级（空/禁用词/超长/缺字段）一律拒绝
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
    return cfg


def build_guardrails(path: str | Path | None = None) -> "Guardrails":
    """按配置构建 ``Guardrails`` 实例（含门禁模式与默认合规词表）。"""
    cfg = load_guardrail_config(path)
    return Guardrails(
        banned_words=cfg["banned_words"],
        max_chars=cfg["max_chars"],
        min_chars=cfg["min_chars"],
        allow_warnings=cfg["allow_warnings"],
    )
