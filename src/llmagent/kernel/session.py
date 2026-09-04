"""Session 聚合根（M3.1）：会话管理 + 三层 Context + 七统一归属抬升"""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from .task import TaskRun, TaskSpec


# ===== 数据模型 =====


@dataclass
class SessionContext:
    """Session 层上下文：全局配置 + 用户偏好"""
    session_id: str
    user_id: str = ""
    project: str = ""
    global_config: dict[str, Any] = field(default_factory=dict)
    tags: dict[str, str] = field(default_factory=dict)


@dataclass
class TaskContext:
    """Task 层上下文：当前 Task 的输入输出"""
    task_spec: TaskSpec | None = None
    task_input: dict[str, Any] = field(default_factory=dict)
    task_output: dict[str, Any] = field(default_factory=dict)
    task_tags: dict[str, str] = field(default_factory=dict)


@dataclass
class ChatContext:
    """Chat 层上下文：对话历史 + 当前轮次"""
    messages: list[dict[str, str]] = field(default_factory=list)
    turn_count: int = 0
    current_turn: int = 0


@dataclass
class DialogueTurn:
    """对话轮次"""
    turn_id: int
    role: str  # user / assistant / tool
    content: str
    timestamp: str = ""


class SessionState:
    """Session 状态常量"""
    OPEN = "open"
    SUBMITTED = "submitted"
    CLOSED = "closed"
    CANCELLED = "cancelled"


class Session:
    """Session 聚合根

    贯穿一次完整的对话/任务生命周期。
    """

    def __init__(
        self,
        session_id: str = "",
        user_id: str = "",
        project: str = "",
    ) -> None:
        self.session_id = session_id or f"sess-{uuid.uuid4().hex[:12]}"
        self.state = SessionState.OPEN
        self.created_at = datetime.now(timezone.utc).isoformat()
        self.updated_at = self.created_at
        self.closed_at: str = ""

        # 三层 context
        self.session_ctx = SessionContext(
            session_id=self.session_id,
            user_id=user_id,
            project=project,
        )
        self.task_ctx = TaskContext()
        self.chat_ctx = ChatContext()

        # 对话历史
        self.dialogue_turns: list[DialogueTurn] = []
        self.input_queue: list[str] = []

        # 七统一归属
        self.trace_id: str = ""
        self.budget_ref: str = ""
        self.event_bus: Any = None
        self.artifact_store: Any = None
        self.monitor: Any = None
        self.metrics: Any = None
        self.validator: Any = None
        self.failure_handler: Any = None

    def add_turn(self, role: str, content: str) -> DialogueTurn:
        turn = DialogueTurn(
            turn_id=len(self.dialogue_turns) + 1,
            role=role,
            content=content,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        self.dialogue_turns.append(turn)
        self.chat_ctx.turn_count = len(self.dialogue_turns)
        self.chat_ctx.current_turn = turn.turn_id
        self.chat_ctx.messages.append({"role": role, "content": content})
        self.updated_at = datetime.now(timezone.utc).isoformat()
        return turn

    def get_last_turn(self) -> DialogueTurn | None:
        return self.dialogue_turns[-1] if self.dialogue_turns else None

    def close(self) -> None:
        self.state = SessionState.CLOSED
        self.closed_at = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "state": self.state,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "closed_at": self.closed_at,
            "user_id": self.session_ctx.user_id,
            "project": self.session_ctx.project,
            "turn_count": len(self.dialogue_turns),
            "trace_id": self.trace_id,
            "budget_ref": self.budget_ref,
        }


# ===== SessionManager =====


