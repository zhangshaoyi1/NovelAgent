"""状态机引擎（M17）

自实现轻量状态机：状态枚举 + 转换表 + 持久化 + 门禁查询接口。

状态枚举（见 PRD M17）：
    INIT → CONFIGURING → DISCUSSING → ARCHITECTING → ARCH_CONFIRMED
         → OUTLINING → CHARACTER_DESIGN → WRITING ⇄ PAUSED
         → (ARCH_REVISION → ARCH_CONFIRMED) → COMPLETED
"""

from __future__ import annotations

import json
from enum import Enum
from pathlib import Path
from typing import Any


class State(str, Enum):
    """Agent 状态枚举"""

    INIT = "INIT"
    CONFIGURING = "CONFIGURING"
    DISCUSSING = "DISCUSSING"
    ARCHITECTING = "ARCHITECTING"
    ARCH_CONFIRMED = "ARCH_CONFIRMED"
    ARCH_REVISION = "ARCH_REVISION"
    OUTLINING = "OUTLINING"
    CHARACTER_DESIGN = "CHARACTER_DESIGN"
    WRITING = "WRITING"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"


# 事件枚举（部分示例，后续扩展）
class Event(str, Enum):
    START = "START"
    DISCUSS = "DISCUSS"
    GENERATE_ARCHITECTURE = "GENERATE_ARCHITECTURE"
    CONFIRM_ARCHITECTURE = "CONFIRM_ARCHITECTURE"
    REVISE_ARCHITECTURE = "REVISE_ARCHITECTURE"
    GENERATE_OUTLINE = "GENERATE_OUTLINE"
    DESIGN_CHARACTERS = "DESIGN_CHARACTERS"
    WRITE = "WRITE"
    PAUSE = "PAUSE"
    RESUME = "RESUME"
    COMPLETE = "COMPLETE"


# ============================================================
# 单一状态转换表（T-6：状态机唯一真相源，替代分散的状态-命令映射表）
# ============================================================
TRANSITIONS: dict[tuple[State, Event], State] = {
    (State.INIT, Event.START): State.CONFIGURING,
    (State.CONFIGURING, Event.DISCUSS): State.DISCUSSING,
    (State.DISCUSSING, Event.GENERATE_ARCHITECTURE): State.ARCHITECTING,
    (State.ARCHITECTING, Event.CONFIRM_ARCHITECTURE): State.ARCH_CONFIRMED,
    (State.ARCH_CONFIRMED, Event.GENERATE_OUTLINE): State.OUTLINING,
    (State.ARCH_CONFIRMED, Event.REVISE_ARCHITECTURE): State.ARCH_REVISION,
    (State.ARCH_REVISION, Event.CONFIRM_ARCHITECTURE): State.ARCH_CONFIRMED,
    (State.OUTLINING, Event.DESIGN_CHARACTERS): State.CHARACTER_DESIGN,
    (State.CHARACTER_DESIGN, Event.WRITE): State.WRITING,
    (State.WRITING, Event.PAUSE): State.PAUSED,
    (State.PAUSED, Event.RESUME): State.WRITING,
    (State.WRITING, Event.COMPLETE): State.COMPLETED,
}


