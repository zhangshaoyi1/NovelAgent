"""FailureHandler：完整失败处理门面（M2：Catcher + Mutator + Compensator + Escalator）"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .redlines import (
    BUDGET_HARD_STOP,
    COMPENSATION_FAIL_ACTION,
    MAX_RETRY_PER_TRACE,
    POLICY_ERROR_FALLBACK,
)
from .task import FailurePolicy, TaskRun, TaskStatus


# ===== 数据模型 =====


@dataclass
class CaughtError:
    """捕获的异常"""

    run_id: str
    raw: str
    error_class: str = "UNKNOWN"
    source: str = "exception"  # exception / validation


@dataclass
class FailureContext:
    """失败上下文"""

    run: TaskRun
    error: CaughtError
    trace_retry_count: int = 0
    budget_used_ratio: float = 0.0
    compensation_count: int = 0
    escalation_count: int = 0


@dataclass
class FailureAction:
    """失败动作"""

    action: str = "retry"  # retry / ignore / escalate / compensate / stop
    status: TaskStatus = TaskStatus.FAILED
    message: str = ""
    retry_delay_s: float = 1.0
    compensation_hint: str = ""
    escalation_target: str = ""


# ===== Catcher =====


class Catcher:
    """异常捕获器：双来源归一化（异常 + 校验失败）"""

    @staticmethod
    def catch(run_id: str, exc: BaseException | None = None) -> CaughtError:
        if exc is None:
            return CaughtError(run_id=run_id, raw="", source="unknown")
        return CaughtError(run_id=run_id, raw=str(exc), source="exception")

    @staticmethod
    def from_validation(
        run_id: str, error_class: str, message: str, details: list[str] | None = None
    ) -> CaughtError:
        return CaughtError(
            run_id=run_id,
            raw=message,
            error_class=error_class,
            source="validation",
        )


# ===== ErrorClassifier =====


class ErrorClassifier:
    """错误分类器：按信号归一化到 8 类"""

    CLASSIFICATION_RULES = {
        "budget": "BUDGET",
        "rate": "RATE_LIMIT",
        "timeout": "TIMED_OUT",
        "connection": "TRANSIENT",
        "value": "DETERMINISTIC",
        "invalid": "DETERMINISTIC",
        "content": "CONTENT_FILTER",
        "validation": "SEMANTIC",
        "semantic": "SEMANTIC",
    }

    CLASSIFICATION_WEIGHTS = {
        "BUDGET": 1.0,
        "RATE_LIMIT": 0.3,
        "TIMED_OUT": 0.5,
        "TRANSIENT": 0.3,
        "DETERMINISTIC": 0.8,
        "CONTENT_FILTER": 0.9,
        "SEMANTIC": 0.6,
        "UNKNOWN": 0.5,
    }

    @classmethod
    def classify(cls, exc: BaseException | str) -> str:
        """分类：支持异常对象和字符串"""
        if isinstance(exc, str):
            text = exc.lower()
        else:
            text = type(exc).__name__.lower() + " " + str(exc).lower()
        for keyword, error_class in cls.CLASSIFICATION_RULES.items():
            if keyword in text:
                return error_class
        return "UNKNOWN"

    @classmethod
    def severity_weight(cls, error_class: str) -> float:
        """错误严重性权重（0~1）"""
        return cls.CLASSIFICATION_WEIGHTS.get(error_class, 0.5)


# ===== PolicyResolver =====


class PolicyResolver:
    """策略解析器：取 TaskSpec 声明的 FailurePolicy"""

    @staticmethod
    def resolve(policy: FailurePolicy | None, ctx: FailureContext) -> FailureAction:
        if policy is None:
            return FailureAction(action="stop", status=TaskStatus.FAILED, message="未配置失败策略")

        if policy.ignore_failure:
            return FailureAction(action="ignore", status=TaskStatus.SKIPPED)

        # 检查是否应升级
        if ctx.error.error_class in policy.escalate_on:
            return FailureAction(
                action="escalate",
                status=TaskStatus.FAILED,
                message=f"错误 '{ctx.error.error_class}' 在 escalate_on 列表中",
                escalation_target="human",
            )

        # 重试
        if ctx.trace_retry_count < policy.max_retries:
            delay = policy.retry_delay_s * (policy.retry_backoff ** ctx.trace_retry_count)
            return FailureAction(
                action="retry",
                status=TaskStatus.RUNNING,
                retry_delay_s=min(delay, 60.0),
            )

        return FailureAction(action="stop", status=TaskStatus.FAILED, message="重试次数耗尽")


# ===== Mutator =====


class Mutator:
    """变异器：对失败输出做原地修复尝试

    对 SEMANTIC / DETERMINISTIC 类型错误，尝试自动修复输出。
    """

    SUPPORTED_CLASSES = {"SEMANTIC", "DETERMINISTIC"}

    @staticmethod
    def mutate(ctx: FailureContext, action: FailureAction) -> FailureAction | None:
        """尝试修复；返回 None 表示无法修复"""
        if ctx.error.error_class not in Mutator.SUPPORTED_CLASSES:
            return None

        # 语义错误尝试修复
        if ctx.error.error_class == "SEMANTIC":
            action.action = "retry"
            action.message = f"Mutator 尝试修复: {ctx.error.raw[:100]}"
            action.compensation_hint = "调整输出格式后重试"
            return action

        return None


# ===== Compensator =====


class Compensator:
    """补偿器：对不可重试、不可修复的错误做补偿

    补偿策略：
    - 降级：将 FAILED 改为 DEGRADED，保留部分输出
    - 重构造：重新构建部分上下文后重试
    - 截断：截断输入后重试（CONTEXT_OVERFLOW）
    """

    def __init__(self, max_compensations: int = 3) -> None:
        self._max_compensations = max_compensations

    def compensate(self, ctx: FailureContext, action: FailureAction) -> FailureAction:
        """执行补偿"""
        if ctx.compensation_count >= self._max_compensations:
            return FailureAction(
                action="escalate",
                status=TaskStatus.FAILED,
                message=f"补偿次数超上限 ({self._max_compensations})",
            )

        error_class = ctx.error.error_class

        if error_class == "BUDGET":
            action.action = "retry"
            action.status = TaskStatus.RUNNING
            action.message = "Compensator: 尝试降级模型以节省预算"
            return action

        if error_class == "CONTEXT_OVERFLOW":
            action.action = "retry"
            action.status = TaskStatus.RUNNING
            action.message = "Compensator: 截断输入后重试"
            return action

        if error_class == "CONTENT_FILTER":
            action.action = "retry"
            action.status = TaskStatus.RUNNING
            action.message = "Compensator: 调整输出内容后重试"
            return action

        # 默认降级
        action.action = "compensate"
        action.status = TaskStatus.DEGRADED
        action.message = f"Compensator: 降级处理 ({error_class})"
        return action


# ===== Escalator =====


class Escalator:
    """升级器：补偿失败 → 转人工 / 停机

    升级策略（按 severity 递增）：
    1. 记录告警
    2. 转人工工单
    3. 停机止损
    """

    @staticmethod
    def escalate(ctx: FailureContext, action: FailureAction) -> FailureAction:
        """执行升级"""
        severity = ErrorClassifier.severity_weight(ctx.error.error_class)

        if severity < 0.5:
            # 低严重度：记录告警，继续
            action.action = "ignore"
            action.status = TaskStatus.DEGRADED
            action.message = f"Escalator: 记录告警后继续 ({ctx.error.error_class})"
            return action

        if severity < 0.8:
            # 中严重度：转人工
            action.action = "escalate"
            action.status = TaskStatus.FAILED
            action.message = f"Escalator: 转人工处理 ({ctx.error.error_class})"
            action.escalation_target = "human"
            return action

        # 高严重度：停机止损
        action.action = "stop"
        action.status = TaskStatus.FAILED
        action.message = f"Escalator: 停机止损 ({ctx.error.error_class})"
        return action


# ===== RedLineGuard =====


class RedLineGuard:
    """红线守卫：拦截越红线动作（M2 完整版：五条红线强制校验）"""

    @staticmethod
    def check(action: FailureAction, ctx: FailureContext) -> FailureAction | None:
        """检查红线；违反 → 返回改写后的动作，否则返回 None"""
        # 红线 1：全局预算熔断
        if BUDGET_HARD_STOP and ctx.budget_used_ratio >= 1.0:
            return FailureAction(action="stop", status=TaskStatus.FAILED, message="红线1: 预算熔断")

        # 红线 2：单 Trace 最大重试总数
        if ctx.trace_retry_count >= MAX_RETRY_PER_TRACE:
            return FailureAction(
                action="escalate",
                status=TaskStatus.FAILED,
                message=f"红线2: 重试超上限({MAX_RETRY_PER_TRACE})",
            )

        # 红线 3：补偿失败转人工（COMPENSATION_FAIL_ACTION）
        if action.action == "compensate" and COMPENSATION_FAIL_ACTION == "escalate_human":
            action.action = "escalate"
            action.message = "红线3: 补偿失败，转人工处理"
            return action

        # 红线 4：FailurePolicy 自身抛异常 → 降级为 NeverRetry
        if action.action == "policy_error":
            action.action = "stop"
            action.status = TaskStatus.FAILED
            action.message = f"红线4: 策略异常，降级为 {POLICY_ERROR_FALLBACK}"
            return action

        # 红线 5：补偿器循环上限
        if ctx.compensation_count >= 5:
            return FailureAction(action="stop", status=TaskStatus.FAILED, message="红线5: 补偿循环超限")

        return None


# ===== FailureHandler =====


class FailureHandler:
    """失败处理门面

    完整升级链：PolicyResolver → Mutator → RedLineGuard → Compensator → Escalator → RedLineGuard
    """

    def __init__(
        self,
        catcher: Catcher | None = None,
        classifier: ErrorClassifier | None = None,
        policy_resolver: PolicyResolver | None = None,
        mutator: Mutator | None = None,
        compensator: Compensator | None = None,
        escalator: Escalator | None = None,
        redline_guard: RedLineGuard | None = None,
    ) -> None:
        self.catcher = catcher or Catcher()
        self.classifier = classifier or ErrorClassifier()
        self.policy_resolver = policy_resolver or PolicyResolver()
        self.mutator = mutator or Mutator()
        self.compensator = compensator or Compensator()
        self.escalator = escalator or Escalator()
        self.redline_guard = redline_guard or RedLineGuard()

    def handle(
        self, run: TaskRun, error: BaseException | None = None,
        validation_error: tuple[str, str, list[str]] | None = None,
        compensation_count: int = 0,
    ) -> FailureAction:
        """处理失败

        Args:
            run: 失败的 TaskRun
            error: 原始异常（可选）
            validation_error: 校验失败信息 (error_class, message, details)
            compensation_count: 已补偿次数
        """
        # 捕获异常
        if error:
            caught = self.catcher.catch(run.run_id, error)
            caught.error_class = self.classifier.classify(error)
        elif validation_error:
            caught = self.catcher.from_validation(run.run_id, *validation_error)
        else:
            caught = CaughtError(run_id=run.run_id, raw="", source="unknown")

        ctx = FailureContext(
            run=run,
            error=caught,
            trace_retry_count=run.attempt,
            compensation_count=compensation_count,
        )

        # ① PolicyResolver：取策略
        action = self.policy_resolver.resolve(run.spec.failure_policy, ctx)

        # ② 红线拦截
        redline = self.redline_guard.check(action, ctx)
        if redline is not None:
            return redline

        # ③ Mutator：尝试修复
        if action.action in ("retry", "stop"):
            mutated = self.mutator.mutate(ctx, action)
            if mutated is not None:
                action = mutated
                redline = self.redline_guard.check(action, ctx)
                if redline is not None:
                    return redline

        # ④ Compensator：补偿（仅对 stop 动作，escalate 已是故意决策）
        if action.action == "stop":
            action = self.compensator.compensate(ctx, action)
            redline = self.redline_guard.check(action, ctx)
            if redline is not None:
                return redline

        # ⑤ Escalator：升级
        if action.action == "compensate":
            action = self.escalator.escalate(ctx, action)
            redline = self.redline_guard.check(action, ctx)
            if redline is not None:
                return redline

        return action