"""状态所有权红线（架构 §3.7「项目状态的唯一读写门」）

现状（2026-09-12 实测）：``state.json`` 被 cli / core / workflows / web **四层
24 个文件**直接引用。架构文档称 ``web/state.py`` 是「项目状态唯一读写门」，
但它实际只是**接入层的适配**——cli 与 workflows 各自直写，所谓「唯一」名不副实。

为什么不一次性迁移：state.json 是运行中的小说项目的活状态，全量改写入口
风险过高（涉及正在续写的长篇任务）。故采用**棘轮**策略：

1. 先显式声明状态所有权者（合法直写）；
2. 其余层的直写点冻结在当前基线，只减不增；
3. 逐批把 cli / workflows 的直写收敛到 ``core/engine/state_machine.py``，
   每清偿一个文件就下调对应预算。

目标终态：``core/engine/state_machine.py`` 是 state.json 的唯一所有权者，
``web/state.py`` 是其在接入层的适配，其余层一律经所有权者访问。
"""

from __future__ import annotations

from pathlib import Path

AGENT_SRC = Path(__file__).resolve().parents[2] / "src" / "agent"

# 状态所有权者与必要工具：允许直接触碰 state.json
STATE_OWNERS = {
    "core/engine/state_machine.py",  # 状态机：state.json 的唯一所有权者
    "core/infra/atomic.py",  # 原子写工具（被所有权者调用）
    "core/infra/doctor.py",  # 健康诊断（只读 + 修复建议）
    "core/infra/dashboard_aggregator.py",  # 仪表盘聚合（只读投影）
    "core/engine/events.py",  # 进度事件落盘
    "core/story/injected_trope_store.py",  # 注入梗独立存储
    "web/state.py",  # 接入层适配（§3.7 项目状态读写门）
}

# 待收敛层基线（文件 -> state.json 引用次数）：冻结，只减不增
CONVERGE_BASELINE = {
    "workflows/pipeline/plan_consistency.py": 3,
    "workflows/evaluation/m18_recovery.py": 3,
    "workflows/writing/m8_mode.py": 2,
    "workflows/pipeline/agentic_pipeline_ending.py": 2,
    "workflows/writing/m5_write_chapter.py": 1,
    "workflows/writing/m5_persist.py": 1,
    "workflows/pipeline/budget_planner.py": 1,
    "workflows/evaluation/m10_rollback.py": 1,
    "cli/commands/status.py": 2,
    "cli/_shared.py": 2,
    "cli/commands/setup.py": 1,
    "cli/commands/rollback.py": 1,
    "cli/commands/resume.py": 1,
    "cli/commands/reset_state.py": 1,
    "cli/commands/inject_genre.py": 1,
    "cli/commands/doctor.py": 1,
    "cli/commands/cmd_list.py": 1,
}


def _state_json_hits() -> dict[str, int]:
    """统计每个源文件中 state.json 字面量的出现次数。"""
    counts: dict[str, int] = {}
    for py in sorted(AGENT_SRC.rglob("*.py")):
        try:
            text = py.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        n = text.count("state.json")
        if n:
            counts[py.relative_to(AGENT_SRC).as_posix()] = n
    return counts


class TestStateOwnership:
    def test_no_new_direct_state_writers(self) -> None:
        """禁止新增直写 state.json 的文件（所有权者除外）。"""
        counts = _state_json_hits()
        violations = [
            f"  {f}: {n} 处（既非所有权者，也不在收敛基线内）"
            for f, n in sorted(counts.items())
            if f not in STATE_OWNERS and f not in CONVERGE_BASELINE
        ]
        assert not violations, (
            "新增了直写 state.json 的文件——状态应经 core/engine/state_machine.py 访问，"
            "不要另开门户：\n" + "\n".join(violations)
        )

    def test_converge_budget_never_grows(self) -> None:
        """棘轮：待收敛层的 state.json 引用次数只减不增。"""
        counts = _state_json_hits()
        violations = [
            f"  {f}: 当前 {counts.get(f, 0)} > 基线 {budget}"
            for f, budget in CONVERGE_BASELINE.items()
            if counts.get(f, 0) > budget
        ]
        assert not violations, (
            "待收敛层的 state.json 直写增加（应逐步收敛到状态所有权者）：\n"
            + "\n".join(violations)
        )
