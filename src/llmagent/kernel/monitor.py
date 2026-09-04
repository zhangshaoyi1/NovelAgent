"""Monitor：监控门面（心跳/预算/打转检测）"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class MonitorEvent:
    """监控事件"""

    run_id: str
    type: str  # started / ended / heartbeat / budget_warn / budget_melt / loop_detected
    payload: dict[str, Any] = field(default_factory=dict)


class Heartbeater:
    """心跳探活"""

    def __init__(self, timeout_s: float = 120.0) -> None:
        self._timeout_s = timeout_s
        self._beats: dict[str, float] = {}

    def start(self, run_id: str, timeout_s: float | None = None) -> None:
        self._beats[run_id] = time.monotonic()

    def beat(self, run_id: str) -> None:
        self._beats[run_id] = time.monotonic()

    def is_alive(self, run_id: str) -> bool:
        last = self._beats.get(run_id)
        if last is None:
            return False
        return (time.monotonic() - last) < self._timeout_s


class BudgetWatcher:
    """预算监控"""

    def __init__(self) -> None:
        self._watchers: dict[str, float] = {}

    def watch(self, ledger_ref: str, total_cents: float = 100.0) -> None:
        self._watchers[ledger_ref] = total_cents

    def check(self, ledger_ref: str, used_cents: float) -> str:
        """检查预算状态，返回 '' / 'warn' / 'melt'"""
        total = self._watchers.get(ledger_ref, 100.0)
        ratio = used_cents / total if total > 0 else 1.0
        if ratio >= 1.0:
            return "melt"
        if ratio >= 0.8:
            return "warn"
        return ""


class LoopDetector:
    """打转检测：记录 (tool_name, args_hash, output_hash) 重复→REPLAN"""

    def __init__(self, max_duplicates: int = 3) -> None:
        self._max_duplicates = max_duplicates
        self._history: dict[str, list[str]] = {}

    def observe(self, action_fingerprint: str, run_id: str = "") -> bool:
        """记录动作指纹；返回 True 表示检测到重复环"""
        key = run_id or "_default"
        if key not in self._history:
            self._history[key] = []
        self._history[key].append(action_fingerprint)
        # 检测最近 N 次是否相同
        recent = self._history[key][-self._max_duplicates :]
        if len(recent) >= self._max_duplicates and len(set(recent)) == 1:
            return True
        return False


class Monitor:
    """监控门面：只发信号、不决断"""

    def __init__(
        self,
        heartbeater: Heartbeater | None = None,
        budget_watcher: BudgetWatcher | None = None,
        loop_detector: LoopDetector | None = None,
    ) -> None:
        self.heartbeater = heartbeater or Heartbeater()
        self.budget_watcher = budget_watcher or BudgetWatcher()
        self.loop_detector = loop_detector or LoopDetector()

    def signal(self, run_id: str, event: MonitorEvent) -> None:
        """Kernel 在状态转移点调用"""
        if event.type == "started":
            self.heartbeater.start(run_id)
        elif event.type == "heartbeat":
            self.heartbeater.beat(run_id)

    def tick(self, run_id: str) -> None:
        self.heartbeater.beat(run_id)