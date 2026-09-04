"""LLM 调用结果通用校验（base 层，仅依赖 base）

设计来源：《LLM调用静态分析.md》§6 —— 「理论上每个 LLM 调用都应有结果验证」。
本模块把「校验」塞进唯一 LLM 出口（LLMClient.chat），让业务调用点用声明式
``ValidationSpec`` 描述对返回结果的约束，物理上保证「每个调用都逃不掉校验」。

分层约定：放在 base 层（只依赖 agent.base.llm.LLMResponse），使 client 与所有
workflow 都能零负担 import，不引入 client→core 反向依赖。

校验分级：
- P0：阻断。失败后自动附「请严格遵守要求」重试，耗尽抛 ``ValidationError``。
- P1：告警。仅把问题写入 ``resp.warnings``，不阻断后续流程（兼容现有「解析失败放行」语义）。

内置校验器：min_length / max_length / not_empty / json_valid(+required_keys) /
forbid_patterns / score_in_range(json 路径取分) / custom(任意函数)。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable

from agent.base.llm import LLMResponse

DEFAULT_MAX_RETRIES: int = 2  # P0 失败后带修正提示的重试上限


class ValidationError(Exception):
    """P0 校验失败且重试耗尽"""


@dataclass
class ValidationSpec:
    """声明式校验规格

    kind 决定校验逻辑；severity 决定失败处置（P0 阻断重试 / P1 仅告警）。
    其余字段为对应 kind 的参数；未用到的参数忽略。
    """

    kind: str  # min_length|max_length|not_empty|json_valid|forbid_patterns|score_in_range|custom
    severity: str = "P0"  # "P0" | "P1"
    # ---- 参数 ----
    min_length: int = 0
    max_length: int = 0
    required_keys: list[str] = field(default_factory=list)
    patterns: list[str] = field(default_factory=list)  # forbid_patterns：正则命中即失败
    score_path: str = "score"  # score_in_range：从 JSON 取值的键（支持 a.b.c）
    low: float | None = None
    high: float | None = None
    custom: "Callable[[str], list[str]] | None" = None  # 返回问题列表，空=通过

    # ---- 便捷构造器（调用点可读性更好） ----
    @classmethod
    def min_length(cls, n: int, severity: str = "P0") -> "ValidationSpec":
        return cls(kind="min_length", severity=severity, min_length=n)

    @classmethod
    def max_length(cls, n: int, severity: str = "P0") -> "ValidationSpec":
        return cls(kind="max_length", severity=severity, max_length=n)

    @classmethod
    def not_empty(cls, severity: str = "P0") -> "ValidationSpec":
        return cls(kind="not_empty", severity=severity, min_length=1)

    @classmethod
    def json_valid(
        cls, severity: str = "P0", required_keys: "list[str] | None" = None
    ) -> "ValidationSpec":
        return cls(kind="json_valid", severity=severity, required_keys=required_keys or [])

    @classmethod
    def forbid_patterns(
        cls, patterns: "list[str]", severity: str = "P1"
    ) -> "ValidationSpec":
        return cls(kind="forbid_patterns", severity=severity, patterns=patterns)

    @classmethod
    def score_in_range(
        cls,
        low: float | None = None,
        high: float | None = None,
        score_path: str = "score",
        severity: str = "P0",
    ) -> "ValidationSpec":
        return cls(
            kind="score_in_range",
            severity=severity,
            low=low,
            high=high,
            score_path=score_path,
        )

    @classmethod
    def custom(
        cls, fn: "Callable[[str], list[str]]", severity: str = "P0"
    ) -> "ValidationSpec":
        return cls(kind="custom", severity=severity, custom=fn)


def _get_path(data: Any, path: str) -> Any:
    cur = data
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def _check_one(spec: ValidationSpec, text: str) -> list[str]:
    """运行单条校验，返回问题列表（空=通过）。"""
    if spec.kind == "min_length":
        if len(text.strip()) < spec.min_length:
            return [f"输出长度 {len(text.strip())} 小于下限 {spec.min_length}"]
        return []
    if spec.kind == "max_length":
        if len(text.strip()) > spec.max_length:
            return [f"输出长度 {len(text.strip())} 大于上限 {spec.max_length}"]
        return []
    if spec.kind == "not_empty":
        if not text.strip():
            return ["输出为空"]
        return []
    if spec.kind == "json_valid":
        try:
            data = json.loads(text)
        except Exception:
            return ["输出不是合法 JSON"]
        if spec.required_keys:
            if not isinstance(data, dict):
                return ["JSON 根不是对象"]
            missing = [k for k in spec.required_keys if k not in data]
            if missing:
                return [f"缺少必填字段: {missing}"]
        return []
    if spec.kind == "forbid_patterns":
        hits = [p for p in spec.patterns if re.search(p, text)]
        if hits:
            return [f"命中禁止模式: {hits}"]
        return []
    if spec.kind == "score_in_range":
        try:
            data = json.loads(text)
        except Exception:
            return ["输出不是合法 JSON，无法取分数"]
        val = _get_path(data, spec.score_path)
        if not isinstance(val, (int, float)):
            return [f"分数路径 {spec.score_path} 取值非数字: {val!r}"]
        if spec.low is not None and val < spec.low:
            return [f"分数 {val} 低于下限 {spec.low}"]
        if spec.high is not None and val > spec.high:
            return [f"分数 {val} 高于上限 {spec.high}"]
        return []
    if spec.kind == "custom":
        if spec.custom is not None:
            try:
                return list(spec.custom(text))
            except Exception as e:  # noqa: BLE001
                return [f"custom 校验异常: {e}"]
        return []
    return [f"未知校验类型: {spec.kind}"]


class ValidationEngine:
    """校验引擎：跑一组 ValidationSpec，按严重度分流。"""

    @staticmethod
    def run(
        resp: LLMResponse, specs: "list[ValidationSpec] | None"
    ) -> dict[str, list[str]]:
        """返回 {'p0': [...阻断问题], 'p1': [...告警]}。specs 为空则全空。"""
        result: dict[str, list[str]] = {"p0": [], "p1": []}
        if not specs:
            return result
        for spec in specs:
            issues = _check_one(spec, resp.text)
            if not issues:
                continue
            if spec.severity == "P0":
                result["p0"].extend(issues)
            else:
                result["p1"].extend(issues)
        return result


__all__ = [
    "ValidationError",
    "ValidationSpec",
    "ValidationEngine",
    "DEFAULT_MAX_RETRIES",
]
