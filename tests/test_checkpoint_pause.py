"""批次边界作者检查点红线（登记单 20261001_信任链与叙事上限六项能力·子项 3）。

守卫四条验收标准的机制层：
- **V3.1 挂起与放行**：HEAVY 批次结束进入 ``AWAITING_CHECKPOINT``（可穿越重启：
  状态与卡片均落盘），``checkpoint-continue`` 对应的 RESUME 转换存在。
- **V3.2 三挡语义**：HEAVY 每批必停 / LIGHT 仅风险停 / AUTO 从不停；
  escalated/tripped/blocked 批次不进检查点（不被稀释）。
- **V3.3 卡片信息**：卡片包含风险信号、低置信标记、下批计划要点、四动作提示，
  且每批都落盘（AUTO 挡只写卡不停，可见性不缺位）。
- **V3.4 门禁语义**：挂起态下 autowrite 被命令门禁拦截（防外层盲写），
  而 rewrite/rollback/adjust-route/checkpoint-continue 可用；
  PAUSE/stop 语义与检查点互不干扰（PAUSED 状态不含 RESUME→检查点路径）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import agent.cli.commands  # noqa: F401,E402 - 触发 @command 注册副作用（命令门禁元数据）  # noqa: E401
from agent.core.engine import command_router
from agent.core.engine.state_machine import Event, State, StateMachine, TRANSITIONS
from agent.workflows.pipeline.agentic_pipeline_events import (
    GATE_BLIND_FILE,
    _PipelineEventsMixin,
)
from agent.workflows.writing.m8_mode import checkpoint_pause_decision


# ---------------------------------------------------------------- V3.2 三挡语义
def test_tier_heavy_always_pauses() -> None:
    for risk in (True, False):
        pause, reason = checkpoint_pause_decision(20, risk)
        assert pause and "每批必停" in reason


def test_tier_light_pauses_only_on_risk() -> None:
    pause, reason = checkpoint_pause_decision(55, True)
    assert pause and "风险" in reason
    pause, _ = checkpoint_pause_decision(55, False)
    assert not pause


def test_tier_auto_never_pauses() -> None:
    pause, _ = checkpoint_pause_decision(100, True)
    assert not pause
    pause, _ = checkpoint_pause_decision(90, True)
    assert not pause


# ---------------------------------------------------------------- 状态机
def test_state_transitions_enter_and_resume_checkpoint() -> None:
    assert (State.WRITING, Event.ENTER_CHECKPOINT) in TRANSITIONS
    assert TRANSITIONS[(State.WRITING, Event.ENTER_CHECKPOINT)] is State.AWAITING_CHECKPOINT
    assert TRANSITIONS[(State.AWAITING_CHECKPOINT, Event.RESUME)] is State.WRITING


def test_state_machine_roundtrip_survives_reload(tmp_path) -> None:
    """挂起态落盘可穿越重启（V3.1 重启恢复的机制基础）。"""
    sm = StateMachine(tmp_path)
    sm.state = State.WRITING
    sm.save()
    sm2 = StateMachine(tmp_path)
    sm2.load()
    assert sm2.state is State.WRITING
    sm2.transition(Event.ENTER_CHECKPOINT)
    sm3 = StateMachine(tmp_path)
    sm3.load()
    assert sm3.state is State.AWAITING_CHECKPOINT
    sm3.transition(Event.RESUME)
    sm4 = StateMachine(tmp_path)
    sm4.load()
    assert sm4.state is State.WRITING


# ---------------------------------------------------------------- V3.4 命令门禁
def test_autowrite_blocked_in_checkpoint_state() -> None:
    assert not command_router.command_allowed_in_state("/autowrite", State.AWAITING_CHECKPOINT)
    assert command_router.command_allowed_in_state("/autowrite", State.WRITING)


def test_checkpoint_actions_allowed_in_checkpoint_state() -> None:
    for cmd in (
        "/checkpoint",
        "/checkpoint-continue",
        "/rewrite",
        "/rollback",
        "/adjust-route",
        "/adjust-relation",
        "/appeal",
        "/evaluate",
    ):
        assert command_router.command_allowed_in_state(cmd, State.AWAITING_CHECKPOINT), cmd


def test_pause_semantics_untangled() -> None:
    """PAUSED 与 AWAITING_CHECKPOINT 互不混淆：PAUSED 不进检查点，检查点不是 PAUSE。"""
    assert (State.PAUSED, Event.ENTER_CHECKPOINT) not in TRANSITIONS
    assert (State.AWAITING_CHECKPOINT, Event.PAUSE) not in TRANSITIONS


# ---------------------------------------------------------------- 批末检查点行为
class _Console:
    def print(self, *args, **kwargs):  # noqa: D102
        pass


class _SM:
    """状态机桩：记录 autonomy 与转换调用。"""

    def __init__(self, autonomy: int = 70):
        self.autonomy_level = autonomy
        self.state = State.WRITING
        self.transitions: list[Event] = []

    def load(self) -> None:
        pass

    def get_autonomy_level(self) -> int:
        return int(self.autonomy_level or 0)

    def transition(self, ev: Event) -> None:
        self.transitions.append(ev)
        self.state = TRANSITIONS[(self.state, ev)]


class _Result:
    def __init__(self, chapters_written=5, final_chapter=5, escalated=False, tripped=False, blocked=False):
        self.chapters_written = chapters_written
        self.final_chapter = final_chapter
        self.escalated = escalated
        self.tripped = tripped
        self.blocked = blocked
        self.failures: list[dict] = []
        self.checkpoint = None


class _Harness(_PipelineEventsMixin):
    def __init__(self, project_dir: Path, autonomy: int = 70):
        self.project_dir = project_dir
        self.console = _Console()
        self.state_machine = _SM(autonomy)
        self._failures: list = []
        self._gate_escalation_reason = ""

    def _emit_event(self, type_, **fields):  # noqa: D102
        self._failures.append((type_, fields))

    def _emit_failure(self, *a, **k):  # noqa: D102
        pass


def test_heavy_batch_pauses_with_card(tmp_path) -> None:
    h = _Harness(tmp_path, autonomy=20)
    r = _Result()
    h._finalize_checkpoint(r)
    card = json.loads((tmp_path / ".state" / "checkpoint.json").read_text(encoding="utf-8"))
    assert card["paused"] is True
    assert card["batch_range"] == [1, 5]
    assert card["actions"] and len(card["actions"]) == 4
    assert r.checkpoint is not None
    assert h.state_machine.transitions == [Event.ENTER_CHECKPOINT]


def test_auto_batch_writes_card_but_never_pauses(tmp_path) -> None:
    h = _Harness(tmp_path, autonomy=100)
    r = _Result()
    h._finalize_checkpoint(r)
    card = json.loads((tmp_path / ".state" / "checkpoint.json").read_text(encoding="utf-8"))
    assert card["paused"] is False
    assert h.state_machine.transitions == []


def test_light_pauses_on_risk_only(tmp_path) -> None:
    # 有风险（低置信交付）→ 挂起
    (tmp_path / ".state").mkdir(parents=True, exist_ok=True)
    (tmp_path / GATE_BLIND_FILE).write_text(
        json.dumps({"low_confidence_delivery": True}), encoding="utf-8"
    )
    h = _Harness(tmp_path, autonomy=55)
    r = _Result()
    h._finalize_checkpoint(r)
    card = json.loads((tmp_path / ".state" / "checkpoint.json").read_text(encoding="utf-8"))
    assert card["paused"] is True
    assert card["low_confidence_delivery"] is True
    assert any("低置信" in x for x in card["risks"])
    # 无风险 → 不挂起
    (tmp_path / GATE_BLIND_FILE).write_text("{}", encoding="utf-8")
    h2 = _Harness(tmp_path, autonomy=55)
    r2 = _Result()
    h2._finalize_checkpoint(r2)
    card2 = json.loads((tmp_path / ".state" / "checkpoint.json").read_text(encoding="utf-8"))
    assert card2["paused"] is False


def test_escalated_batch_skips_checkpoint(tmp_path) -> None:
    h = _Harness(tmp_path, autonomy=20)  # HEAVY 也不得挂起
    r = _Result(escalated=True)
    h._finalize_checkpoint(r)
    assert not (tmp_path / ".state" / "checkpoint.json").exists()
    assert r.checkpoint is None
    assert h.state_machine.transitions == []


def test_zero_chapters_skips_checkpoint(tmp_path) -> None:
    h = _Harness(tmp_path, autonomy=20)
    r = _Result(chapters_written=0, final_chapter=0)
    h._finalize_checkpoint(r)
    assert not (tmp_path / ".state" / "checkpoint.json").exists()


# ---------------------------------------------------------------- V3.3 卡片信息
def test_card_contains_next_plan_notes(tmp_path) -> None:
    (tmp_path / ".state").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".state" / "batch_reflection.json").write_text(
        json.dumps({
            "latest": {
                "summary": "下批开复仇支线，第 8 章埋高潮钩子",
                "items": [{"action": "开线 A", "cause": "上一批支线预算未用", "phenomenon": "", "verified": False}],
            }
        }),
        encoding="utf-8",
    )
    h = _Harness(tmp_path, autonomy=100)
    r = _Result()
    h._finalize_checkpoint(r)
    card = json.loads((tmp_path / ".state" / "checkpoint.json").read_text(encoding="utf-8"))
    assert "复仇支线" in card["next_plan"]["notes"]
    assert card["next_plan"]["actions"] == ["开线 A"]
