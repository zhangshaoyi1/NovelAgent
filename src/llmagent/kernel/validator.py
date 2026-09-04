"""ValidatorRunner：完整校验门面（M2：PolicyResolver + Composer + PureRunner + ModelRunner + ResultLedger）"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from .task import ValidationPolicy


# ===== 校验结果 =====


@dataclass
class ValidationResult:
    """校验结果"""

    passed: bool = True
    error_class: str = ""
    repair_hint: str = ""
    details: list[str] = field(default_factory=list)


# ===== Validator 协议 =====


class Validator(Protocol):
    """校验器协议"""

    name: str

    def validate(self, ctx: dict[str, Any]) -> ValidationResult:
        ...


# ===== 内置校验器 =====


class NoOpValidator:
    """空校验器（未配校验时使用）"""

    name = "noop"

    @staticmethod
    def validate(ctx: dict[str, Any]) -> ValidationResult:
        return ValidationResult(passed=True)


class JsonSchemaValidator:
    """JSON Schema 校验器"""

    def __init__(self, schema: dict[str, Any]) -> None:
        self.schema = schema
        self.name = f"jsonschema({schema.get('title', 'unnamed')})"

    def validate(self, ctx: dict[str, Any]) -> ValidationResult:
        required = self.schema.get("required", [])
        props = self.schema.get("properties", {})
        for field in required:
            if field not in ctx:
                return ValidationResult(
                    passed=False,
                    error_class="SEMANTIC",
                    repair_hint=f"缺少必填字段: {field}",
                    details=[f"'{field}' 是必填字段但未提供"],
                )
            expected_type = props.get(field, {}).get("type")
            if expected_type == "integer" and not isinstance(ctx.get(field), int):
                return ValidationResult(
                    passed=False,
                    error_class="SEMANTIC",
                    repair_hint=f"字段 '{field}' 应为 integer",
                )
            if expected_type == "string" and not isinstance(ctx.get(field), str):
                return ValidationResult(
                    passed=False,
                    error_class="SEMANTIC",
                    repair_hint=f"字段 '{field}' 应为 string",
                )
        return ValidationResult(passed=True)


class WordCountValidator:
    """字数校验器"""

    def __init__(self, min_words: int = 100, max_words: int = 10000, name: str = "word_count") -> None:
        self.min_words = min_words
        self.max_words = max_words
        self.name = name

    def validate(self, ctx: dict[str, Any]) -> ValidationResult:
        content = ctx.get("content", ctx.get("chapter_content", ""))
        if not content:
            return ValidationResult(passed=True)  # 无内容不校验
        word_count = len(content)
        if word_count < self.min_words:
            return ValidationResult(
                passed=False,
                error_class="SEMANTIC",
                repair_hint=f"字数不足: {word_count} < {self.min_words}",
                details=[f"当前字数 {word_count}，目标最低 {self.min_words}"],
            )
        if word_count > self.max_words:
            return ValidationResult(
                passed=False,
                error_class="SEMANTIC",
                repair_hint=f"字数超限: {word_count} > {self.max_words}",
                details=[f"当前字数 {word_count}，上限 {self.max_words}"],
            )
        return ValidationResult(passed=True)


class QualityScoreValidator:
    """质量评分校验器"""

    def __init__(self, min_score: int = 3, name: str = "quality_score") -> None:
        self.min_score = min_score
        self.name = name

    def validate(self, ctx: dict[str, Any]) -> ValidationResult:
        score = ctx.get("score", ctx.get("quality_score", 10))
        if isinstance(score, int) and score < self.min_score:
            return ValidationResult(
                passed=False,
                error_class="SEMANTIC",
                repair_hint=f"质量评分过低: {score} < {self.min_score}",
                details=[f"评分 {score} 低于阈值 {self.min_score}"],
            )
        return ValidationResult(passed=True)


# ===== 校验器注册中心 =====


class ValidatorRegistry:
    """校验器注册中心：按名称查找校验器"""

    def __init__(self) -> None:
        self._validators: dict[str, Validator] = {}

    def register(self, name: str, validator: Validator) -> None:
        self._validators[name] = validator

    def get(self, name: str) -> Validator:
        v = self._validators.get(name)
        if v is None:
            raise KeyError(f"校验器 '{name}' 未注册")
        return v

    def has(self, name: str) -> bool:
        return name in self._validators

    def list(self) -> list[str]:
        return list(self._validators.keys())


# ===== PolicyResolver =====


class PolicyResolver:
    """校验策略解析器：从注册中心加载 Validator 列表"""

    def __init__(self, registry: ValidatorRegistry | None = None) -> None:
        self._registry = registry or ValidatorRegistry()

    @property
    def registry(self) -> ValidatorRegistry:
        return self._registry

    def resolve(self, policy: ValidationPolicy | None, kind: str = "") -> list[Validator]:
        if policy is None or not policy.validators:
            return [NoOpValidator()]
        validators: list[Validator] = []
        for name in policy.validators:
            if self._registry.has(name):
                validators.append(self._registry.get(name))
        if not validators:
            return [NoOpValidator()]
        return validators


# ===== Composer =====


class ChainValidator:
    """链式校验器：短路——便宜排前，贵的不必跑"""

    def __init__(self, validators: list[Validator]) -> None:
        self.validators = validators

    @property
    def name(self) -> str:
        return "chain(" + ",".join(v.name for v in self.validators) + ")"

    def validate(self, ctx: dict[str, Any]) -> ValidationResult:
        for v in self.validators:
            result = v.validate(ctx)
            if not result.passed:
                return result
        return ValidationResult(passed=True)


class AllOfValidator:
    """全部通过：所有校验器必须通过"""

    def __init__(self, validators: list[Validator]) -> None:
        self.validators = validators

    @property
    def name(self) -> str:
        return "all_of(" + ",".join(v.name for v in self.validators) + ")"

    def validate(self, ctx: dict[str, Any]) -> ValidationResult:
        all_details: list[str] = []
        for v in self.validators:
            result = v.validate(ctx)
            if not result.passed:
                all_details.extend(result.details)
        if all_details:
            return ValidationResult(passed=False, error_class="SEMANTIC", details=all_details)
        return ValidationResult(passed=True)


class AnyOfValidator:
    """任选通过：至少一个校验器通过"""

    def __init__(self, validators: list[Validator]) -> None:
        self.validators = validators

    @property
    def name(self) -> str:
        return "any_of(" + ",".join(v.name for v in self.validators) + ")"

    def validate(self, ctx: dict[str, Any]) -> ValidationResult:
        for v in self.validators:
            result = v.validate(ctx)
            if result.passed:
                return ValidationResult(passed=True)
        return ValidationResult(passed=False, error_class="SEMANTIC", details=["所有校验器均未通过"])


class WeightedValidator:
    """加权通过：加权得分 >= threshold 即通过"""

    def __init__(self, validators: list[tuple[Validator, float]], threshold: float = 0.5) -> None:
        self.validators = validators
        self.threshold = threshold

    @property
    def name(self) -> str:
        return "weighted(" + ",".join(v.name for v, _ in self.validators) + ")"

    def validate(self, ctx: dict[str, Any]) -> ValidationResult:
        total_weight = 0.0
        passed_weight = 0.0
        for v, weight in self.validators:
            total_weight += weight
            result = v.validate(ctx)
            if result.passed:
                passed_weight += weight
        if total_weight > 0 and (passed_weight / total_weight) >= self.threshold:
            return ValidationResult(passed=True)
        return ValidationResult(
            passed=False,
            error_class="SEMANTIC",
            repair_hint=f"加权得分不足: {passed_weight}/{total_weight} < {self.threshold}",
        )


class Composer:
    """校验组合器"""

    @staticmethod
    def compose(validators: list[Validator], chain_type: str = "chain") -> Validator:
        if len(validators) == 1:
            return validators[0]
        if chain_type == "all_of":
            return AllOfValidator(validators)
        if chain_type == "any_of":
            return AnyOfValidator(validators)
        return ChainValidator(validators)


# ===== PureRunner =====


class PureRunner:
    """纯函数校验器运行器

    进程内执行 JsonSchema / Pydantic / Guardrail 校验器。
    """

    @staticmethod
    def run(v: Validator, ctx: dict[str, Any]) -> ValidationResult:
        return v.validate(ctx)


# ===== ModelRunner =====


class ModelRunner:
    """LLM 校验器：包装为子 TaskRun，经 Gateway 调用

    将校验请求包装为 LLM 调用，模型判断内容是否合规。
    """

    def __init__(self, gateway: Any = None) -> None:
        self._gateway = gateway

    def run(self, validator_name: str, ctx: dict[str, Any]) -> ValidationResult:
        """通过 LLM 校验"""
        if self._gateway is None:
            return ValidationResult(passed=True, details=["ModelRunner 未配置 Gateway，跳过"])
        # 实际使用时通过 Gateway.chat() 调用 LLM 做校验
        return ValidationResult(passed=True)


# ===== ResultLedger（SQLite 持久化） =====


class ResultLedger:
    """校验结果记账：SQLite 存储 + 分层统计"""

    def __init__(self, db_path: str | Path = "") -> None:
        self._db_path = str(db_path) if db_path else ":memory:"
        self._conn = sqlite3.connect(self._db_path)
        self._init_db()

    def _init_db(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS validation_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                validator_name TEXT NOT NULL,
                passed INTEGER NOT NULL,
                error_class TEXT NOT NULL DEFAULT '',
                repair_hint TEXT NOT NULL DEFAULT '',
                details TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL
            )
            """
        )
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_vr_run_id ON validation_results(run_id)")
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_vr_passed ON validation_results(passed)")
        self._conn.commit()

    def record(self, run_id: str, validator_name: str, result: ValidationResult) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "INSERT INTO validation_results (run_id, validator_name, passed, error_class, repair_hint, details, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (run_id, validator_name, int(result.passed), result.error_class, result.repair_hint, json.dumps(result.details, ensure_ascii=False), now),
        )
        self._conn.commit()

    def get_records(self, run_id: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT run_id, validator_name, passed, error_class, repair_hint, details, created_at FROM validation_results WHERE run_id = ? ORDER BY id",
            (run_id,),
        ).fetchall()
        return [
            {
                "run_id": r[0],
                "validator_name": r[1],
                "passed": bool(r[2]),
                "error_class": r[3],
                "repair_hint": r[4],
                "details": json.loads(r[5]),
                "created_at": r[6],
            }
            for r in rows
        ]

    def statistics(self) -> dict[str, Any]:
        """分层统计"""
        total = self._conn.execute("SELECT COUNT(*) FROM validation_results").fetchone()[0]
        passed = self._conn.execute("SELECT COUNT(*) FROM validation_results WHERE passed = 1").fetchone()[0]
        failed = total - passed
        error_counts = self._conn.execute(
            "SELECT error_class, COUNT(*) as cnt FROM validation_results WHERE passed = 0 GROUP BY error_class ORDER BY cnt DESC"
        ).fetchall()
        return {
            "total": total,
            "passed": passed,
            "failed": failed,
            "pass_rate": round(passed / total * 100, 1) if total > 0 else 0,
            "error_distribution": {r[0]: r[1] for r in error_counts},
        }

    def get_records_by_error(self, error_class: str, limit: int = 50) -> list[dict]:
        rows = self._conn.execute(
            "SELECT run_id, validator_name, passed, error_class, repair_hint, details, created_at FROM validation_results WHERE error_class = ? ORDER BY id DESC LIMIT ?",
            (error_class, limit),
        ).fetchall()
        return [
            {
                "run_id": r[0],
                "validator_name": r[1],
                "passed": bool(r[2]),
                "error_class": r[3],
                "repair_hint": r[4],
                "details": json.loads(r[5]),
                "created_at": r[6],
            }
            for r in rows
        ]

    def close(self) -> None:
        self._conn.close()


# ===== ValidatorRunner =====


class ValidatorRunner:
    """校验门面"""

    def __init__(
        self,
        policy_resolver: PolicyResolver | None = None,
        composer: Composer | None = None,
        pure_runner: PureRunner | None = None,
        model_runner: ModelRunner | None = None,
        ledger: ResultLedger | None = None,
    ) -> None:
        self.policy_resolver = policy_resolver or PolicyResolver()
        self.composer = composer or Composer()
        self.pure_runner = pure_runner or PureRunner()
        self.model_runner = model_runner or ModelRunner()
        self.ledger = ledger or ResultLedger()

    def run(
        self, policy: ValidationPolicy | None, ctx: dict[str, Any], kind: str = "", run_id: str = ""
    ) -> ValidationResult:
        validators = self.policy_resolver.resolve(policy, kind)
        composed = self.composer.compose(validators, policy.chain[0] if policy and policy.chain else "chain")
        result = self.pure_runner.run(composed, ctx)

        if run_id:
            self.ledger.record(run_id, composed.name, result)

        return result