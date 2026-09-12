"""资源账本（resource ledger，长线一致性设计稿第二期·维度轨迹账本 P1 #3）

解决：writer 顺手让主角掏出前文没得到的宝贝、或花掉没赚到的灵石——
"账实不符"类硬穿帮。确定性进出记账，零 LLM 成本。

数据来源（与实体名册同一 facts 流，``sync_resources_from_facts``）：
- ``world`` 域 ``count`` 事实（正文计数，如"三十七"枚培元丹）→ 解析中文数字
  得到观察快照，与存量账面比对：一致则仅刷新观察章；有出入记"账实核对"事件
  并以正文为准更新（正文即 canon）；
- ``world`` 域 ``holder`` 事实 → 持有者转移事件。

写时注入（``render_for_prompt``）：主角与出场角色名下的资源清单——writer
掏东西前先对账。纯文件读写，失败显性。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any


class ResourceError(Exception):
    """资源账本操作违规（文件损坏等）。"""


_CN_DIGITS = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
              "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_CN_UNITS = {"十": 10, "百": 100, "千": 1000}


def parse_cn_number(text: str) -> int | None:
    """解析中文数字/阿拉伯数字（"三十七"/"一百二十"/"两"/"37"）；解析失败返回 None。

    覆盖常用组合（十/百/千级），不追求完整中文数词体系——解析失败宁缺毋滥，
    调用方按"账面未知"处理。
    """
    s = str(text).strip()
    if not s:
        return None
    if s.isdigit():
        return int(s)
    section = 0
    num = 0
    seen = False
    for ch in s:
        if ch in _CN_DIGITS:
            num = _CN_DIGITS[ch]
            seen = True
        elif ch in _CN_UNITS:
            unit = _CN_UNITS[ch]
            section += (num if num else 1) * unit
            num = 0
            seen = True
        elif ch == "零":
            continue
        else:
            # 量词/杂质（"枚"/"堆"…）：整串必须可解析，否则宁缺毋滥
            return None
        if section + num > 9_999_999:
            return None
    return (section + num) if seen else None


@dataclass
class ResourceEvent:
    """一条资源变动事件（delta=None 表示观察快照、非增减）。"""

    ch: int
    desc: str
    delta: int | None = None


@dataclass
class ResourceEntry:
    """一项资源：当前持有者 + 账面数量 + 变动事件链。"""

    item: str
    holder: str = ""  # 当前持有者（"主角"或角色名；空=公共/未知）
    qty: int | None = None  # 账面数量（None=未知，仅登记存在）
    events: list[ResourceEvent] = field(default_factory=list)


class ResourceLedgerStore:
    """资源账本存取（``.state/continuity/resources.json``）。"""

    def __init__(self, project_dir: str | Path) -> None:
        self.project_dir = Path(project_dir)
        self.path = self.project_dir / ".state" / "continuity" / "resources.json"
        self.entries: list[ResourceEntry] = []

    def load(self) -> "ResourceLedgerStore":
        if not self.path.exists():
            self.entries = []
            return self
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise ResourceError(f"资源账本文件损坏（{self.path}）：{e}") from e
        self.entries = [
            ResourceEntry(
                **{
                    **{k: v for k, v in it.items() if k in ResourceEntry.__dataclass_fields__},
                    "events": [
                        ResourceEvent(**ev) for ev in it.get("events", []) if isinstance(ev, dict)
                    ],
                }
            )
            for it in raw.get("resources", [])
            if isinstance(it, dict)
        ]
        return self

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"resources": [asdict(r) for r in self.entries]}
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def get(self, item: str) -> ResourceEntry | None:
        item = item.strip()
        for e in self.entries:
            if e.item == item:
                return e
        return None

    def ensure(self, item: str) -> ResourceEntry:
        item = item.strip()
        if not item:
            raise ResourceError("资源名不能为空")
        e = self.get(item)
        if e is None:
            e = ResourceEntry(item=item)
            self.entries.append(e)
        return e

    def held_by(self, holder: str) -> list[ResourceEntry]:
        return [e for e in self.entries if e.holder == holder.strip() and (e.qty is None or e.qty > 0)]

    # ------ 写时渲染 ------
    def render_for_prompt(self, holders: list[str], limit: int = 8) -> str:
        lines: list[str] = []
        for h in holders:
            items = self.held_by(h)[:limit]
            if items:
                parts = [
                    f"{e.item}（{'数量未知' if e.qty is None else f'x{e.qty}'}）"
                    for e in items
                ]
                lines.append(f"{h}名下：{'、'.join(parts)}")
        if not lines:
            return ""
        return (
            "\n【资源账本（主角/出场角色名下的既有资源；本章动用必须先对账，"
            "不得凭空出现或消失）】\n" + "\n".join(f"- {ln}" for ln in lines)
        )


def sync_resources_from_facts(project_dir: str | Path, facts: list[Any], chapter_num: int) -> int:
    """从连续性账本 facts 确定性同步资源账（与实体名册同一 facts 流）。

    - ``world`` 域 ``count``：正文计数快照，与账面比对，有出入记"账实核对"事件
      并以正文为准；解析不出数字则仅登记存在（qty=None）。
    - ``world`` 域 ``holder``：持有者转移事件。

    Returns:
        更新条目次数。 Raises: ResourceError（文件损坏，调用方 degrade 处理）。
    """
    store = ResourceLedgerStore(project_dir).load()
    touched = 0
    for f in facts:
        domain = str(getattr(f, "domain", ""))
        item = str(getattr(f, "subject_id", "")).strip()
        fname = str(getattr(f, "field", ""))
        value = str(getattr(f, "value", "")).strip()
        if domain != "world" or not item or fname not in ("count", "holder"):
            continue
        entry = store.ensure(item)
        touched += 1
        if fname == "count":
            n = parse_cn_number(value)
            if n is None:
                if not entry.events or entry.events[-1].desc != "正文提及（数量未知）":
                    entry.events.append(ResourceEvent(ch=int(chapter_num or 0), desc="正文提及（数量未知）"))
            elif entry.qty != n:
                if entry.qty is None:
                    entry.events.append(ResourceEvent(ch=int(chapter_num or 0), desc=f"入账 x{n}"))
                else:
                    entry.events.append(ResourceEvent(
                        ch=int(chapter_num or 0), delta=n - entry.qty,
                        desc=f"账实核对：账面 {entry.qty} → 正文 {n}（以正文为准）",
                    ))
                entry.qty = n
        else:  # holder
            if value and value != entry.holder:
                entry.events.append(ResourceEvent(
                    ch=int(chapter_num or 0), desc=f"持有者：{entry.holder or '（无）'} → {value}",
                ))
                entry.holder = value
    if touched:
        store.save()
    return touched


__all__ = [
    "ResourceError",
    "ResourceEvent",
    "ResourceEntry",
    "ResourceLedgerStore",
    "parse_cn_number",
    "sync_resources_from_facts",
]