class StateMachine:
    """状态机引擎

    职责：
        - 持有当前状态
        - 校验命令在该状态下是否可用（门禁）
        - 持久化到 .state/state.json
    """

    def __init__(self, project_dir: Path) -> None:
        self.project_dir = project_dir
        self.state_file = project_dir / ".state" / "state.json"
        self.state: State = State.INIT
        self.progress: dict[str, Any] = {}
        self.mode: str = "heavy"  # heavy | light | auto

    # ------ 持久化 ------
    def load(self) -> None:
        """从磁盘加载状态"""
        if not self.state_file.exists():
            self.state = State.INIT
            return
        data = json.loads(self.state_file.read_text(encoding="utf-8"))
        raw = data.get("state", State.INIT)
        try:
            self.state = State(raw)
        except ValueError:
            # 防御：state.json 中存在非法/笔误状态值（如历史数据 "COMPLETE"）
            # 不阻断 Web / CLI，降级为 INIT 并保留原始值供排查。
            print(
                f"[state_machine] 警告：{self.state_file} 的状态值 "
                f"{raw!r} 非法，已降级为 INIT"
            )
            self.state = State.INIT
        self.progress = data.get("progress", {})
        self.mode = data.get("mode", "heavy")

    def save(self) -> None:
        """原子写入状态文件"""
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "state": self.state.value,
            "progress": self.progress,
            "mode": self.mode,
        }
        tmp = self.state_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.state_file)

    # ------ 写章失败记录（Phase 5 巡检自愈：区分系统异常 vs 等待用户决策）------
    def record_write_error(self, code: str, message: str) -> None:
        """记录最近一次写章失败，供自动化巡检判定停止类型（系统异常/等待决策）。

        写入 progress.last_error = {"code", "message", "at"}；成功写章后由写章
        工作流调用 clear_write_error() 清除。仅持久化必要字段，不污染正常进度。
        """
        if "progress" not in self.progress or not isinstance(self.progress.get("progress"), dict):
            # progress 本身即 dict（见 load），这里防御性兜底
            if not isinstance(self.progress, dict):
                self.progress = {}
        from datetime import datetime

        self.progress["last_error"] = {
            "code": code,
            "message": (message or "")[:300],
            "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        self.save()

    def clear_write_error(self) -> None:
        """写章成功后清除 last_error 与连续失败计数（非阻断，字段缺失/异常均静默）。"""
        try:
            self.progress.pop("last_error", None)
            self.progress["consecutive_write_failures"] = 0
            self.save()
        except Exception:  # noqa: BLE001 - 清除失败不阻断主流程
            pass

    def bump_write_failure(self) -> int:
        """写章失败时累加连续失败计数，返回累加后的值（供巡检判定连续 2 次告警）。

        与 record_write_error 配套：每次写章失败 +1，成功写章（clear_write_error）
        归零。巡检读到 progress.consecutive_write_failures >= 2 即触发告警。
        """
        try:
            n = int(self.progress.get("consecutive_write_failures", 0) or 0) + 1
            self.progress["consecutive_write_failures"] = n
            self.save()
            return n
        except Exception:  # noqa: BLE001 - 计数失败不阻断
            return 0

    # ------ 门禁查询（T-6：门禁改由命令元数据派生）------
    def is_command_allowed(self, command: str) -> bool:
        """查询命令在当前状态下是否可用（基于 CommandMeta.allowed_states/is_global）"""
        from agent.core.command_router import command_allowed_in_state

        return command_allowed_in_state(command, self.state)

    def allowed_commands(self) -> list[str]:
        """当前状态下所有可用命令（基于命令元数据）"""
        from agent.core.command_router import commands_for_state

        return [m.name for m in commands_for_state(self.state.value)]

    # ------ 模式切换（M8）------
    VALID_MODES = {"heavy", "light", "auto"}

    def set_mode(self, mode: str) -> None:
        """切换介入频率模式（M8）

        Args:
            mode: heavy / light / auto

        Raises:
            ValueError: 非法模式
        """
        if mode not in self.VALID_MODES:
            raise ValueError(f"非法模式：{mode}，可选值：{sorted(self.VALID_MODES)}")
        self.mode = mode
        self.save()

    # ------ 状态转换（T-6：使用单一 TRANSITIONS 表）------
    def transition(self, event: Event) -> None:
        """根据事件转换状态（基于单一 TRANSITIONS 表）

        Args:
            event: 触发的事件

        Raises:
            ValueError: 非法转换（当前状态+事件不在 TRANSITIONS 中）
        """
        key = (self.state, event)
        if key not in TRANSITIONS:
            raise ValueError(f"非法状态转换: {self.state} + {event}")
        self.state = TRANSITIONS[key]
        self.save()
