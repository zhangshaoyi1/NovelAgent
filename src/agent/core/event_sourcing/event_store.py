"""事件存储 Provider——事件持久化"""

from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import ClassVar, Optional

from agent.core.event_sourcing.event_model import Event, Snapshot


class EventStoreProvider(ABC):
    """事件存储 Provider 抽象基类"""

    @abstractmethod
    def append(self, event: Event) -> None:
        """持久化事件"""
        ...

    @abstractmethod
    def replay(self, correlation_id: str) -> list[Event]:
        """按 correlation_id 重放事件"""
        ...

    @abstractmethod
    def query(
        self,
        event_type: Optional[str] = None,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
        limit: int = 100,
    ) -> list[Event]:
        """按条件查询事件"""
        ...

    @abstractmethod
    def save_snapshot(self, snapshot: Snapshot) -> None:
        """保存快照"""
        ...

    @abstractmethod
    def load_latest_snapshot(self, correlation_id: str) -> Optional[Snapshot]:
        """加载最近快照"""
        ...

    @abstractmethod
    def list_snapshots(self, correlation_id: str, limit: int = 3) -> list[Snapshot]:
        """列出快照（最近优先）"""
        ...


class FileEventStore(EventStoreProvider):
    """基于文件的事件存储——默认实现，事件落盘到小说目录 .events/"""

    MAX_SNAPSHOTS: ClassVar[int] = 3

    def __init__(self, project_dir: str | Path) -> None:
        self.project_dir = Path(project_dir)
        self._events_dir = self.project_dir / ".events"
        self._events_dir.mkdir(parents=True, exist_ok=True)
        self._event_file = self._events_dir / "events.jsonl"
        self._snapshot_dir = self._events_dir / "snapshots"
        self._snapshot_dir.mkdir(parents=True, exist_ok=True)

    def append(self, event: Event) -> None:
        try:
            with open(self._event_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")
        except OSError:
            pass  # 降级不阻断

    def replay(self, correlation_id: str) -> list[Event]:
        events: list[Event] = []
        try:
            if not self._event_file.exists():
                return events
            with open(self._event_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = Event.from_dict(json.loads(line))
                        if event.correlation_id == correlation_id:
                            events.append(event)
                    except (json.JSONDecodeError, KeyError):
                        continue
        except OSError:
            pass
        return events

    def query(
        self,
        event_type: Optional[str] = None,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
        limit: int = 100,
    ) -> list[Event]:
        events: list[Event] = []
        try:
            if not self._event_file.exists():
                return events
            with open(self._event_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = Event.from_dict(json.loads(line))
                        if event_type and event.type != event_type:
                            continue
                        if since and event.timestamp < since:
                            continue
                        if until and event.timestamp > until:
                            continue
                        events.append(event)
                        if len(events) >= limit:
                            break
                    except (json.JSONDecodeError, KeyError):
                        continue
        except OSError:
            pass
        return events

    def save_snapshot(self, snapshot: Snapshot) -> None:
        try:
            snapshot_file = (
                self._snapshot_dir
                / f"snapshot_{snapshot.correlation_id}_{snapshot.id}.json"
            )
            with open(snapshot_file, "w", encoding="utf-8") as f:
                json.dump(snapshot.to_dict(), f, ensure_ascii=False, indent=2)
            self._cleanup_old_snapshots(snapshot.correlation_id)
        except OSError:
            pass

    def load_latest_snapshot(self, correlation_id: str) -> Optional[Snapshot]:
        snapshots = self.list_snapshots(correlation_id, limit=1)
        return snapshots[0] if snapshots else None

    def list_snapshots(self, correlation_id: str, limit: int = 3) -> list[Snapshot]:
        snapshots: list[Snapshot] = []
        try:
            if not self._snapshot_dir.exists():
                return snapshots
            pattern = f"snapshot_{correlation_id}_*.json"
            files = sorted(
                self._snapshot_dir.glob(pattern),
                key=os.path.getmtime,
                reverse=True,
            )
            for f in files[:limit]:
                try:
                    with open(f, "r", encoding="utf-8") as sf:
                        snapshots.append(Snapshot.from_dict(json.load(sf)))
                except (json.JSONDecodeError, OSError):
                    continue
        except OSError:
            pass
        return snapshots

    def _cleanup_old_snapshots(self, correlation_id: str) -> None:
        """清理旧快照，只保留最近 MAX_SNAPSHOTS 个"""
        snapshots = self.list_snapshots(correlation_id, limit=100)
        if len(snapshots) > self.MAX_SNAPSHOTS:
            for old in snapshots[self.MAX_SNAPSHOTS :]:
                old_file = (
                    self._snapshot_dir
                    / f"snapshot_{correlation_id}_{old.id}.json"
                )
                try:
                    old_file.unlink(missing_ok=True)
                except OSError:
                    pass


class EventStoreRegistry:
    """事件存储注册表——支持扩展存储方式"""

    def __init__(self) -> None:
        self._providers: dict[str, EventStoreProvider] = {}

    def register(self, name: str, provider: EventStoreProvider) -> None:
        self._providers[name] = provider

    def get(self, name: str) -> EventStoreProvider:
        if name not in self._providers:
            raise KeyError(f"EventStoreProvider '{name}' not registered")
        return self._providers[name]

    def all(self) -> list[EventStoreProvider]:
        return list(self._providers.values())

    def default(self) -> Optional[EventStoreProvider]:
        """返回第一个注册的 Provider"""
        if self._providers:
            return next(iter(self._providers.values()))
        return None