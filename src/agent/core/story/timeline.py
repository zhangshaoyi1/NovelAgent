"""时序与叙事分层 · Timeline（G15 P0-3）

对标 DeepWrite `plot.ts`：把「真实时间线」与「阅读顺序/披露层级」解耦记录。

- ``StoryEvent``：真实时间线上的一个剧情事件（time_mode + event_connections）。
- ``NarrativePlacement``：某事件在某一章「如何被表达」（叙事模式 + 披露层级），
  唯一锚定 ``(event_id, chapter)``。

不变式（入口 validator + 本模块聚合校验）：
- story event 自引用 / 关系环（自环）被拒；
- placement 的 ``(event_id, chapter)`` 唯一；
- scene（本体场景）不得用 ``false`` 披露。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, field_validator, model_validator

EventTimeMode = Literal["exact", "relative", "sequence", "unknown"]
ConnType = Literal["before", "same_time", "overlaps", "causes", "enables", "conceals"]
NarrMode = Literal[
    "scene", "flashback", "retelling", "clue",
    "misdirection", "reveal", "dream", "prophecy",
]
Disclosure = Literal["hint", "partial", "full", "false"]


class StoryEvent(BaseModel):
    """真实时间线上的事件。"""

    event_id: str
    title: str
    time_mode: EventTimeMode = "sequence"
    story_order: int | None = None          # 真实时间线内顺序
    connections: list[dict[str, str]] = []  # [{"to": event_id, "kind": ConnType}]

    @field_validator("event_id", "title")
    @classmethod
    def _nonempty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("不可为空")
        return v.strip()

    @model_validator(mode="after")
    def _conn_shape(self) -> "StoryEvent":
        for c in self.connections:
            to = c.get("to", "")
            if not to or to == self.event_id:
                raise ValueError("connection 不能自引用或为空")
            if c.get("kind") not in ("before", "same_time", "overlaps",
                                     "causes", "enables", "conceals"):
                raise ValueError(f"非法连接类型: {c.get('kind')}")
        return self


class NarrativePlacement(BaseModel):
    """某事件在某一章的叙事表达。"""

    event_id: str
    chapter: int
    mode: NarrMode = "scene"
    disclosure: Disclosure = "full"

    @model_validator(mode="after")
    def _rules(self) -> "NarrativePlacement":
        if self.chapter <= 0:
            raise ValueError("chapter 必须为正整数")
        # 本体场景（scene）必须真实：不得用 false 披露
        if self.mode == "scene" and self.disclosure == "false":
            raise ValueError("scene 场景披露不能为 false")
        # 纯虚构叙事（dream/prophecy/retelling 且 false 披露）允许 —— 即主体验真实但呈现为非事实
        return self


class Timeline(BaseModel):
    """时序 + 叙事双层。"""

    schema_version: int = 1
    events: list[StoryEvent] = []
    placements: list[NarrativePlacement] = []

    @model_validator(mode="after")
    def _refs_and_unique(self) -> "Timeline":
        event_ids = {e.event_id for e in self.events}
        for e in self.events:
            for c in e.connections:
                if c.get("to") not in event_ids:
                    raise ValueError(f"event 引用不存在: {c.get('to')}")
        seen = set()
        for p in self.placements:
            key = (p.event_id, p.chapter)
            if key in seen:
                raise ValueError(f"placement 重复: {key}")
            seen.add(key)
            if p.event_id not in event_ids:
                raise ValueError(f"placement 引用 event 不存在: {p.event_id}")
        return self


def find_event(timeline: Timeline, event_id: str) -> StoryEvent | None:
    return next((e for e in timeline.events if e.event_id == event_id), None)


def placements_for_chapter(
    timeline: Timeline, chapter: int
) -> list[NarrativePlacement]:
    """某章所有叙事放置（写手上下文注入用，有界）。"""
    return sorted(
        (p for p in timeline.placements if p.chapter == chapter),
        key=lambda p: p.chapter,
    )


__all__ = [
    "EventTimeMode",
    "ConnType",
    "NarrMode",
    "Disclosure",
    "StoryEvent",
    "NarrativePlacement",
    "Timeline",
    "find_event",
    "placements_for_chapter",
]