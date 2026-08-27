"""长小说监督体系——监督引擎与插件注册表"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from agent.core.event_sourcing.event_bus import EventBus
from agent.core.event_sourcing.event_model import Event, EventType

logger = logging.getLogger(__name__)


@dataclass
class SupervisionIssue:
    """监督问题"""
    dimension: str = ""
    severity: str = "info"  # info / warning / critical
    message: str = ""
    chapter: int = 0
    details: dict = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class SupervisionReport:
    """监督报告"""
    issues: list[SupervisionIssue] = field(default_factory=list)
    healthy: bool = True
    summary: str = ""


class SupervisorPlugin(ABC):
    """监督插件抽象基类"""

    @abstractmethod
    def check(self, project_dir: str) -> list[SupervisionIssue]:
        """执行检查"""
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        """插件名称"""
        ...

    @property
    @abstractmethod
    def check_interval_chapters(self) -> int:
        """检查间隔（章数）"""
        ...


class SupervisorRegistry:
    """监督插件注册表"""

    def __init__(self) -> None:
        self._plugins: dict[str, SupervisorPlugin] = {}

    def register(self, plugin: SupervisorPlugin) -> None:
        self._plugins[plugin.name] = plugin

    def unregister(self, name: str) -> None:
        self._plugins.pop(name, None)

    def get(self, name: str) -> Optional[SupervisorPlugin]:
        return self._plugins.get(name)

    def all(self) -> list[SupervisorPlugin]:
        return list(self._plugins.values())


class SupervisorEngine:
    """监督引擎——管理监督插件的执行"""

    def __init__(self, project_dir: str = "") -> None:
        self.project_dir = project_dir
        self._registry = SupervisorRegistry()
        self._last_check_chapter: dict[str, int] = {}
        self._event_bus = EventBus.get_instance()

    @property
    def registry(self) -> SupervisorRegistry:
        return self._registry

    def set_project_dir(self, project_dir: str) -> None:
        self.project_dir = project_dir

    def check_all(self, current_chapter: int) -> SupervisionReport:
        """执行所有插件的检查"""
        issues: list[SupervisionIssue] = []

        for plugin in self._registry.all():
            # 检查间隔
            last_check = self._last_check_chapter.get(plugin.name, 0)
            if current_chapter - last_check < plugin.check_interval_chapters:
                continue

            try:
                plugin_issues = plugin.check(self.project_dir)
                issues.extend(plugin_issues)
                self._last_check_chapter[plugin.name] = current_chapter

                # 发射监督事件
                for issue in plugin_issues:
                    self._event_bus.emit_event(
                        EventType.SUPERVISOR_ALERT,
                        payload={
                            "dimension": issue.dimension,
                            "severity": issue.severity,
                            "message": issue.message,
                            "chapter": issue.chapter,
                        },
                    )
            except Exception:
                logger.exception("Supervisor plugin '%s' check failed", plugin.name)

        has_critical = any(i.severity == "critical" for i in issues)
        report = SupervisionReport(
            issues=issues,
            healthy=not has_critical,
            summary=self._build_summary(issues),
        )
        return report

    def get_issues_since(self, chapter: int) -> list[SupervisionIssue]:
        """获取自某章以来的所有问题"""
        all_issues: list[SupervisionIssue] = []
        for plugin in self._registry.all():
            try:
                plugin_issues = plugin.check(self.project_dir)
                all_issues.extend(
                    i for i in plugin_issues if i.chapter >= chapter
                )
            except Exception:
                continue
        return all_issues

    def _build_summary(self, issues: list[SupervisionIssue]) -> str:
        if not issues:
            return "监督通过，无问题"
        critical = sum(1 for i in issues if i.severity == "critical")
        warning = sum(1 for i in issues if i.severity == "warning")
        parts = []
        if critical:
            parts.append(f"严重问题 {critical} 个")
        if warning:
            parts.append(f"警告 {warning} 个")
        parts.append(f"信息 {len(issues) - critical - warning} 个")
        return "，".join(parts) if parts else "全部通过"