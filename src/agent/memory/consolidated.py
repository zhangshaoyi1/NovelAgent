"""整合记忆（ConsolidatedMemory，Phase 2）

把**语义记忆（长期事实）+ 会话记忆（近期决策）+ 当前设定集**整合为一个
"当前权威快照"（Book Bible）：最新确立的事实、活跃剧情线、未结债务（open debts）、
角色状态、质量目标、已整合到第几章。供 Planner 修订计划、Writer/Editor 取最新上下文。

与单纯的事实堆不同，"整合"强调**去重 + 取最新 + 结构化**：例如同一角色的多条记忆
合并为一条角色状态；已回收的伏笔从"未结债务"移除。

持久化：``<project>/.state/memory/consolidated.json``。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any


def _merge_unique(base: list[Any], extra: list[Any] | None) -> list[Any]:
    """保留 base 顺序并追加 extra 中未出现的项。"""
    out = list(base)
    for item in extra or []:
        if item not in out:
            out.append(item)
    return out


class ConsolidatedMemory:
    """整合记忆（Book Bible 快照，JSON 持久化）"""

    def __init__(self, project_dir: Path | str | None = None) -> None:
        self.project_dir = Path(project_dir) if project_dir else None
        self._data: dict[str, Any] = {}
        self._file = (
            self.project_dir / ".state" / "memory" / "consolidated.json"
            if self.project_dir
            else None
        )
        self._load()

    def _load(self) -> None:
        if not self._file or not self._file.exists():
            return
        try:
            self._data = json.loads(self._file.read_text(encoding="utf-8")) or {}
        except (json.JSONDecodeError, OSError):
            self._data = {}

    def _persist(self) -> None:
        if not self._file:
            return
        try:
            self._file.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._file.with_suffix(".json.tmp")
            tmp.write_text(
                json.dumps(self._data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            tmp.replace(self._file)
        except OSError:
            pass

    def get(self, section: str, default: Any = None) -> Any:
        return self._data.get(section, default)

    def set_section(self, section: str, value: Any) -> None:
        self._data[section] = value
        self.touch()

    def touch(self) -> None:
        self._data["updated_at"] = time.time()
        self._persist()

    def update(
        self,
        facts: list[str] | None = None,
        plot_threads: list[str] | None = None,
        open_debts: list[str] | None = None,
        characters: list[dict[str, Any]] | None = None,
        quality_targets: dict[str, Any] | None = None,
        last_consolidated_chapter: int | None = None,
    ) -> None:
        """增量更新整合记忆（合并去重）。"""
        if facts:
            self._data["facts"] = _merge_unique(self._data.get("facts", []), facts)
        if plot_threads:
            self._data["plot_threads"] = _merge_unique(
                self._data.get("plot_threads", []), plot_threads
            )
        if open_debts:
            self._data["open_debts"] = _merge_unique(
                self._data.get("open_debts", []), open_debts
            )
        if characters:
            self._data["characters"] = _merge_unique(
                self._data.get("characters", []), characters
            )
        if quality_targets:
            merged = dict(self._data.get("quality_targets", {}) or {})
            merged.update(quality_targets)
            self._data["quality_targets"] = merged
        if last_consolidated_chapter is not None:
            self._data["last_consolidated_chapter"] = max(
                int(self._data.get("last_consolidated_chapter", 0) or 0),
                int(last_consolidated_chapter),
            )
        self.touch()

    def remove_open_debt(self, debt: str) -> None:
        """债务结清（如伏笔已回收）时移除。"""
        debts = self._data.get("open_debts", [])
        if len(debts):
            self._data["open_debts"] = [d for d in debts if d != debt]
            self.touch()

    @property
    def last_consolidated_chapter(self) -> int:
        return int(self._data.get("last_consolidated_chapter", 0) or 0)

    def to_dict(self) -> dict[str, Any]:
        return dict(self._data)

    def build_from(
        self,
        semantic_facts: list[str] | None = None,
        plot_threads: list[str] | None = None,
        open_debts: list[str] | None = None,
        characters: list[dict[str, Any]] | None = None,
        quality_targets: dict[str, Any] | None = None,
        last_consolidated_chapter: int | None = None,
    ) -> None:
        """从各来源一次性整合（覆盖式重建快照）。"""
        self._data = {
            "facts": list(semantic_facts or []),
            "plot_threads": list(plot_threads or []),
            "open_debts": list(open_debts or []),
            "characters": list(characters or []),
            "quality_targets": dict(quality_targets or {}),
            "last_consolidated_chapter": int(last_consolidated_chapter or 0),
        }
        self.touch()
