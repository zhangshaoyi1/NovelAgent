"""检查点升级共享出口（登记单 20261004_规划质量分层守卫·件 1）。

背景
----
规划闸门（``core/plan_gate``）revise 超限 / 评审不可用时也要"挂起交人工"，
与计划审稿人（``agents/plan_reviewer``）的升级动作同型：写 ``.state/checkpoint.json``
原子卡 + 状态机 WRITING→AWAITING_CHECKPOINT（可穿越重启，RESUME 恢复）。
此前该逻辑只存在于 agents 层，core 不能反向 import agents（R6）——
故把"升级检查点"这个纯 core 动作下沉为本模块，agents 层改薄封装。

失败语义（G3）：卡片落盘失败 / 状态迁移失败均 degrade 留痕**不抛**——
升级动作自身不得因基础设施抖动把调用方炸掉；调用方随后自行决定业务语义
（拒写 / 挂起等）。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from agent.core.infra.degrade import degrade

__all__ = ["raise_checkpoint"]


def raise_checkpoint(
    project_dir: str | Path,
    *,
    pause_reason: str,
    risks: list[str] | None = None,
    actions: list[str] | None = None,
    console: Any = None,
) -> None:
    """升级检查点：写检查点卡 + 状态迁移 WRITING→AWAITING_CHECKPOINT。

    Args:
        project_dir: 小说项目根目录。
        pause_reason: 挂起原因（写入卡片 ``pause_reason``）。
        risks: 风险/分歧描述列表（逐条写入卡片）。
        actions: 建议处置动作列表（checkpoint-continue / adjust-route 等）。
        console: 可选 rich console，用于回显挂起提示。

    失败不抛（degrade 留痕）：卡片写失败或状态迁移失败只留痕，
    由调用方决定后续业务语义。
    """
    project_path = Path(project_dir)
    card = {
        "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "batch_range": [],
        "chapters_written": 0,
        "low_confidence_delivery": False,
        "paused": True,
        "pause_reason": pause_reason,
        "risks": [f"{r}"[:300] for r in (risks or [])],
        "next_plan": {},
        "actions": list(actions or []),
    }
    try:
        p = project_path / ".state" / "checkpoint.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(card, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(p)
    except Exception as e:  # noqa: BLE001 - 升级卡落盘失败不阻断调用方业务语义
        degrade("engine.checkpoint", "检查点卡片落盘失败", e)
    try:
        from agent.core.engine.state_machine import Event, State, StateMachine

        sm = StateMachine(project_path)
        sm.load()
        if sm.state is State.WRITING:
            sm.transition(Event.ENTER_CHECKPOINT)
    except Exception as e:  # noqa: BLE001 - 状态迁移失败不阻断调用方业务语义
        degrade("engine.checkpoint", "检查点状态迁移失败", e)
    if console is not None:
        try:
            console.print(
                f"[red]✗ 已挂起等作者裁决：{str(pause_reason)[:200]}[/red]"
            )
        except Exception:  # noqa: SILENT_DEGRADE reason=logging-only - 打印失败不影响挂起
            pass
