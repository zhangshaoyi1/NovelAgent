"""事件数据模型"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class EventType(str, Enum):
    """事件类型枚举"""

    # 工作流事件
    WORKFLOW_STARTED = "workflow.started"
    WORKFLOW_COMPLETED = "workflow.completed"
    WORKFLOW_FAILED = "workflow.failed"

    # LLM 调用事件
    LLM_CALL = "llm.call"
    LLM_RETRY = "llm.retry"
    LLM_FAILED = "llm.failed"

    # 状态机事件
    STATE_TRANSITION = "state.transition"
    STATE_SNAPSHOT = "state.snapshot"

    # 章节事件
    CHAPTER_WRITTEN = "chapter.written"
    CHAPTER_REVISED = "chapter.revised"
    CHAPTER_FAILED = "chapter.failed"

    # 质量事件
    QUALITY_CHECK = "quality.check"
    QUALITY_FAILED = "quality.failed"

    # 监督事件
    SUPERVISOR_ALERT = "supervisor.alert"
    SUPERVISOR_CHECK = "supervisor.check"

    # 重试事件
    RETRY_ATTEMPTED = "retry.attempted"
    RETRY_EXHAUSTED = "retry.exhausted"

    # 系统事件
    SYSTEM_STARTUP = "system.startup"
    SYSTEM_SHUTDOWN = "system.shutdown"
    SYSTEM_ERROR = "system.error"

    # 续写事件
    RECOVERY_STARTED = "recovery.started"
    RECOVERY_COMPLETED = "recovery.completed"
    RECOVERY_FAILED = "recovery.failed"

    # 自动编排事件
    ORCHESTRATOR_PLAN = "orchestrator.plan"
    ORCHESTRATOR_DECISION = "orchestrator.decision"
    ORCHESTRATOR_EXECUTION = "orchestrator.execution"
    ORCHESTRATOR_ADJUST = "orchestrator.adjust"


@dataclass
class EventContext:
    """事件上下文——关联同一次写作流程"""
    project_dir: str = ""
    session_id: str = ""
    user_id: str = ""
    correlation_id: str = ""


@dataclass
class Event:
    """事件——Event Sourcing 的基本单元"""
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    type: str = ""
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    correlation_id: str = ""
    payload: dict = field(default_factory=dict)
    context: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "type": self.type,
            "timestamp": self.timestamp.isoformat(),
            "correlation_id": self.correlation_id,
            "payload": self.payload,
            "context": self.context,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Event:
        return cls(
            id=data.get("id", uuid.uuid4().hex),
            type=data.get("type", ""),
            timestamp=datetime.fromisoformat(data["timestamp"])
            if "timestamp" in data
            else datetime.now(timezone.utc),
            correlation_id=data.get("correlation_id", ""),
            payload=data.get("payload", {}),
            context=data.get("context", {}),
        )


@dataclass
class Snapshot:
    """状态快照——用于续写重建"""
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    correlation_id: str = ""
    state_machine: dict = field(default_factory=dict)
    progress: dict = field(default_factory=dict)
    llm_context: dict = field(default_factory=dict)
    setting_checksum: str = ""
    event_count: int = 0

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "timestamp": self.timestamp.isoformat(),
            "correlation_id": self.correlation_id,
            "state_machine": self.state_machine,
            "progress": self.progress,
            "llm_context": self.llm_context,
            "setting_checksum": self.setting_checksum,
            "event_count": self.event_count,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Snapshot:
        return cls(
            id=data.get("id", uuid.uuid4().hex),
            timestamp=datetime.fromisoformat(data["timestamp"])
            if "timestamp" in data
            else datetime.now(timezone.utc),
            correlation_id=data.get("correlation_id", ""),
            state_machine=data.get("state_machine", {}),
            progress=data.get("progress", {}),
            llm_context=data.get("llm_context", {}),
            setting_checksum=data.get("setting_checksum", ""),
            event_count=data.get("event_count", 0),
        )