class SessionManager:
    """Session 管理器门面：open() / submit() / close()"""

    def __init__(self, db_path: str | Path = "") -> None:
        self._db_path = str(db_path) if db_path else ":memory:"
        self._conn = sqlite3.connect(self._db_path)
        self._sessions: dict[str, Session] = {}
        self._init_db()

    def _init_db(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                state TEXT NOT NULL,
                user_id TEXT NOT NULL DEFAULT '',
                project TEXT NOT NULL DEFAULT '',
                trace_id TEXT NOT NULL DEFAULT '',
                budget_ref TEXT NOT NULL DEFAULT '',
                turn_count INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                closed_at TEXT NOT NULL DEFAULT ''
            )
            """
        )
        self._conn.commit()

    def open(self, user_id: str = "", project: str = "") -> Session:
        """打开新 Session"""
        session = Session(user_id=user_id, project=project)
        self._sessions[session.session_id] = session
        self._persist(session)
        return session

    def get(self, session_id: str) -> Session | None:
        """获取 Session"""
        if session_id in self._sessions:
            return self._sessions[session_id]
        return self._load(session_id)

    def submit(self, session: Session, input_data: str = "") -> None:
        """提交用户输入到 Session"""
        if session.state == SessionState.CLOSED:
            raise RuntimeError(f"Session {session.session_id} 已关闭")
        if input_data:
            session.add_turn("user", input_data)
        session.state = SessionState.SUBMITTED
        session.updated_at = datetime.now(timezone.utc).isoformat()
        self._persist(session)

    def close(self, session: Session) -> None:
        """关闭 Session"""
        session.close()
        self._persist(session)

    def list_active(self) -> list[Session]:
        """列出活跃 Session"""
        rows = self._conn.execute(
            "SELECT session_id FROM sessions WHERE state = 'open' OR state = 'submitted' ORDER BY updated_at DESC"
        ).fetchall()
        result: list[Session] = []
        for (sid,) in rows:
            if sid in self._sessions:
                result.append(self._sessions[sid])
            else:
                loaded = self._load(sid)
                if loaded:
                    result.append(loaded)
        return result

    def _persist(self, session: Session) -> None:
        data = session.to_dict()
        self._conn.execute(
            "INSERT OR REPLACE INTO sessions (session_id, state, user_id, project, trace_id, budget_ref, turn_count, created_at, updated_at, closed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                data["session_id"], data["state"], data["user_id"],
                data["project"], data["trace_id"], data["budget_ref"],
                data["turn_count"], data["created_at"],
                data["updated_at"], data["closed_at"],
            ),
        )
        self._conn.commit()

    def _load(self, session_id: str) -> Session | None:
        row = self._conn.execute(
            "SELECT * FROM sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        if row is None:
            return None
        session = Session(session_id=row[0], user_id=row[2], project=row[3])
        session.state = row[1]
        session.trace_id = row[4]
        session.budget_ref = row[5]
        session.created_at = row[7]
        session.updated_at = row[8]
        session.closed_at = row[9]
        self._sessions[session_id] = session
        return session

    def close_all(self) -> None:
        self._sessions.clear()
        self._conn.close()


# ===== SessionGate =====


class SessionGate:
    """Session 门禁：校验 Session 状态"""

    @staticmethod
    def validate(session: Session) -> None:
        if session.state == SessionState.CLOSED:
            raise RuntimeError(f"Session {session.session_id} 已关闭，不可提交")
        if session.state == SessionState.CANCELLED:
            raise RuntimeError(f"Session {session.session_id} 已取消")


# ===== ContextBuilder =====


class ContextBuilder:
    """上下文构建器：组装三层 Context"""

    @staticmethod
    def build(
        session: Session,
        task_spec: TaskSpec | None = None,
        task_input: dict[str, Any] | None = None,
    ) -> Session:
        """构建三层上下文"""
        if task_spec:
            session.task_ctx.task_spec = task_spec
        if task_input:
            session.task_ctx.task_input = task_input
        return session

    @staticmethod
    def build_system_prompt(session: Session, extra_context: str = "") -> str:
        """构建系统 Prompt（含 Session 上下文）"""
        parts = [
            f"Session ID: {session.session_id}",
            f"User: {session.session_ctx.user_id}",
            f"Project: {session.session_ctx.project}",
        ]
        if session.task_ctx.task_spec:
            parts.append(f"Task: {session.task_ctx.task_spec.name}")
        if extra_context:
            parts.append(extra_context)
        return "\n".join(parts)


# ===== InputQueue =====


class InputQueue:
    """输入队列：管理用户输入"""

    def __init__(self, session: Session) -> None:
        self._session = session

    def enqueue(self, message: str) -> None:
        self._session.input_queue.append(message)

    def dequeue(self) -> str | None:
        if not self._session.input_queue:
            return None
        return self._session.input_queue.pop(0)

    def peek(self) -> str | None:
        if not self._session.input_queue:
            return None
        return self._session.input_queue[0]

    def size(self) -> int:
        return len(self._session.input_queue)

    def clear(self) -> None:
        self._session.input_queue.clear()


# ===== DialogueInterpreter =====


class DialogueInterpreter:
    """对话解释器：解析用户输入 + 生成回复"""

    @staticmethod
    def interpret(session: Session, user_input: str) -> dict[str, Any]:
        """解析用户输入，提取意图"""
        intent: str = "unknown"
        if user_input.startswith("/"):
            intent = "command"
        elif "?" in user_input or "？" in user_input:
            intent = "question"
        elif "写" in user_input or "生成" in user_input:
            intent = "generate"
        elif "评审" in user_input or "review" in user_input.lower():
            intent = "review"
        elif "分析" in user_input or "analyze" in user_input.lower():
            intent = "analyze"

        return {
            "intent": intent,
            "raw_input": user_input,
            "session_id": session.session_id,
            "turn_id": len(session.dialogue_turns) + 1,
        }

    @staticmethod
    def format_response(session: Session, response: str) -> str:
        """格式化回复"""
        if response:
            session.add_turn("assistant", response)
        return response