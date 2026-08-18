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
from typing import Any

# 默认占位符（草稿残留，绝不应出现在成书中）
_DEFAULT_PLACEHOLDERS: list[str] = [
    r"\[TODO\]", r"\[待补\]", r"\[占位\]", r"XXX", r"xxxx",
    r"未完待续（占位）", r"此处待写", r"placeholder",
]
# 默认禁用词（平台合规基线；实际项目应以配置文件覆盖为完整词表）
_DEFAULT_BANNED: list[str] = []


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
