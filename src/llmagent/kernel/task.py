"""Task 数据模型：TaskSpec / TaskRun / 状态机 / 类型"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Protocol


class TaskKind(str, Enum):
    """Task 类型（M0：LLM / TOOL / WORKFLOW / VALIDATOR 必须，其余占位）"""

    LLM = "LLM"
    TOOL = "TOOL"
    WORKFLOW = "WORKFLOW"
    VALIDATOR = "VALIDATOR"
    SKILL = "SKILL"
    HUMAN = "HUMAN"
    CONTROL = "CONTROL"
    AGENT = "AGENT"


class TaskStatus(str, Enum):
    """TaskRun 状态机"""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"
    DEGRADED = "DEGRADED"
    TIMED_OUT = "TIMED_OUT"
    CANCELLED = "CANCELLED"


@dataclass
class FailurePolicy:
    """失败策略声明"""

    max_retries: int = 0
    retry_delay_s: float = 1.0
    retry_backoff: float = 2.0
    ignore_failure: bool = False
    escalate_on: list[str] = field(default_factory=list)


@dataclass
class ValidationPolicy:
    """校验策略声明"""

    validators: list[str] = field(default_factory=list)
    chain: list[str] = field(default_factory=list)
    strict: bool = False


@dataclass
class TaskSpec:
    """Task 规格定义（不可变，注册后不可修改）"""

    name: str
    kind: TaskKind
    description: str = ""
    input_schema: dict[str, Any] = field(default_factory=dict)
    output_schema: dict[str, Any] = field(default_factory=dict)
    failure_policy: FailurePolicy = field(default_factory=FailurePolicy)
    validation_policy: ValidationPolicy = field(default_factory=ValidationPolicy)
    tags: dict[str, str] = field(default_factory=dict)
    timeout_s: float = 300.0
    budget_category: str = "default"

    def __hash__(self) -> int:
        return hash(self.name)


@dataclass
class TaskRun:
    """Task 运行时实例"""

    run_id: str
    spec: TaskSpec
    status: TaskStatus = TaskStatus.PENDING
    parent_run_id: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    started_at: datetime | None = None
    ended_at: datetime | None = None
    attempt: int = 0
    error: str = ""
    output: dict[str, Any] = field(default_factory=dict)
    budget_ref: str = ""
    trace_id: str = ""


class Executor(Protocol):
    """Task 执行器协议"""

    kind: TaskKind

    async def execute(self, run: TaskRun) -> TaskRun:
        ...