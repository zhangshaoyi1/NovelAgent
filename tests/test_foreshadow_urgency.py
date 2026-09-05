"""P1-8 伏笔提前预警 / 紧急度三级（m13_foreshadow.foreshadow_urgency + m5 上下文注入）。

覆盖：
- 纯函数 foreshadow_urgency：normal / due（预警窗口内）/ overdue / 边界
  （恰在窗口边缘、恰到期、已回收不参与、无法解析回收点）；
- Foreshadow.urgency 方法委托；
- compute_stats 的 due 计数与 M13Report.due 清单、报告渲染"即将到期"分节；
- m5_context._load_foreshadow_task 注入"即将到期/逾期"提醒行（含上限与未埋不提醒）。
"""

from __future__ import annotations

from pathlib import Path

from agent.workflows.evaluation.m13_foreshadow import (
    REMIND_BEFORE_CHAPTERS,
    Foreshadow,
    M13ForeshadowWorkflow,
    foreshadow_urgency,
)
from agent.workflows.writing.m5_context import M5ContextMixin


# ---------------------------------------------------------------- 纯函数
def test_urgency_normal_when_far_from_expected() -> None:
    assert foreshadow_urgency("已埋", "S03/E02/ch068", 50) == "normal"


def test_urgency_due_inside_window() -> None:
    # 预期 ch068，当前 65 → 恰好进入 3 章预警窗口
    assert foreshadow_urgency("已埋", "S03/E02/ch068", 68 - REMIND_BEFORE_CHAPTERS) == "due"
    assert foreshadow_urgency("已埋", "S03/E02/ch068", 67) == "due"


def test_urgency_overdue_after_expected() -> None:
    assert foreshadow_urgency("已埋", "S03/E02/ch068", 69) == "overdue"


def test_urgency_boundaries() -> None:
    # 恰到期当章 = due（未过点）；窗口前一章 = normal
    assert foreshadow_urgency("已埋", "ch100", 100) == "due"
    assert foreshadow_urgency("已埋", "ch100", 100 - REMIND_BEFORE_CHAPTERS - 1) == "normal"


def test_urgency_ignores_resolved_and_unparseable() -> None:
    assert foreshadow_urgency("已回收", "ch010", 50) == "normal"
    assert foreshadow_urgency("已废弃", "ch010", 50) == "normal"
    assert foreshadow_urgency("未埋", "S04/E01（无章节号）", 50) == "normal"
    assert foreshadow_urgency("已埋", "", 50) == "normal"


def test_foreshadow_method_delegates() -> None:
    f = Foreshadow(
        fid="F-01", content="测试", planted_at="ch001",
        expected_resolve="ch010", state="已埋", related_characters="",
    )
    assert f.urgency(5) == "normal"
    assert f.urgency(8) == "due"
    assert f.urgency(11) == "overdue"


# ---------------------------------------------------------------- 统计与报告
def test_stats_counts_due_and_report_renders(tmp_path: Path) -> None:
    md = """# 伏笔登记表

| ID | 伏笔内容 | 埋设位置 | 预期回收点 | 状态 | 关联角色 |
|---|---|---|---|---|---|
| F-01 | 快到期 | ch001 | ch060 | 已埋 | 甲 |
| F-02 | 已逾期 | ch001 | ch040 | 已埋 | 乙 |
| F-03 | 还早 | ch001 | ch200 | 已埋 | 丙 |
"""
    d = tmp_path / "p"
    d.mkdir()
    (d / "foreshadows.md").write_text(md, encoding="utf-8")
    from agent.core.engine.state_machine import State, StateMachine

    sm = StateMachine(d)
    sm.load()
    sm.state = State.WRITING
    sm.progress = {"total_written": 58}
    sm.save()

    wf = M13ForeshadowWorkflow(project_dir=d)
    items = wf.load_foreshadows()
    stats = wf.compute_stats(items, current_chapter=58)
    assert stats.due == 1  # F-01（ch060 距 58 仅 2 章）
    assert stats.overdue == 1  # F-02

    report = wf.generate_completion_report()
    assert report.due[0].fid == "F-01"
    content = report.report_file.read_text(encoding="utf-8")
    assert "即将到期伏笔" in content
    assert "F-01" in content
    assert "预警" in content
    # F-03（还早）不出现在到期分节
    assert content.index("F-01") < content.index("F-03")


# ---------------------------------------------------------------- m5 上下文注入
def _build_project_with_foreshadow(tmp_path: Path, table: str) -> Path:
    d = tmp_path / "p"
    d.mkdir()
    (d / "foreshadows.md").write_text(table, encoding="utf-8")
    return d


class _Host(M5ContextMixin):
    """M5ContextMixin 是 Mixin，测试用最小宿主类直接驱动。"""

    def __init__(self, project_dir: Path) -> None:
        self.project_dir = project_dir


def test_m5_context_injects_due_reminder(tmp_path: Path) -> None:
    table = """# 伏笔登记表

| ID | 伏笔内容 | 埋设位置 | 预期回收点 | 状态 | 关联角色 |
|---|---|---|---|---|---|
| F-01 | 快到期 | ch001 | ch060 | 已埋 | 甲 |
| F-02 | 还早 | ch001 | ch200 | 已埋 | 乙 |
"""
    d = _build_project_with_foreshadow(tmp_path, table)
    host = _Host(d)
    task = host._load_foreshadow_task({"total_written": 58})
    assert "⏳" in task and "F-01" in task
    assert "F-02" not in task  # 未进窗口不提醒


def test_m5_context_injects_overdue_reminder(tmp_path: Path) -> None:
    table = """# 伏笔登记表

| ID | 伏笔内容 | 埋设位置 | 预期回收点 | 状态 | 关联角色 |
|---|---|---|---|---|---|
| F-09 | 已逾期 | ch001 | ch040 | 已埋 | 甲 |
"""
    d = _build_project_with_foreshadow(tmp_path, table)
    host = _Host(d)
    task = host._load_foreshadow_task({"total_written": 50})
    assert "⚠" in task and "F-09" in task and "已逾期" in task


def test_m5_context_caps_reminders_and_skips_unplanted(tmp_path: Path) -> None:
    rows = "\n".join(
        f"| F-{i:02d} | 到期伏笔{i} | ch001 | ch05{i} | 已埋 | 甲 |"
        for i in range(1, 6)  # 5 条全部进入预警窗口（当前 51 章，预期 ch051-ch055）
    )
    table = (
        "# 伏笔登记表\n\n"
        "| ID | 伏笔内容 | 埋设位置 | 预期回收点 | 状态 | 关联角色 |\n"
        "|---|---|---|---|---|---|\n" + rows + "\n"
    )
    d = _build_project_with_foreshadow(tmp_path, table)
    host = _Host(d)
    task = host._load_foreshadow_task({"total_written": 50})
    assert task.count("⏳") == 3  # 最多 3 条提醒（防 prompt 膨胀）


def test_m5_context_no_reminder_when_all_normal(tmp_path: Path) -> None:
    table = """# 伏笔登记表

| ID | 伏笔内容 | 埋设位置 | 预期回收点 | 状态 | 关联角色 |
|---|---|---|---|---|---|
| F-01 | 还早 | ch001 | ch200 | 已埋 | 甲 |
| F-02 | 未埋 | ch050 | ch080 | 未埋 | 乙 |
"""
    d = _build_project_with_foreshadow(tmp_path, table)
    host = _Host(d)
    task = host._load_foreshadow_task({"total_written": 50})
    assert "⏳" not in task and "⚠" not in task
