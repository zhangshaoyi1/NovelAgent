"""高潮账本（名场面登记，PRD B7 / 登记单 20261001_信任链与叙事上限六项能力·子项 6）

背景
----
伏笔/心性/关系账本全是「防出错」的守势机制；「第 40 章该引爆一场蓄谋已久的高潮」
这种**攻势**的戏剧结构设计没有承载。本模块把「预定高潮事件」变为一等账本数据：
锚定章区间、蓄势来源（喂它的伏笔/支线）、满足判据、状态推进（pending → charging
→ detonated / skipped）。

设计取舍
--------
- **数据形态**：独立账本文件 ``.state/climax_ledger.json`` + 唯一读写口
  :class:`ClimaxLedgerStore`（同 foresight/pacing 模式）——不改 plan.json schema
  （PlanStore 契约不动），计划数据的单一权威语义等价。
- **提取（deterministic bootstrap）**：``derive_from_plan`` 从 MasterPlan 的
  episode_tree 弧线确定性推导——每条弧的末章即该卷主峰锚点（弧级模型
  「高潮爆发」相位的确定论落点），蓄势来源取 ``foreshadow_plan`` 中预计回收点
  落在弧区间内的伏笔。首轮 M3 后调用一次即可建账（batch_replan 空账自举）。
- **写前注入**：``climax_context_text`` 随伏笔任务通道下发（m5_context 装配）——
  蓄势章告知「在为什么蓄势」，引爆章给出兑现要求（登记的判据）。
- **状态推进**：``update_status_after_chapter`` 在章末调用——引爆判定 =
  蓄势来源伏笔在 G15 连续性账本中于本章 resolve（复用 60 字回收判据的登记产物）；
  逾期（chapter_end+2 后仍未引爆）→ skipped 留因，检查点卡/体检点名。
- **爆点间隔告警**：确定性——距上次引爆超阈值（默认 30 章）告警。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from agent.core.infra.degrade import degrade

CLIMAX_LEDGER_FILE = ".state/climax_ledger.json"

#: 爆点间隔告警阈值（章）：距上次引爆超过该值即告警（可按体量在账本里覆写）
DETONATION_GAP_ALERT = 30

#: 引爆判定的宽限期：peak 后 N 章内仍未兑现 → skipped 留因
SKIP_GRACE_CHAPTERS = 2

VALID_STATUS = ("pending", "charging", "detonated", "skipped")


class ClimaxEvent(BaseModel):
    """一个预定高潮事件（名场面登记）。"""

    id: str
    title: str
    climax_type: str = "main"        # main=卷级主峰 / beat=小爆点
    chapter_start: int
    peak_chapter: int
    chapter_end: int
    feeders: list[str] = Field(default_factory=list)   # 蓄势来源：伏笔 loop_id / 支线名
    criteria: str = ""               # 满足判据（引爆章的写法要求，人读）
    status: str = "pending"          # pending / charging / detonated / skipped
    detonated_ch: int = 0
    skip_reason: str = ""
    updated_at: str = ""


class ClimaxLedgerStore:
    """高潮账本唯一读写口（原子写 + 全量替换；不做并发合并——单写者架构）。"""

    def __init__(self, project_dir: str | Path) -> None:
        self.path = Path(project_dir) / CLIMAX_LEDGER_FILE

    def load(self) -> list[ClimaxEvent]:
        if not self.path.exists():
            return []
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            events = data.get("events", []) if isinstance(data, dict) else []
            return [ClimaxEvent.model_validate(e) for e in events if isinstance(e, dict)]
        except Exception as e:  # noqa: BLE001 - 账本损坏不阻断写作，显性降级
            degrade("climax_ledger.load", "高潮账本不可读，按空账处理（登记丢失风险）", e)
            return []

    def save(self, events: list[ClimaxEvent]) -> None:
        for e in events:
            e.updated_at = time.strftime("%Y-%m-%dT%H:%M:%S")
        payload = {"events": [e.model_dump() for e in events]}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        tmp.replace(self.path)

    def upsert(self, event: ClimaxEvent) -> None:
        events = self.load()
        events = [e for e in events if e.id != event.id] + [event]
        events.sort(key=lambda e: (e.peak_chapter, e.id))
        self.save(events)


def derive_from_plan(project_dir: str | Path) -> list[ClimaxEvent]:
    """从 MasterPlan 确定性推导高潮事件（每弧一主峰；空计划返回空）。

    - 主峰锚点 = 弧末章（弧级模型「高潮爆发」相位的确定论落点）；
    - 蓄势来源 = ``foreshadow_plan`` 中预计回收点落在弧区间内的伏笔 id；
    - 判据 = 「兑现蓄势来源 + 完成/推进弧目标」。
    """
    project_path = Path(project_dir)
    try:
        plan = json.loads(
            (project_path / ".state" / "plan.json").read_text(encoding="utf-8")
        )
    except Exception as e:  # noqa: BLE001
        degrade("climax_ledger.derive", "plan.json 不可读，无法推导高潮事件", e)
        return []
    arcs = plan.get("episode_tree") or []
    if not arcs:
        return []
    foreshadows = plan.get("foreshadow_plan") or []
    events: list[ClimaxEvent] = []
    for a in arcs:
        if not isinstance(a, dict):
            continue
        raw_start, raw_end = a.get("chapter_start", 0), a.get("chapter_end", 0)
        start = int(raw_start) if isinstance(raw_start, (int, float)) else 0
        end = int(raw_end) if isinstance(raw_end, (int, float)) else 0
        if end <= 0 or end < start:
            continue
        feeders = [
            str(f.get("id"))
            for f in foreshadows
            if isinstance(f, dict)
            and start <= int(f.get("expected_resolve_est", 0) or 0) <= end
        ][:6]
        name = str(a.get("name") or a.get("id") or "?")
        goal = str(a.get("goal") or "")[:120]
        events.append(
            ClimaxEvent(
                id=f"climax-{a.get('id', len(events))}",
                title=f"《{name}》卷末主峰",
                climax_type="main",
                chapter_start=start,
                peak_chapter=end,
                chapter_end=end,
                feeders=feeders,
                criteria=(
                    f"引爆本章需：{'、'.join('兑现伏笔 ' + fid for fid in feeders) or '给出弧线高潮场面'}"
                    + (f"；并完成/实质推进弧目标「{goal}」" if goal else "")
                ),
            )
        )
    return events


def ensure_ledger(project_dir: str | Path) -> list[ClimaxEvent]:
    """空账自举：账本为空且有计划时推导建账（幂等；失败降级返回空）。"""
    store = ClimaxLedgerStore(project_dir)
    events = store.load()
    if events:
        return events
    events = derive_from_plan(project_dir)
    if events:
        try:
            store.save(events)
        except Exception as e:  # noqa: BLE001
            degrade("climax_ledger.bootstrap", "高潮账本建账落盘失败", e)
    return events


def _resolved_loops_this_chapter(project_dir: Path, chapter: int) -> set[str]:
    """查 G15 连续性账本：本章 resolve 的 open_loop 集合（查询失败按空集）。"""
    try:
        from agent.core.continuity.ledger import ContinuityLedgerStore

        ledger = ContinuityLedgerStore(project_path_str := str(project_dir)).load()
        out: set[str] = set()
        for lo in ledger.open_loops:
            if lo.status != "resolved":
                continue
            rin = str(lo.resolved_in or "")
            if str(chapter) in rin:
                out.add(lo.loop_id)
        return out
    except Exception as e:  # noqa: BLE001
        degrade("climax_ledger.check", "连续性账本查询失败，引爆判定按未兑现", e)
        return set()


def update_status_after_chapter(project_dir: str | Path, chapter: int) -> None:
    """章末状态推进（pipeline 章末调用；任何失败显性降级不阻断）。"""
    project_path = Path(project_dir)
    store = ClimaxLedgerStore(project_path)
    events = store.load()
    if not events:
        return
    changed = False
    resolved_here = None  # 惰性查询：仅在有候选引爆事件时查账本
    for e in events:
        if e.status in ("detonated", "skipped"):
            continue
        if e.chapter_start <= chapter and e.status == "pending":
            e.status = "charging"
            changed = True
        if e.status == "charging" and chapter >= e.peak_chapter:
            if resolved_here is None:
                resolved_here = _resolved_loops_this_chapter(project_path, chapter)
            hit = any(fid in resolved_here for fid in e.feeders) if e.feeders else None
            if hit or (hit is None and not e.feeders):
                # 有伏笔于本章兑现，或无登记来源（宽判据：到峰即引爆，批末复核查漏）
                e.status = "detonated"
                e.detonated_ch = chapter
                changed = True
            elif chapter > e.chapter_end + SKIP_GRACE_CHAPTERS:
                e.status = "skipped"
                e.skip_reason = (
                    f"至第 {chapter} 章（峰 {e.peak_chapter} + 宽限 {SKIP_GRACE_CHAPTERS}）"
                    "蓄势来源仍未兑现"
                )
                changed = True
    if changed:
        try:
            store.save(events)
        except Exception as err:  # noqa: BLE001
            degrade("climax_ledger.update", "高潮账本状态落盘失败", err)


def climax_context_text(project_dir: str | Path, chapter: int) -> str:
    """写前注入文本：本章处于哪个高潮事件的蓄势/引爆位（空串=无关章）。"""
    events = ClimaxLedgerStore(project_dir).load()
    lines: list[str] = []
    for e in events:
        if e.status in ("skipped",):
            continue
        if e.status == "detonated":
            continue
        if e.chapter_start <= chapter <= e.chapter_end:
            if chapter >= e.peak_chapter:
                lines.append(
                    f"★ 本章为登记的**高潮引爆章**（{e.title}）：{e.criteria}。"
                    "高潮必须以完整场景呈现，不得一句带过。"
                )
            else:
                lines.append(
                    f"★ 本章处于高潮**蓄势段**（{e.title}，第 {e.peak_chapter} 章引爆）："
                    "只推进、加压、收紧，不提前摊牌；为引爆章积累情绪与信息差。"
                )
    return "\n".join(lines)


def ledger_warnings(project_dir: str | Path, current_chapter: int) -> list[str]:
    """账本级告警（进检查点卡 risks）：逾期未引爆 + 爆点间隔超阈 + 跳过点名。"""
    events = ClimaxLedgerStore(project_dir).load()
    out: list[str] = []
    last_detonated = 0
    for e in sorted(events, key=lambda x: x.peak_chapter):
        if e.status == "skipped":
            out.append(f"高潮事件被跳过：{e.title}（{e.skip_reason}）——蓄势浪费，体检/人工应复核")
            continue
        if e.status == "detonated":
            last_detonated = max(last_detonated, e.detonated_ch)
            continue
        if e.status in ("pending", "charging") and current_chapter > e.chapter_end + SKIP_GRACE_CHAPTERS:
            out.append(
                f"高潮事件逾期未引爆：{e.title}（峰 {e.peak_chapter}，当前第 {current_chapter} 章）"
                "——该爆没爆，节奏将出现长平段"
            )
    if last_detonated and current_chapter - last_detonated > DETONATION_GAP_ALERT:
        out.append(
            f"距上次高潮引爆已 {current_chapter - last_detonated} 章"
            f"（阈值 {DETONATION_GAP_ALERT}）——爆点间隔超限，读者疲劳风险"
        )
    return out
