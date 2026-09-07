"""进度推导唯一答案函数（复盘根治 P2，2026-09-07）。

背景：``total_written + 1`` 曾散布在 8 个文件 12 处独立推导，「下一章是几」
没有唯一答案——F-8 超章事故（门禁打回重写溢出新章）正是两处推导口径漂移
的产物。本模块把进度类推导收口为纯函数，任何模块需要这些答案时一律引用
这里，禁止再手写 ``total_written + 1``。

设计：
- 纯函数、无 IO（``next_chapter`` / ``ending_trigger``）；
- ``book_total`` 是唯一一处读 plan.json 取总章数的底层实现
  （``plan_consistency.load_plan_total`` 委托至此，行为零改动）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping


def next_chapter(progress: Mapping[str, Any] | None) -> int:
    """「即将写的章号」全系统唯一实现（原 total_written + 1 的 12 处散布收口）。

    progress 缺失 / total_written 缺失或非法时返回 1（从第一章开始写）。
    """
    try:
        written = int((progress or {}).get("total_written", 0) or 0)
    except (TypeError, ValueError):
        return 1
    return written + 1 if written >= 0 else 1


def book_total(project_dir: str | Path) -> int | None:
    """全书设计总章数（plan.json 的 total_chapters，唯一权威读取）。

    缺失 / 损坏 / 非法时返回 None——调用方按自身语义兜底
    （pipeline._book_total 兜底 100，但**禁止**用本轮 --chapters 兜底）。
    """
    f = Path(project_dir) / ".state" / "plan.json"
    if not f.exists():
        return None
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
        total = int(data.get("total_chapters") or 0)
    except Exception:  # noqa: BLE001 - 读失败视为未配置
        return None
    return total if total > 0 else None


def ending_trigger(book_total: int, ending_ratio: float) -> int:
    """结局模式触发章号：第一本应处于结局段的章。

    与历史公式 ``int(book_total * (1 - ratio)) + 1`` 逐值等价
    （整数章号 chapter > book_total*(1-ratio)  ⇔  chapter >= 本函数返回值）。
    例：12 章书 ratio=0.25 → 第 10 章；1200 章书 → 第 901 章。
    """
    return int(book_total * (1 - ending_ratio)) + 1
