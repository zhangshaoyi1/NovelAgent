"""PlanStore —— plan.json 唯一读写收口（复盘根治 P1，2026-09-07）。

背景（五灵破归档 2026-09-07 结局模式误触发事故链，见
项目文档/进度状态一致性复盘与根治方案.md）：
``total_chapters`` 本是 ``scope.estimated_chapters`` 的推导值，却被多个
程序化写入点和人/Agent 直改当成独立字段随意改写；派生关系断裂后
mainline / route / ending_mode 全线漂移（规划告警 ×7、结局模式在全书
之外的第 13 章触发、才 14 章误入结局模式）。

设计原则：
- **唯一写入口** ``mutate()``：读 → 变更 → 不变量校正 → 原子写 →
  变更日志 → 派生重算。plan.json 的程序化修改必须走这里。
- **推导关系强制化**：``scope.estimated_chapters`` 存在时，
  ``total_chapters`` 一律钳制为该值（默认行为，附警告与日志）；
  确需偏离（修复/拍板场景）用 ``override=True`` + ``reason``，同样留痕。
- **原子写**：tmp + replace（与 planner._save 同型）。
- **变更日志**：``.state/plan_history.json``（append-only，上限 50 条，
  记录变更键、旧/新 total、reason、是否 override）。
- **派生重算**：``total_chapters`` 变化时惰性调用
  ``plan_consistency.align_mainline_to_plan``（core 不反向依赖 workflows，
  方法内延迟导入，与 mainline_orchestrator 同型）。
- **读侧防线**：``load_validated()`` 对直改破坏返回数据 + 告警列表
  （G3：不阻断读，留待写侧修复），调用方可自行打印/记录。

不变量清单（后续新增规划字段时在此扩展）：
1. ``total_chapters`` 为正整数（可从 str 宽容转换）；
2. ``total_chapters == scope.estimated_chapters``（scope 存在时强制推导）。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable

HISTORY_FILE = "plan_history.json"
HISTORY_LIMIT = 50


class PlanInvariantError(RuntimeError):
    """plan.json 不变量被破坏且无法安全校正时抛出（当前仅保留扩展位）。"""


class PlanStore:
    """plan.json 唯一读写入口。所有程序化修改必须经 ``mutate()``。"""

    def __init__(self, project_dir: str | Path) -> None:
        self.project_dir = Path(project_dir)
        self.state_dir = self.project_dir / ".state"
        self.plan_file = self.state_dir / "plan.json"

    # ---------------------------------------------------------------- 读
    def load(self) -> dict[str, Any] | None:
        """原始读取（不存在/损坏返回 None，不抛异常）。"""
        if not self.plan_file.exists():
            return None
        try:
            data = json.loads(self.plan_file.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 - 损坏文件交由调用方处置
            return None
        return data if isinstance(data, dict) else None

    def load_validated(self) -> tuple[dict[str, Any] | None, list[str]]:
        """读取 + 不变量检查。返回 (plan, warnings)；只报告不修改。"""
        plan = self.load()
        if plan is None:
            return None, []
        return plan, self.invariant_warnings(plan)

    # ---------------------------------------------------------------- 写
    def mutate(
        self,
        mutator: Callable[[dict[str, Any]], dict[str, Any] | None],
        *,
        reason: str = "",
        override: bool = False,
        console: Any = None,
    ) -> dict[str, Any]:
        """唯一写入口。

        Args:
            mutator: 接收当前 plan（不存在时传 ``{}``），返回修改后的 plan；
                返回 ``None`` 表示无变更（不写盘、不留史）。
            reason: 变更原因（写入变更日志）。
            override: 置 True 时允许 ``total_chapters`` 偏离
                ``scope.estimated_chapters``（必须给 reason，日志留痕）。
            console: 可选 rich console，用于回显钳制/覆盖警告。

        Returns:
            最终落盘的 plan。

        Raises:
            PlanInvariantError: override=True 但 reason 为空。
        """
        if override and not reason:
            raise PlanInvariantError("override=True 必须携带 reason（审计留痕）")
        old = self.load() or {}
        new = mutator(dict(old))
        if new is None:
            return dict(old)
        if not isinstance(new, dict):
            raise PlanInvariantError("mutator 必须返回 dict 或 None")

        notes = self._enforce_invariants(old, new, override=override)
        for n in notes:
            if console is not None:
                console.print(f"[yellow]⚠ 规划一致性：{n}[/yellow]")
            else:
                print(f"⚠ 规划一致性：{n}")

        old_total = self._valid_total(old)
        new_total = self._valid_total(new)
        self._atomic_write(new)
        self._append_history(old, new, reason=reason, override=override)

        if old_total is not None and new_total is not None and old_total != new_total:
            self._recalc_derived(console=console)
        return new

    # ---------------------------------------------------------------- 不变量
    @staticmethod
    def _valid_total(plan: dict[str, Any]) -> int | None:
        try:
            total = int(plan.get("total_chapters") or 0)
        except (TypeError, ValueError):
            return None
        return total if total > 0 else None

    @classmethod
    def invariant_warnings(cls, plan: dict[str, Any]) -> list[str]:
        """读侧不变量检查（直改破坏检测）。返回告警列表，空 = 自洽。"""
        warnings: list[str] = []
        total = cls._valid_total(plan)
        if plan.get("total_chapters") is not None and total is None:
            warnings.append("total_chapters 非法（非正整数）")
        estimated = cls._estimated(plan)
        if total is not None and estimated is not None and total != estimated:
            warnings.append(
                f"total_chapters={total} 与 scope.estimated_chapters={estimated} 不一致"
                "（total_chapters 是 scope 推导值；请走 PlanStore.mutate 修复）"
            )
        return warnings

    @staticmethod
    def _estimated(plan: dict[str, Any]) -> int | None:
        try:
            est = int((plan.get("scope") or {}).get("estimated_chapters") or 0)
        except (TypeError, ValueError):
            return None
        return est if est > 0 else None

    def _enforce_invariants(
        self, old: dict[str, Any], new: dict[str, Any], *, override: bool
    ) -> list[str]:
        """写侧不变量强制。默认把 total 钳制到 scope.estimated（推导关系）。"""
        notes: list[str] = []
        estimated = self._estimated(new)
        total = self._valid_total(new)
        if total is None and new.get("total_chapters") is not None:
            raise PlanInvariantError(
                f"total_chapters 非法：{new.get('total_chapters')!r}（必须为正整数）"
            )
        if (
            not override
            and total is not None
            and estimated is not None
            and total != estimated
        ):
            new["total_chapters"] = estimated
            notes.append(
                f"total_chapters {total} → {estimated}"
                "（钳制为 scope.estimated_chapters：total 是 scope 推导值，"
                "不得独立修改；如确需偏离请 override=True 并说明 reason）"
            )
        return notes

    # ---------------------------------------------------------------- 落盘
    def _atomic_write(self, plan: dict[str, Any]) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.plan_file.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        tmp.replace(self.plan_file)

    def _append_history(
        self, old: dict[str, Any], new: dict[str, Any], *, reason: str, override: bool
    ) -> None:
        changed = sorted(
            k for k in set(old) | set(new) if old.get(k) != new.get(k)
        )
        entry = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "reason": reason,
            "override": override,
            "keys_changed": changed,
            "total_chapters": {"old": old.get("total_chapters"), "new": new.get("total_chapters")},
        }
        hist_file = self.state_dir / HISTORY_FILE
        history: list[dict[str, Any]] = []
        try:
            if hist_file.exists():
                loaded = json.loads(hist_file.read_text(encoding="utf-8"))
                if isinstance(loaded, list):
                    history = loaded
        except Exception:  # noqa: BLE001 - 历史损坏视为空，重建
            history = []  # noqa: SILENT_DEGRADE
        history.append(entry)
        history = history[-HISTORY_LIMIT:]
        try:
            hist_file.write_text(
                json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception:  # noqa: BLE001 - 日志写失败不影响主写
            pass  # noqa: SILENT_DEGRADE

    def _recalc_derived(self, console: Any = None) -> None:
        """total_chapters 变化后重算派生物（mainline horizon/share 对齐）。"""
        try:
            # 延迟导入：core 内部依赖（R6 分层，align 已下沉 core/story/mainline_align）
            from agent.core.story.mainline_align import align_mainline_to_plan

            align_mainline_to_plan(self.project_dir, console=console)
        except Exception:  # noqa: BLE001 - 派生重算失败不阻断主写（写前对账兜底）
            pass  # noqa: SILENT_DEGRADE
