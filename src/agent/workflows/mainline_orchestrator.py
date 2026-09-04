"""MainlineOrchestrator —— 主线/支线推进的**唯一**仲裁与落盘入口。

设计动机（收敛三个问题）：
1. 收口：此前「推进决策」代码被复制进 ``agentic_write`` 与 ``agentic_pipeline``，
   两个写章入口行为分叉（Web 逐章推进从未切支线）。本类把「裁决 + 落盘」收归一处，
   两条写章路径只需调用 :meth:`maybe_advance`。
2. 比例/预算可算出：顶层「前期/中期/后期 比例」过去只活在对话里，未落盘，
   运行时读不到。本类把分线预算持久化到 ``.state/mainline.json``（``subline_share``），
   并将之作为每个支线的硬上界（cap）传给 ``decide_mainline_advance``，从而避免
   某个支线被 LLM 自由生成的曲线无限拖长（「一本全在 S01」类问题）。
3. 预算缺省自动计算：``mainline.json`` 缺失时按 ``expected_chapters / n_sublines``
   均衡分账自动生成并落盘，保证任何书都有兜底预算。

数据来源：
- ``expected_chapters``：world.md 体量档位 → ``estimate_chapters``（core/story/volume）。
- ``subline_share``：每个支线的章数预算（上面 2/3）。可选，可被 ``mainline init --subline S0x=N`` 覆盖。

矩不包括 LLM。所有写章入口只依赖本类的确定性裁决（G8 拍板 1 语义保持）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from rich.console import Console

from agent.core.engine.state_machine import StateMachine


class MainlineOrchestrator:
    """确定性的主线/支线推进仲裁器（零 LLM，拍板 1 语义）。

    Args:
        project_dir: 小说项目目录。
        state_machine: 已 load() 的状态机；为 None 时内部创建（从磁盘读）。
        mainline_window: 决策窗口（章），默认 5。
        console: rich 控制台（用于初始化提示；推进打印由调用方负责）。
    """

    PLAN_FILE = ".state" / Path("mainline.json")
    _DEFAULT_PHASE_RATIO: dict[str, int] = {}  # 缺省不擅自填用户比例；由 --ratio 明确写入

    def __init__(
        self,
        project_dir: str | Path,
        state_machine: StateMachine | None = None,
        mainline_window: int = 5,
        console: Console | None = None,
        budget_planner: "BudgetPlanner | None" = None,
        replan_window: int | None = None,
    ) -> None:
        self.project_dir = Path(project_dir)
        self.state_machine = state_machine or StateMachine(self.project_dir)
        self.mainline_window = max(1, int(mainline_window))
        self.console = console or Console()
        self.budget_planner = budget_planner
        # 预算重规划窗口：默认与推进窗口一致（每 N 章重规划一次分线预算）
        self.replan_window = max(1, int(replan_window or mainline_window))
        self._plan_file = self.project_dir / ".state" / "mainline.json"

    # ------------------------------------------------------------------
    # 主编预算动态重规划（LLM 规划预算，本类只做确定性执行）
    # ------------------------------------------------------------------
    def replan_if_due(self) -> bool:
        """到重规划窗口时，调用注入的 BudgetPlanner 重新动态划分分线预算。

        预算由 LLM 主编依据当前进度与内容动态生成（写入 ``subline_share``），
        本类的推进裁决（:meth:`maybe_advance`）在下一窗口读到更新后的 cap。
        未注入 planner 或未到窗口 / 异常 → 静默返回 False，绝不影响写章（G3）。
        """
        if self.budget_planner is None:
            return False
        try:
            self.state_machine.load()
            progress = self.state_machine.progress or {}
            chapter = int(progress.get("total_written", 0)) + 1
            if chapter <= 1 or (chapter - 1) % self.replan_window != 0:
                return False
            ok = bool(self.budget_planner.plan())
            if ok:
                self.console.print(
                    f"[dim]主编预算已重规划，第 {chapter} 章起生效[/dim]"
                )
            return ok
        except Exception:  # noqa: BLE001 - 规划异常降级不阻断（G3 哲学）
            return False

    # ------------------------------------------------------------------
    # 预算计划（.state/mainline.json）
    # ------------------------------------------------------------------
    def load_plan(self) -> dict[str, Any]:
        """读主线预算计划；缺失则按体量均衡分账自动生成并落盘。"""
        if not self._plan_file.exists():
            plan = self._default_plan()
            try:
                self._plan_file.parent.mkdir(parents=True, exist_ok=True)
                self._plan_file.write_text(
                    json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                self.console.print(
                    f"[dim]MainlineOrchestrator：已生成预算 {self._plan_file.name} [/dim]"
                )
            except Exception:  # noqa: BLE001 - 写失败降级为内存计划，不阻断推进
                return plan
            return plan
        try:
            data = json.loads(self._plan_file.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("mainline.json 顶层非对象")
            return data
        except Exception:  # noqa: BLE001 - 读失败降级为空
            return {"subline_share": {}, "phase_ratio": {}, "horizon_chapters": None}

    def save_plan(self, plan: dict[str, Any]) -> None:
        """写主线预算计划（供 CLI init 用）。"""
        self._plan_file.parent.mkdir(parents=True, exist_ok=True)
        self._plan_file.write_text(
            json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def _default_plan(self) -> dict[str, Any]:
        """缺省预算：按体量均衡分账（expected_chapters / sublines 数，余数顺延）。"""
        from agent.core.story.setting_manager import SettingManager

        sublines = SettingManager(self.project_dir).list_sublines()
        expected = self._expected_chapters()
        share: dict[str, int] = {}
        if expected and sublines:
            base, rem = divmod(int(expected), len(sublines))
            for i, sid in enumerate(sublines):
                share[sid] = base + (1 if i < rem else 0)
        return {
            "version": 1,
            "horizon_chapters": expected,
            "phase_ratio": dict(self._DEFAULT_PHASE_RATIO),
            "subline_share": share,
        }

    def _expected_chapters(self) -> Optional[int]:
        """按 world.md 体量档位估算全书目标章数；缺世界设定则 None。"""
        try:
            from agent.core.story.setting_manager import SettingManager

            md = SettingManager(self.project_dir).load_world().get("metadata", {}) or {}
            scope = md.get("scope") or "medium"
            total_words = md.get("scope_total_words")
            cl = md.get("scope_chapter_length") or md.get("chapter_length") or 3000
            from agent.core.story.volume import estimate_chapters

            return int(estimate_chapters(scope, total_words, cl))
        except Exception:  # noqa: BLE001 - 估算失败降级 None
            return None

    # ------------------------------------------------------------------
    # 唯一裁决入口
    # ------------------------------------------------------------------
    def maybe_advance(self) -> Optional[str]:
        """执行一次推进裁决并落盘。

        - 每 ``mainline_window`` 章执行一次（第 1 章前不裁决）。
        - 预算上界 = ``min(曲线/episode 多源上界, subline_share 预算 cap)``。
        - 越过上界且非最后一条 → 切到下一支线并写 ``current_subline`` + ``mainline_visited`` 落盘。

        Returns:
            切到的目标 subline_id；不切换返回 None。
        """
        self.state_machine.load()
        progress = self.state_machine.progress or {}
        chapter = int(progress.get("total_written", 0)) + 1
        current = str(progress.get("current_subline", "") or "")
        if chapter <= 1 or (chapter - 1) % self.mainline_window != 0:
            return None
        cap = self._cap_for(current)
        from agent.workflows.mainline import decide_mainline_advance

        new_subline = decide_mainline_advance(
            self.project_dir, self.state_machine, self.mainline_window, cap=cap
        )
        if not new_subline:
            return None
        next_progress = dict(progress)
        next_progress["current_subline"] = new_subline
        visited = list(next_progress.get("mainline_visited", []) or [])
        if new_subline not in visited:
            visited.append(new_subline)
        next_progress["mainline_visited"] = visited
        self.state_machine.progress = next_progress
        self.state_machine.save()
        return new_subline

    def _cap_for(self, subline_id: str) -> Optional[int]:
        """取该支线的预算硬上界；无预算返回 None（不设 cap）。"""
        plan = self.load_plan()
        share = plan.get("subline_share", {}) or {}
        try:
            cap = int(share.get(subline_id) or 0)
        except (TypeError, ValueError):
            return None
        return cap if cap > 0 else None