"""追读力账本存储（增量 C / T04）

``PacingStore`` 读写 ``.state/pacing.json``（追读力账本），严格遵循
``InjectedTropeStore`` 存储范式：get/set/add/clear + 解析失败降级为空、不阻断写章。

账本结构（``Ledger``）：
- ``open_debts``：未收回的「钩子债 / 伏笔债」（``Debt`` 列表）
- ``resolved``：已收回的债务
- ``cool_density``：每章爽点密度序列（用于趋势观测）
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Debt:
    """一条「债务」（钩子/伏笔/角色线未收回项）"""

    id: str
    desc: str = ""
    kind: str = "general"     # general / foreshadow / character / plot
    planted_ch: int = 0       # 埋设章节
    status: str = "open"      # open / resolved


@dataclass
class Ledger:
    """追读力账本"""

    open_debts: list[Debt] = field(default_factory=list)
    resolved: list[Debt] = field(default_factory=list)
    cool_density: list[float] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "open_debts": [asdict(d) for d in self.open_debts],
            "resolved": [asdict(d) for d in self.resolved],
            "cool_density": list(self.cool_density),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Ledger":
        try:
            return cls(
                open_debts=[Debt(**d) for d in (data.get("open_debts") or [])],
                resolved=[Debt(**d) for d in (data.get("resolved") or [])],
                cool_density=[float(x) for x in (data.get("cool_density") or [])],
            )
        except (TypeError, ValueError, KeyError):
            return cls()


class PacingStore:
    """追读力账本存储（.state/pacing.json）"""

    def __init__(self, project_dir: Path) -> None:
        self.project_dir = Path(project_dir)
        self.file = self.project_dir / ".state" / "pacing.json"

    # ============================================================
    # 读写（损坏降级空，绝不抛异常）
    # ============================================================
    def load(self) -> Ledger:
        """加载账本；文件缺失/损坏一律降级为空账本"""
        if not self.file.exists():
            return Ledger()
        try:
            return Ledger.from_dict(json.loads(self.file.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError, ValueError, TypeError):
            return Ledger()

    def save(self, ledger: Ledger) -> None:
        """持久化账本"""
        self.file.parent.mkdir(parents=True, exist_ok=True)
        self.file.write_text(
            json.dumps(ledger.to_dict(), ensure_ascii=False), encoding="utf-8"
        )

    # ============================================================
    # 增量操作
    # ============================================================
    def add_debt(self, debt: Debt) -> None:
        """新增一条开放债务（去重：同 id 不重复添加）"""
        ledger = self.load()
        if any(d.id == debt.id for d in ledger.open_debts):
            return
        ledger.open_debts.append(debt)
        self.save(ledger)

    def get_open_debts(self, n: int = 20) -> list[Debt]:
        """取当前开放债务（最多 n 条）"""
        return self.load().open_debts[:n]

    def clear(self) -> None:
        """清空账本（保留文件，写入空账本）"""
        self.save(Ledger())
