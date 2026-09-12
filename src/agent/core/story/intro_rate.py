"""新实体引入率度量（长线一致性设计稿第二期·结构管理者消费端）

读者记忆负荷有限——一次塞过多新命名实体（人名/势力/地名）会掉读，高潮段
尤其如此。名册（entity_ledger）已记录每个实体的首出场章，本模块只做消费端
度量：近 N 章的平均新实体引入率，超阈值时产出给规划者的提示文本（复规划
摘要注入，控制世界观展开节奏）。

纯确定性，零 LLM。
"""

from __future__ import annotations

from pathlib import Path

from agent.core.story.entity_ledger import EntityLedgerStore, EntityLedgerError


def new_entity_rate(project_dir: str | Path, window: int = 10) -> dict | None:
    """近 window 章的新实体引入率。

    Returns:
        {"window_start", "window_end", "total", "per_chapter", "by_ch": {ch: n}}
        数据不足（名册为空/无首章记录）返回 None。
    """
    try:
        st = EntityLedgerStore(project_dir).load()
    except EntityLedgerError:
        raise
    firsts = [e.first_ch for e in st.entities if e.first_ch]
    if not firsts:
        return None
    last = max(firsts)
    start = max(1, last - window + 1)
    in_window = [c for c in firsts if start <= c <= last]
    by_ch: dict[int, int] = {}
    for c in in_window:
        by_ch[c] = by_ch.get(c, 0) + 1
    span = last - start + 1
    return {
        "window_start": start,
        "window_end": last,
        "total": len(in_window),
        "per_chapter": round(len(in_window) / span, 2) if span else 0.0,
        "by_ch": by_ch,
    }


def intro_rate_text(project_dir: str | Path, window: int = 10, warn_threshold: float = 3.0) -> str:
    """给规划者的引入率提示（超阈值显性提示控制节奏；数据不足返回空串）。"""
    try:
        rate = new_entity_rate(project_dir, window)
    except EntityLedgerError as e:
        from agent.core.infra.degrade import degrade

        degrade("intro_rate.text", "名册损坏，引入率度量不可用", e)
        return ""
    if rate is None:
        return ""
    peak = max(rate["by_ch"].values()) if rate["by_ch"] else 0
    lines = [f"近 {rate['window_end'] - rate['window_start'] + 1} 章新引入命名实体 {rate['total']} 个"
             f"（均值 {rate['per_chapter']}/章，单章峰值 {peak}）"]
    if rate["per_chapter"] > warn_threshold:
        lines.append(
            f"⚠ 引入率超阈值 {warn_threshold}/章：后续弧线应放缓新命名实体（人名/势力/地名）"
            "的引入节奏，优先收敛既有实体；高潮段尤其收紧。"
        )
    return "\n【新实体引入率（读者记忆负荷度量）】\n" + "\n".join(lines)


__all__ = ["new_entity_rate", "intro_rate_text"]
