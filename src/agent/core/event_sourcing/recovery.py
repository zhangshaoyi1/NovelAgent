"""续写重建引擎——基于事件系统恢复写作状态"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from agent.core.event_sourcing.event_bus import EventBus
from agent.core.event_sourcing.event_model import Event, EventType, Snapshot

logger = logging.getLogger(__name__)


@dataclass
class RecoveryReport:
    """续写重建报告"""
    success: bool = False
    correlation_id: str = ""
    last_state: dict = field(default_factory=dict)
    last_progress: dict = field(default_factory=dict)
    setting_changed: bool = False
    event_count: int = 0
    ended_at: Optional[datetime] = None
    summary: str = ""
    errors: list[str] = field(default_factory=list)


class RecoveryEngine:
    """续写重建引擎——通过事件系统恢复写作状态"""

    def __init__(self, project_dir: str | Path) -> None:
        self.project_dir = Path(project_dir)
        self._events_dir = self.project_dir / ".events"
        self._event_bus = EventBus.get_instance()

    def detect_previous_session(self) -> Optional[str]:
        """检测是否存在之前的写作会话，返回 correlation_id"""
        events_file = self._events_dir / "events.jsonl"
        if not events_file.exists():
            return None

        # 读取最后一个带 correlation_id 的事件
        correlation_id: Optional[str] = None
        try:
            import json
            with open(events_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        cid = data.get("correlation_id", "")
                        if cid:
                            correlation_id = cid
                    except json.JSONDecodeError:
                        continue
        except OSError:
            pass
        return correlation_id

    def rebuild(self, correlation_id: Optional[str] = None) -> RecoveryReport:
        """重建写作状态"""
        report = RecoveryReport()

        # 自动检测 correlation_id
        if correlation_id is None:
            correlation_id = self.detect_previous_session()
        if correlation_id is None:
            report.success = False
            report.summary = "未找到之前的写作会话"
            return report

        report.correlation_id = correlation_id

        # 1. 加载最近快照
        try:
            snapshot = self._event_bus.load_snapshot(correlation_id)
        except Exception:
            snapshot = None

        if snapshot is None:
            report.success = False
            report.summary = "未找到可用快照，无法重建"
            return report

        report.last_state = snapshot.state_machine
        report.last_progress = snapshot.progress
        report.event_count = snapshot.event_count
        report.ended_at = snapshot.timestamp

        # 2. 差异检测：对比设定文件 checksum
        try:
            current_checksum = self._compute_setting_checksum()
            report.setting_changed = current_checksum != snapshot.setting_checksum
        except Exception:
            report.setting_changed = True

        # 3. 生成续作简报
        state_name = report.last_state.get("state", "unknown")
        chapter_num = report.last_progress.get("chapter", 0)
        report.summary = (
            f"状态恢复成功：状态={state_name}，已写章节={chapter_num}，"
            f"事件数={report.event_count}，"
            f"设定变更={'是' if report.setting_changed else '否'}"
        )

        report.success = True
        return report

    def _compute_setting_checksum(self) -> str:
        """计算设定文件 checksum"""
        hasher = hashlib.sha256()
        setting_files = [
            self.project_dir / "world.md",
            self.project_dir / "architecture.md",
            self.project_dir / "outline.md",
        ]
        for f in setting_files:
            if f.exists():
                try:
                    hasher.update(f.read_bytes())
                except OSError:
                    continue
        return hasher.hexdigest()

    def create_recovery_snapshot(
        self,
        state_machine: dict,
        progress: dict,
        correlation_id: str,
    ) -> None:
        """创建用于续写恢复的快照"""
        snapshot = Snapshot(
            correlation_id=correlation_id,
            state_machine=state_machine,
            progress=progress,
            setting_checksum=self._compute_setting_checksum(),
        )
        self._event_bus.save_snapshot(snapshot)