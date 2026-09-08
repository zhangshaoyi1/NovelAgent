"""主线规划对齐（R6 分层：从 workflows/pipeline/plan_consistency 下沉到 core）

``align_mainline_to_plan`` 把 .state/mainline.json 对齐到 plan.json 总章数（自动修正派生物）。
原实现位于 workflows/pipeline/plan_consistency.py，被 core/plan_store 调用时违反
R6 分层（core 层不得依赖 workflows）。本模块是纯标准库 + core 内部依赖（book_total），
下沉后 core 与 workflows 均可引用（workflows → core 合法）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _rescale_largest_remainder(share: dict[str, Any], total: int) -> dict[str, int]:
    """等比缩放整数预算到总和 == total（最大余数法，确定性）。"""
    old_sum = sum(int(v) for v in share.values())
    if old_sum <= 0:
        base, rem = divmod(total, max(1, len(share)))
        return {k: base + (1 if i < rem else 0) for i, (k, _) in enumerate(share.items())}
    exact = {k: int(v) * total / old_sum for k, v in share.items()}
    floor = {k: int(v) for k, v in exact.items()}
    rem = total - sum(floor.values())
    # 余数按小数部分从大到小分派（键名做次序兜底，保证确定性）
    order = sorted(exact, key=lambda k: (-(exact[k] - floor[k]), k))
    for k in order[:rem]:
        floor[k] += 1
    return floor


def align_mainline_to_plan(
    project_dir: str | Path, console: Any = None
) -> list[str]:
    """把 .state/mainline.json 对齐到 plan.json 总章数（自动修正派生物，幂等）。

    - ``horizon_chapters`` ≠ plan 总章数 → 钳制为 plan 总章数；
    - ``subline_share`` 总和随 horizon 等比缩放（最大余数法），保证 Σshare == plan 总章数；
      缩放后的 cap 才能让 ``decide_mainline_advance`` 的切线条件在 plan 体量内可达。

    Returns:
        变更说明列表（无变更则为空）。
    """
    from agent.core.progress import book_total

    total = book_total(project_dir)
    if total is None:
        return []
    plan_file = Path(project_dir) / ".state" / "mainline.json"
    if not plan_file.exists():
        return []
    try:
        data = json.loads(plan_file.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return []
    except Exception:  # noqa: BLE001 - 读失败交给 load_plan 的缺省逻辑
        return []

    notes: list[str] = []
    horizon = data.get("horizon_chapters")
    try:
        horizon = int(horizon) if horizon else None
    except (TypeError, ValueError):
        horizon = None  # noqa: SILENT_DEGRADE

    share = data.get("subline_share")
    if not isinstance(share, dict) or not share:
        share = None

    # 均无需对齐：horizon 一致且 Σshare == total（份额缺省时由 load_plan 兜底生成）
    share_sum = 0
    if share:
        try:
            share_sum = sum(int(v) for v in share.values())
        except (TypeError, ValueError):
            share = None
            share_sum = 0  # noqa: SILENT_DEGRADE
    if horizon == total and (share is None or share_sum == total):
        return []

    if horizon != total:
        notes.append(
            f"mainline.json horizon_chapters {horizon} → {total}（对齐 plan.json）"
        )
        data["horizon_chapters"] = total

    if share and share_sum != total:
        new_share = _rescale_largest_remainder(share, total)
        notes.append(
            "mainline.json subline_share 总和 "
            f"{share_sum} → {total}（等比缩放："
            + ", ".join(f"{k}={v}" for k, v in new_share.items())
            + "）"
        )
        data["subline_share"] = new_share

    try:
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception:  # noqa: BLE001 - 写失败仅报告，不阻断
        notes.append("mainline.json 对齐写盘失败（本次仅内存生效）")  # noqa: SILENT_DEGRADE
    for n in notes:
        if console is not None:
            console.print(f"[yellow]⚠ 规划一致性：{n}[/yellow]")
        else:
            print(f"⚠ 规划一致性：{n}")
    return notes
