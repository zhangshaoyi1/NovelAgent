"""连续性账本 · 确定性推导（G15 P0-1）

开放剧情线（open_loop）状态与「逾期」判定全部用纯函数推导，不依赖 LLM 主观断言，
落实项目「确定性优先」哲学。
"""

from __future__ import annotations

from agent.core.continuity.models import ContinuityOpenLoop, LoopStatus


def derive_open_loop_status(
    loop: ContinuityOpenLoop,
    current_chapter: int,
) -> LoopStatus:
    """纯函数派生 open_loop 状态。

    规则（可预测、可测试）：
    - 已在 resolved_at 显式闭环 → ``resolved``；
    - 已废弃 → ``abandoned``；
    - 明确标注预计闭环章（expected_resolve_chapter，存于 detail 前缀 `resolve_after:`）
      且当前章已越过 → 提示 un 闭环（仍返回 ``open``，但由调用方读逾期标记）；
    - 否则维持现状。
    本函数只负责状态本身，逾期信息由调用方依 ``resolve_after`` 判定。
    """
    if loop.status == "resolved" or loop.resolved_in:
        return "resolved"
    if loop.status == "abandoned":
        return "abandoned"
    return loop.status


def expected_resolve_chapter(loop: ContinuityOpenLoop) -> int | None:
    """从 Detail 提取预计回收章（无则 None）。

    约定 ``detail`` 含 ``resolve_after:NNN`` 片段；
    同时兼容从 ``resolved_in``（闭环位置）读取。
    """
    if loop.resolved_in:
        for tok in loop.resolved_in.replace("，", ",").replace(",", " ").split():
            if tok.isdigit():
                return int(tok)
    text = loop.detail
    marker = "resolve_after:"
    if marker in text:
        tail = text.split(marker, 1)[1].split()[0].strip(",：，:")
        if tail.isdigit():
            return int(tail)
    return None


def is_overdue(loop: ContinuityOpenLoop, current_chapter: int) -> bool:
    """是否逾期（已到预计回收章但未闭环）。"""
    if loop.status in ("resolved", "abandoned") or loop.resolved_in:
        return False
    expected = expected_resolve_chapter(loop)
    if expected is None:
        return False
    return current_chapter > expected


__all__ = [
    "derive_open_loop_status",
    "expected_resolve_chapter",
    "is_overdue",
]