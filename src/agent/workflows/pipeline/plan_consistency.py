"""规划一致性守护（缺口 A/C，2026-09-06）。

背景（novels/无灵 实证，见 .agents/notes/proposed/architecture/
2026-09-06-plot-stall-root-causes-and-fix-plan.md）：
1. ``mainline.json`` 的 horizon/subline_share 由 world.md 体量机械估算生成，
   从未与 ``plan.json.total_chapters`` 校验 → S01 cap=480 在 350 章的书里
   永不触发切线，全书被锁死在首条支线（剧情原地打转的结构性根因）。
2. subline.md 缺「情节点序列 / 章节钩子设计」时 M5 静默注入空串，
   每章只剩「接上一章结尾续写」，母题循环无机制可纠。
3. 持久化顺序「先章节文件后进度」，崩溃后恢复按 ``total_written+1`` 盲写 →
   覆盖重写已存在章节（ch191 重演 ch190 实证）。

本模块确立 **plan.json 为全书规模唯一权威**，并承担写前对账：
- 主线预算（派生物）加载时按 plan 总章数自动对齐（horizon 钳制 + 份额等比缩放）；
- 主角路线节点区间越界 → 告警（用户文档，不自动改）；
- subline 剧情源缺失 → **fail-fast**（确定性不变量破坏，降级只会产出废稿）；
- 章节目录 vs ``total_written`` 对账，文件领先进度则补记进度（采纳文件，消除覆盖重写）。

原则：LLM 失败可降级（G3）；**确定性状态不一致必须响**（缺口 C）。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Optional


def load_plan_total(project_dir: str | Path) -> Optional[int]:
    """读 plan.json 的 total_chapters（全书规模唯一权威）；缺失/非法返回 None。"""
    f = Path(project_dir) / ".state" / "plan.json"
    if not f.exists():
        return None
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
        total = int(data.get("total_chapters") or 0)
        return total if total > 0 else None
    except Exception:  # noqa: BLE001 - 读失败视为未配置
        return None


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
    total = load_plan_total(project_dir)
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


def reconcile_chapters_with_progress(
    project_dir: str | Path, console: Any = None
) -> list[str]:
    """对账 chapters/ 目录与 state.json 的 total_written（缺口 C 恢复对账）。

    「先章节文件后进度」的写序在两步之间崩溃会留下『文件已存在、进度落后』，
    恢复后按 total_written+1 盲写会**覆盖重写**已有章节（ch191 实证）。
    本方法在写前把进度补记到章节文件的真实水位（采纳文件为准）。

    Returns:
        修复说明列表（一致则为空）。
    """
    project_dir = Path(project_dir)
    chapters_dir = project_dir / "chapters"
    if not chapters_dir.exists():
        return []
    max_ch = 0
    for f in chapters_dir.glob("ch*.md"):
        m = re.fullmatch(r"ch(\d+)\.md", f.name)
        if m:
            max_ch = max(max_ch, int(m.group(1)))
    if max_ch <= 0:
        return []

    state_file = project_dir / ".state" / "state.json"
    try:
        state = json.loads(state_file.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - 无状态文件时无从对账
        return []
    progress = state.get("progress") or {}
    try:
        written = int(progress.get("total_written") or 0)
    except (TypeError, ValueError):
        written = 0  # noqa: SILENT_DEGRADE
    if written >= max_ch:
        return []

    notes = [
        f"chapters/ 目录实际已有 {max_ch} 章，progress.total_written={written}，"
        "已按文件水位补记进度（防止恢复后覆盖重写已有章节）"
    ]
    progress["total_written"] = max_ch
    progress["current_chapter"] = max_ch
    state["progress"] = progress
    try:
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(
            json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception:  # noqa: BLE001
        notes.append("state.json 进度补记写盘失败")  # noqa: SILENT_DEGRADE
    if console is not None:
        for n in notes:
            console.print(f"[yellow]⚠ 恢复对账：{n}[/yellow]")
    return notes


_PLOT_SOURCE_SECTIONS = ("情节点序列", "章节钩子设计")


def check_subline_plot_source(project_dir: str | Path) -> list[str]:
    """校验每条 subline.md 至少含一个剧情源段（确定性不变量 → fail-fast）。

    M5 每章的前瞻剧情取自「情节点序列 / 章节钩子设计」，两段皆缺时静默注入
    空串，写作退化为「接上一章结尾自由续写」（无灵 ch100-198 母题循环实证）。
    这是数据缺陷而非 LLM 波动，必须启动即报错，不允许静默降级。

    Returns:
        致命错误列表（空 = 通过）。
    """
    errors: list[str] = []
    sublines_dir = Path(project_dir) / "sublines"
    if not sublines_dir.exists():
        return []  # 无支线结构的项目走原有流程，不在此拦截
    for sub_dir in sorted(sublines_dir.iterdir()):
        f = sub_dir / "subline.md"
        if sub_dir.is_dir() and not f.exists():
            errors.append(f"sublines/{sub_dir.name}/subline.md 缺失")
            continue
        try:
            content = f.read_text(encoding="utf-8")
        except Exception:  # noqa: BLE001
            errors.append(f"sublines/{sub_dir.name}/subline.md 读取失败")
            continue  # noqa: SILENT_DEGRADE
        if not any(_has_section(content, s) for s in _PLOT_SOURCE_SECTIONS):
            errors.append(
                f"sublines/{sub_dir.name}/subline.md 缺少剧情源段落"
                f"（「{'」或「'.join(_PLOT_SOURCE_SECTIONS)}」至少其一）。"
                "缺失时每章只能接上一章结尾自由续写，会导致剧情原地打转；"
                "请先运行大纲/支线生成补全后再写作。"
            )
    return errors


def _has_section(content: str, section_name: str) -> bool:
    """判断 markdown 是否存在非空 `## <section_name>` 段（与 mainline._extract_section 同型）。"""
    pattern = rf"## {re.escape(section_name)}\s*\n(.*?)(?=\n## |\Z)"
    m = re.search(pattern, content, re.DOTALL)
    return bool(m and m.group(1).strip())


def check_route_ranges(project_dir: str | Path) -> list[str]:
    """校验 protagonist_route.md 节点章节区间与 plan 总章数（用户文档 → 仅告警）。

    节点起点越过 plan 总章数 → 该节点整书不可达（无灵 N04=351-400 vs plan 350 实证）。
    路线文件是用户可编辑文档，不自动改写，只显著告警。
    """
    total = load_plan_total(project_dir)
    if total is None:
        return []
    f = Path(project_dir) / "protagonist_route.md"
    if not f.exists():
        return []
    try:
        content = f.read_text(encoding="utf-8")
    except Exception:  # noqa: BLE001
        return []
    warnings: list[str] = []
    for m in re.finditer(r"^\s*-\s*\*\*章节范围\*\*[:：]\s*(\d+)\s*-\s*(\d+)", content, re.MULTILINE):
        lo, hi = int(m.group(1)), int(m.group(2))
        if lo > total:
            warnings.append(
                f"protagonist_route.md 存在起点越界节点（章节范围 {lo}-{hi}，"
                f"plan 总章数 {total}）：该节点整书不可达，"
                "请重排路线节点或调整 plan.json total_chapters"
            )
    return warnings


def prepare_for_write(project_dir: str | Path, console: Any = None) -> list[str]:
    """写前统一入口：对齐派生物 → 恢复对账 → 不变量校验。

    Returns:
        致命错误列表（空 = 可以开写）。非致命问题（对齐/告警）直接打印。
    """
    align_mainline_to_plan(project_dir, console=console)
    reconcile_chapters_with_progress(project_dir, console=console)
    for w in check_route_ranges(project_dir):
        if console is not None:
            console.print(f"[yellow]⚠ 规划告警：{w}[/yellow]")
        else:
            print(f"⚠ 规划告警：{w}")
    return check_subline_plot_source(project_dir)
