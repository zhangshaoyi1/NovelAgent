"""跨批回退预算（P1，2026-09-12）——把「连续几次回退仍不达标」这件事持久化。

背景（五灵破归档 31 次回退复盘）：
``max_rollback_attempts=3`` 形同虚设——它只在
:meth:`EvaluatorAgent.evaluate_with_repair` 的循环里计数，而**滚动体检走的是
``evaluate()``（不计数）**；每批又会新建一个 ``EvaluatorAgent``，计数随之归零。
于是「回退 → 外层盲目重写同样的 5 章 → 同样的章间矛盾 → 再回退」永不收敛，
且没有任何一条路径会 ``escalated`` 上报人工。

本模块把这件事**落到磁盘**，跨批次、跨进程生效：

- :meth:`bump` —— 每次回退 +1，并记录回退目标章，用于识别「同一窗口反复翻车」；
- :meth:`reset` —— 体检通过时归零（因此统计的是**连续**失败，不会把历史成功后的
  偶发失败一并算进来）；
- :meth:`tripped` —— 连续次数超过上限 ⇒ 上层应 ``escalated`` 停批、上报人工，
  禁止无限重试。

存储：``.state/rollback_budget.json``。读写失败一律降级为「不阻断」——
本模块是护栏，不是关键路径。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from agent.core.infra.degrade import degrade

BUDGET_FILE = ".state/rollback_budget.json"


def _as_int(value: Any) -> int:
    """宽松取整：字段缺失/类型异常按 0 处理（预算文件是护栏旁路，不因它阻断写章）。"""
    try:
        return int(value)
    except (TypeError, ValueError) as e:
        degrade("rollback_budget.as_int", "预算字段类型异常，按 0 处理", e)
        return 0


@dataclass
class RollbackBudget:
    """连续回退计数器（持久化于项目 ``.state/rollback_budget.json``）。"""

    project_dir: Path
    limit: int = 3
    consecutive: int = 0
    total: int = 0
    last_target: int = 0
    same_target_streak: int = 0
    last_reason: str = ""
    updated_at: str = ""

    @property
    def path(self) -> Path:
        return Path(self.project_dir) / BUDGET_FILE

    # ---- 存取 ----
    @classmethod
    def load(cls, project_dir: str | Path, limit: int = 3) -> "RollbackBudget":
        budget = cls(project_dir=Path(project_dir), limit=max(1, _as_int(limit) or 3))
        f = budget.path
        if not f.exists():
            return budget
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            degrade("rollback_budget.load", "预算文件损坏或不可读，视为未计数", e)
            return budget
        if not isinstance(data, dict):
            return budget
        budget.consecutive = _as_int(data.get("consecutive"))
        budget.total = _as_int(data.get("total"))
        budget.last_target = _as_int(data.get("last_target"))
        budget.same_target_streak = _as_int(data.get("same_target_streak"))
        budget.last_reason = str(data.get("last_reason") or "")
        budget.updated_at = str(data.get("updated_at") or "")
        return budget

    def save(self) -> None:
        self.updated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "consecutive": self.consecutive,
            "total": self.total,
            "last_target": self.last_target,
            "same_target_streak": self.same_target_streak,
            "last_reason": self.last_reason,
            "updated_at": self.updated_at,
            "limit": self.limit,
        }
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    # ---- 计数 ----
    def bump(self, target_chapter: int = 0, reason: str = "") -> int:
        """记一次「体检不达标 → 回退」。返回累计的连续回退次数。"""
        self.consecutive += 1
        self.total += 1
        target = _as_int(target_chapter)
        if target and target == self.last_target:
            self.same_target_streak += 1
        else:
            self.same_target_streak = 1 if target else 0
        self.last_target = target
        self.last_reason = (reason or "")[:200]
        try:
            self.save()
        except OSError as e:
            degrade("rollback_budget.bump.save", "回退预算落盘失败，计数退化为进程内", e)
        return self.consecutive

    def reset(self) -> None:
        """体检通过 → 连续计数归零（累计 total 保留，供复盘）。"""
        if self.consecutive == 0 and self.same_target_streak == 0:
            return
        self.consecutive = 0
        self.same_target_streak = 0
        try:
            self.save()
        except OSError as e:
            degrade("rollback_budget.reset.save", "回退预算落盘失败，跳过持久化", e)

    def tripped(self) -> bool:
        """连续回退次数是否已超过上限（超过即应上报人工，停止自动重试）。"""
        return self.consecutive > self.limit

    def reason_text(self) -> str:
        """给人工看的一句话：为什么停、停在哪。"""
        churn = (
            f"；同一章节窗口（第 {self.last_target} 章附近）已连续回退 {self.same_target_streak} 次"
            if self.last_target and self.same_target_streak >= 2
            else ""
        )
        return (
            f"连续 {self.consecutive} 次体检不达标并回退，已超过上限 {self.limit}"
            f"（累计回退 {self.total} 次）{churn}。自动重试已停止，需人工介入："
            f"请检查设定/细纲一致性，或降低阈值后重新发起。最近一次原因：{self.last_reason}"
        )


__all__ = ["BUDGET_FILE", "RollbackBudget"]
