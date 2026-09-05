"""最简记忆包（竞品差距改进计划 P0-4，对齐 oh-story ``state-tracking`` 判据）。

判据（一条可执行的裁剪标准，代码与提示词双处生效）：
**只保留"如果不知道这个、本章就会写错"的信息**，其余一概不进写章上下文。

三类保留（``KEEP_CATEGORIES``）：
- ``current_state``    角色当前状态（生死/位置/关系/能力现状）
- ``causal_history``   历史因果（导致本章冲突的已发生事件、既有恩怨、上章交接）
- ``world_constraint`` 世界硬约束（冻结设定/境界体系/金手指边界/信息差规则）

配套记忆压缩规则（对齐 oh-story 角色状态快照）：
``prune_recent_changes`` —— 每角色状态变更最多保留最近
``MAX_RECENT_CHANGES`` 条，超出部分**合并为一条摘要行**（不丢时间序，
只折叠细节），防止长篇后期变更流水撑爆上下文。

本模块是纯函数层：不读盘、不触网、不依赖 LLM；供 ``MemoryLayer`` 取用侧
与整合记忆（``ConsolidatedMemory``）写入侧共同使用。
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

# 三类保留（其余类别默认不进最简记忆包）
CURRENT_STATE = "current_state"
CAUSAL_HISTORY = "causal_history"
WORLD_CONSTRAINT = "world_constraint"
KEEP_CATEGORIES = frozenset({CURRENT_STATE, CAUSAL_HISTORY, WORLD_CONSTRAINT})

# 每角色状态变更保留上限（超出合并为摘要行）
MAX_RECENT_CHANGES = 10

# 语义记忆 type → 最简记忆包类别的默认映射。
# 未命中映射的类型（如 brainstorm/feedback 等非权威信息）返回 ""，即不进包。
_CATEGORY_BY_TYPE: dict[str, str] = {
    # 当前状态类
    "character": CURRENT_STATE,
    "character_state": CURRENT_STATE,
    "state": CURRENT_STATE,
    "relation": CURRENT_STATE,
    # 历史因果类
    "fact": CAUSAL_HISTORY,
    "chapter_fact": CAUSAL_HISTORY,
    "event": CAUSAL_HISTORY,
    "handoff": CAUSAL_HISTORY,
    "foreshadow": CAUSAL_HISTORY,
    # 世界硬约束类
    "setting": WORLD_CONSTRAINT,
    "world": WORLD_CONSTRAINT,
    "constraint": WORLD_CONSTRAINT,
    "golden_finger": WORLD_CONSTRAINT,
    "realm": WORLD_CONSTRAINT,
}


def classify_memory_type(type: str) -> str:
    """语义记忆 type → 最简记忆包类别；不属三类权威信息的返回 ``""``。"""
    t = (type or "").strip().lower()
    if t in _CATEGORY_BY_TYPE:
        return _CATEGORY_BY_TYPE[t]
    # 容错：中文/组合类型按关键词归类（如 "角色状态"、"世界设定"）
    if "状态" in t or "关系" in t:
        return CURRENT_STATE
    if "事实" in t or "因果" in t or "伏笔" in t:
        return CAUSAL_HISTORY
    if "设定" in t or "约束" in t or "体系" in t:
        return WORLD_CONSTRAINT
    return ""


def minimal_memory_pack(
    items: Iterable[Any],
    *,
    type_of: Callable[[Any], str] = lambda it: getattr(it, "type", "") or "",
    keep_categories: frozenset[str] | set[str] = KEEP_CATEGORIES,
) -> list[Any]:
    """按最简记忆包判据过滤条目：仅保留三类"不知道就会写错"的权威信息。

    Args:
        items: 待过滤条目（``MemoryEntry`` / 任意对象 / dict 均可，经 ``type_of`` 取类型）。
        type_of: 从条目取语义类型的函数（默认取 ``.type`` 属性）。
        keep_categories: 保留类别集合（默认三类全保留）。

    Returns:
        保持原序的过滤结果；调用方再按自身需要截断 top_k。
    """
    return [
        it for it in items if classify_memory_type(type_of(it)) in keep_categories
    ]


def prune_recent_changes(
    changes: list[Any],
    max_recent: int = MAX_RECENT_CHANGES,
    *,
    summary_width: int = 60,
) -> list[Any]:
    """角色状态变更条数上限：保留最近 ``max_recent`` 条，超出合并为一条摘要行。

    - 条数不超限时原样返回（不复制不重排）；
    - 超限时返回 ``[摘要行] + 最近 max_recent 条``，摘要行形如
      ``（更早 N 条变更已合并：条A；条B；条C…）``，保持时间序可读。
    """
    if len(changes) <= max_recent:
        return changes
    overflow = changes[:-max_recent]
    recent = changes[-max_recent:]
    head = "；".join(str(c)[:summary_width] for c in overflow[:3])
    more = f"…（另 {len(overflow) - 3} 条从略）" if len(overflow) > 3 else ""
    summary = f"（更早 {len(overflow)} 条变更已合并：{head}{more}）"
    return [summary, *recent]
