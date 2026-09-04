"""人类介入：HUMAN Task + 工单 + SLA + 超时默认策略

M3.5 新增模块。
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .task import TaskKind, TaskRun, TaskSpec, TaskStatus


# ===== 数据模型 =====


@dataclass
class HumanTicket:
    """人工工单"""
    ticket_id: str = ""
    run_id: str = ""
    task_name: str = ""
    title: str = ""
    description: str = ""
    context: dict[str, Any] = field(default_factory=dict)
    status: str = "pending"  # pending / in_progress / resolved / rejected / timed_out
    sla_seconds: float = 3600.0  # 默认 1 小时
    created_at: str = ""
    resolved_at: str = ""
    resolution: str = ""
    assigned_to: str = ""


@dataclass
class SLAPolicy:
    """SLA 策略"""
    sla_seconds: float = 3600.0  # 超时时间
    default_action: str = "skip"  # skip / degrade / fail
    escalate_on_timeout: bool = False


# 默认 HUMAN TaskSpec
HUMAN_TASK_SPEC = TaskSpec(
    name="human_intervention",
    kind=TaskKind.HUMAN,
    description="需要人工介入的任务",
    input_schema={
        "type": "object",
        "required": ["title", "description"],
        "properties": {
            "title": {"type": "string"},
            "description": {"type": "string"},
            "context": {"type": "object"},
        },
    },
    output_schema={
        "type": "object",
        "properties": {
            "resolution": {"type": "string"},
            "approved": {"type": "boolean"},
        },
    },
    timeout_s=7200.0,  # 2 小时超时
)


# ===== HumanTicketManager =====


class HumanTicketManager:
    """人工工单管理器"""

    def __init__(self, db_path: str | Path = "") -> None:
        self._db_path = str(db_path) if db_path else ":memory:"
        self._conn = sqlite3.connect(self._db_path)
        self._init_db()

    def _init_db(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS human_tickets (
                ticket_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                task_name TEXT NOT NULL,
                title TEXT NOT NULL,
                description TEXT NOT NULL,
                context TEXT NOT NULL DEFAULT '{}',
                status TEXT NOT NULL DEFAULT 'pending',
                sla_seconds REAL NOT NULL DEFAULT 3600.0,
                created_at TEXT NOT NULL,
                resolved_at TEXT NOT NULL DEFAULT '',
                resolution TEXT NOT NULL DEFAULT '',
                assigned_to TEXT NOT NULL DEFAULT ''
            )
            """
        )
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_ticket_status ON human_tickets(status)")
        self._conn.commit()

    def create_ticket(
        self,
        run_id: str,
        task_name: str,
        title: str,
        description: str,
        context: dict[str, Any] | None = None,
        sla_seconds: float = 3600.0,
    ) -> HumanTicket:
        ticket = HumanTicket(
            ticket_id=f"ticket-{uuid.uuid4().hex[:8]}",
            run_id=run_id,
            task_name=task_name,
            title=title,
            description=description,
            context=context or {},
            sla_seconds=sla_seconds,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        self._conn.execute(
            "INSERT INTO human_tickets (ticket_id, run_id, task_name, title, description, context, status, sla_seconds, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (ticket.ticket_id, run_id, task_name, title, description, json.dumps(ticket.context, ensure_ascii=False), ticket.status, sla_seconds, ticket.created_at),
        )
        self._conn.commit()
        return ticket

    def resolve_ticket(self, ticket_id: str, resolution: str, approved: bool = True) -> None:
        now = datetime.now(timezone.utc).isoformat()
        status = "resolved" if approved else "rejected"
        self._conn.execute(
            "UPDATE human_tickets SET status = ?, resolved_at = ?, resolution = ? WHERE ticket_id = ?",
            (status, now, resolution, ticket_id),
        )
        self._conn.commit()

    def get_pending_tickets(self) -> list[HumanTicket]:
        rows = self._conn.execute(
            "SELECT * FROM human_tickets WHERE status = 'pending' ORDER BY created_at ASC"
        ).fetchall()
        return [self._row_to_ticket(r) for r in rows]

    def get_ticket(self, ticket_id: str) -> HumanTicket | None:
        row = self._conn.execute(
            "SELECT * FROM human_tickets WHERE ticket_id = ?", (ticket_id,)
        ).fetchone()
        if row is None:
            return None
        return self._row_to_ticket(row)

    def check_timeouts(self) -> list[HumanTicket]:
        """检查超时工单"""
        now = datetime.now(timezone.utc)
        rows = self._conn.execute(
            "SELECT * FROM human_tickets WHERE status = 'pending'"
        ).fetchall()
        timed_out: list[HumanTicket] = []
        for r in rows:
            ticket = self._row_to_ticket(r)
            created = datetime.fromisoformat(ticket.created_at)
            if (now - created).total_seconds() > ticket.sla_seconds:
                ticket.status = "timed_out"
                self._conn.execute(
                    "UPDATE human_tickets SET status = 'timed_out' WHERE ticket_id = ?",
                    (ticket.ticket_id,),
                )
                timed_out.append(ticket)
        self._conn.commit()
        return timed_out

    @staticmethod
    def _row_to_ticket(row: Any) -> HumanTicket:
        return HumanTicket(
            ticket_id=row[0], run_id=row[1], task_name=row[2],
            title=row[3], description=row[4],
            context=json.loads(row[5]) if isinstance(row[5], str) else row[5],
            status=row[6], sla_seconds=row[7],
            created_at=row[8], resolved_at=row[9],
            resolution=row[10], assigned_to=row[11],
        )

    def close(self) -> None:
        self._conn.close()


# ===== 超时默认策略 =====


class TimeoutDefaultStrategy:
    """超时默认策略"""

    @staticmethod
    def apply(ticket: HumanTicket, sla_policy: SLAPolicy | None = None) -> dict[str, Any]:
        """应用超时默认策略"""
        policy = sla_policy or SLAPolicy()

        if policy.default_action == "skip":
            return {
                "action": "skip",
                "resolution": "超时自动跳过",
                "approved": False,
            }
        elif policy.default_action == "degrade":
            return {
                "action": "degrade",
                "resolution": "超时降级处理",
                "approved": True,
            }
        else:  # fail
            return {
                "action": "fail",
                "resolution": "超时标记失败",
                "approved": False,
            }


# ===== HUMAN Task Executor 骨架 =====


class HumanTaskExecutor:
    """HUMAN Task 执行器：创建工单 → 等待解决 → 超时降级"""

    kind = TaskKind.HUMAN

    def __init__(self, ticket_manager: HumanTicketManager) -> None:
        self._ticket_manager = ticket_manager

    async def execute(self, run: TaskRun) -> TaskRun:
        input_data = run.output
        title = input_data.get("title", "需要人工介入")
        description = input_data.get("description", "")
        context = input_data.get("context", {})

        ticket = self._ticket_manager.create_ticket(
            run_id=run.run_id,
            task_name=run.spec.name,
            title=title,
            description=description,
            context=context,
        )

        # 检查超时
        timeouts = self._ticket_manager.check_timeouts()
        for t in timeouts:
            if t.ticket_id == ticket.ticket_id:
                strategy = TimeoutDefaultStrategy.apply(t)
                run.output = strategy
                run.status = TaskStatus.DEGRADED
                return run

        # 工单未解决 → 返回等待状态
        run.output = {
            "ticket_id": ticket.ticket_id,
            "status": "pending",
            "message": "等待人工处理",
        }
        run.status = TaskStatus.RUNNING
        return run