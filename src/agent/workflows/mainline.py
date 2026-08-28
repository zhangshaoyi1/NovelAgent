"""G8 主线推进决策（纯确定性零 LLM，拍板 1）。

放 workflows/ 而非 core/：与 M5 `_determine_pressure_stage`（m5_write_chapter.py 行 602-627）
同源解析压力曲线，避免跨层循环依赖；pipeline 与测试均可直接 import。

决策源（多源合一，PRD §7 风险 3 缓解）：
1. ``_pressure_upper_bound`` —— 当前 subline.md「剧集压力曲线」表覆盖 chapter 的行区间上界；
2. ``_episode_upper_bound`` —— ``.state/plan.json`` 的 episode_tree 中该支线章节区间上界。
统一上界 ``U = max(P, E)``（均存在时取较晚者，宁可篇幅稍超也不切太早）；
两源均缺失 → 退化「每 mainline_window 章硬切」。

切换条件：``chapter > U``（已越过区间上界）；已到最后一条支线 / 已进入结局模式 → 返回 None。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Optional


def decide_mainline_advance(
    project_dir: str | Path,
    state_machine: Any,
    mainline_window: int = 5,
) -> Optional[str]:
    """每 mainline_window 章执行一次的确定性支线推进决策。

    Args:
        project_dir: 小说项目目录（读 sublines/plan.json/architecture）。
        state_machine: 已 load() 的状态机（读 progress 的 current_subline/total_written/ending_mode）。
        mainline_window: 决策窗口（章，默认 5，pipeline 保证 ≥1）。

    Returns:
        目标 subline_id（应切换到的下一条支线）或 None（不切换）。
        返回 None 的情形：无支线 / 当前支线未定 / 已到最后一条支线 /
        已进入结局模式（拍板 5：结局段禁新线）/ 尚未越过区间上界。
    """
    from agent.core.story.setting_manager import SettingManager

    project_dir = Path(project_dir)
    progress = state_machine.progress or {}
    chapter = int(progress.get("total_written", 0)) + 1  # 即将写的章号
    current = progress.get("current_subline", "") or ""
    ending_mode = bool(progress.get("ending_mode", False))

    sublines = SettingManager(project_dir).list_sublines()  # 排序稳定 S01→S05（行 185-191）
    if not sublines or not current:
        return None  # 首次写章由 M5 取首条（行 291-298）
    if ending_mode:
        return None  # 拍板 5：结局段不再切换新支线
    if current not in sublines:
        return None  # 脏数据防御

    # ---- 多源合一：统一上界 U（均存在时取较晚者，避免过早切换，PRD §7 风险 3）----
    p_ub = _pressure_upper_bound(project_dir, current, chapter)  # 源 1：剧集压力曲线区间
    e_ub = _episode_upper_bound(project_dir, current)  # 源 2：episode_tree 区间
    if p_ub is not None and e_ub is not None:
        upper = max(p_ub, e_ub)
    elif p_ub is not None:
        upper = p_ub
    elif e_ub is not None:
        upper = e_ub
    else:
        upper = None  # 无区间数据 → 保底硬切

    # ---- 切换条件：已越过区间上界；无区间数据时以决策窗口为保底 ----
    switch = (chapter > upper) if upper is not None else True
    if not switch:
        return None

    idx = sublines.index(current)
    if idx + 1 >= len(sublines):
        return None  # 已到最后一条 → 进入主线收束
    return sublines[idx + 1]


def _pressure_upper_bound(
    project_dir: str | Path, subline_id: str, chapter: int
) -> Optional[int]:
    """解析 subline.md「剧集压力曲线」表，返回覆盖 chapter 的行区间上界。

    表存在但 chapter 已越过所有区间 → 返回最大上界（必然 < chapter，触发切换）；
    无表/解析失败 → None（无约束，走保底）。
    """
    f = Path(project_dir) / "sublines" / subline_id / "subline.md"
    if not f.exists():
        return None
    try:
        content = f.read_text(encoding="utf-8")
    except Exception:  # noqa: BLE001 - 读失败降级
        return None
    section = _extract_section(content, "剧集压力曲线")  # 与 m5._extract_section 同逻辑
    if not section:
        return None
    bounds: list[tuple[int, int]] = []
    for line in section.splitlines():
        if line.startswith("|") and "阶段" not in line and "---" not in line:
            parts = [p.strip() for p in line.split("|")]
            if len(parts) >= 4:
                m = re.match(r"(\d+)[-~](\d+)", parts[2])
                if m:
                    lo, hi = int(m.group(1)), int(m.group(2))
                    bounds.append((lo, hi))
    if not bounds:
        return None
    for lo, hi in bounds:
        if lo <= chapter <= hi:
            return hi
    return max(hi for _, hi in bounds)  # 越界 → 已走完该支线压力曲线


def _episode_upper_bound(project_dir: str | Path, subline_id: str) -> Optional[int]:
    """读 .state/plan.json 的 episode_tree，返回该支线章节区间最大上界。

    （Arc.chapter_start/chapter_end/subline_id，planner_agent.py 行 57-64）
    """
    plan_file = Path(project_dir) / ".state" / "plan.json"
    if not plan_file.exists():
        return None
    try:
        data = json.loads(plan_file.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - 读失败降级
        return None
    arcs = data.get("episode_tree", []) or []
    ends = [
        int(a["chapter_end"]) for a in arcs
        if a.get("subline_id") == subline_id and a.get("chapter_end")
    ]
    return max(ends) if ends else None


def _extract_section(content: str, section_name: str) -> str:
    """从 markdown 内容提取 ## 段落（与 M5._extract_section 同逻辑）。"""
    pattern = rf"## {re.escape(section_name)}\s*\n(.*?)(?=\n## |\Z)"
    m = re.search(pattern, content, re.DOTALL)
    return m.group(1).strip() if m else ""
