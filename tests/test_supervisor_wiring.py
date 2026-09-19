"""红线 · M26 Supervisor 接线（2026-09-19，修复 SupervisorEngine 零消费点假把关者）

**事故**：``SupervisorEngine`` + 4 个内置 checker（``dimensions.py``）自实现以来
**全仓零调用点**——``SupervisorRegistry.register`` 无任何调用、``check_all`` 永不
执行；而同时：

- ``core/quality/policy.py:16`` 把 Supervisor 列进 review 层清单（批次/手动）；
- ``web/quality_admin.py`` 声称「监督体系 active: 事件驱动 M26」。

⇒ 双重「假把关者」（纪律 #7 家族：清单/界面声称的能力必须真实可达）。
注：``test_plan_review_reachability`` D3 只扫**模块级函数**、不扫类，
故类形态的孤儿此前对红线不可见 —— 本文件补上类装配的接线判据。

**接线（三端可达，纪律 #8）**：

====================  ==========================================================
1 引擎装配            ``create_default_engine()`` 唯一默认装配点（恰 4 内置插件）
2 批末自动            ``_PipelineEventsMixin._run_supervisor_batch_end``（advisory）
3 CLI 手动            ``agent supervisor-check --json``
====================  ==========================================================

判据形态：① 装配成员契约 ② 批末步骤**行为级**（supervisor.alert 事件真落盘
+ memory 真留痕 —— 纪律 #10：只断言「函数被调用」会漏掉静默截断）
③ 插件崩溃不阻断批末（显性降级）④ ``run()`` 调用点在位（辅助源码断言）
⑤ CLI --json 信封契约 + 空项目健康通过。
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from rich.console import Console
from typer.testing import CliRunner

from agent.cli import app
from agent.core.event_sourcing.event_bus import EventBus
from agent.core.supervisor.supervisor import (
    SupervisorEngine,
    SupervisorPlugin,
    create_default_engine,
)
from agent.workflows.pipeline.agentic_pipeline_events import _PipelineEventsMixin

_FILLER = "饭后两人闲聊了几句，又各自回屋休息，把白天的事想了想。\n" * 40


# ---------------------------------------------------------------- 装配契约
def test_default_engine_registers_exactly_four_builtin_plugins(tmp_path: Path) -> None:
    """① 唯一默认装配点必须注册**恰** 4 个内置 checker（成员契约，多/少都算缺陷）。"""
    engine = create_default_engine(str(tmp_path))
    names = sorted(p.name for p in engine.registry.all())
    assert names == ["language_guard", "plot_progress", "style_drift", "trope_payoff"]


def test_default_engine_check_all_returns_report(tmp_path: Path) -> None:
    """装配后 ``check_all`` 真的可产出报告（不是恒空转的摆设引擎）。"""
    ch = tmp_path / "chapters"
    ch.mkdir()
    for i in range(1, 11):
        (ch / f"ch{i:03d}.md").write_text(_FILLER, encoding="utf-8")

    engine = create_default_engine(str(tmp_path))
    report = engine.check_all(10)
    assert any(i.dimension == "plot_progress" for i in report.issues), (
        "10 章 filler 文本必须触发情节推进告警（checker 真在跑）"
    )
    assert report.healthy is True, "warning 不算 critical（advisory 语义）"


# ---------------------------------------------------------------- 批末步骤（行为级）
class _MemoryStub:
    def __init__(self) -> None:
        self.entries: list[tuple] = []

    def log(self, ns: str, msg: str, data: object = None) -> None:
        self.entries.append((ns, msg, data))


class _Workflow(_PipelineEventsMixin):
    """最小宿主：批末步骤只依赖 project_dir / console / memory。"""

    def __init__(self, project_dir: Path) -> None:
        self.project_dir = project_dir
        self.console = Console()
        self.memory = _MemoryStub()


@pytest.fixture
def event_bus(tmp_path: Path):
    """EventBus 单例指向 tmp 项目（测后重置，防污染其它用例）。"""
    EventBus.reset_instance()
    bus = EventBus.get_instance()
    bus.configure(str(tmp_path))
    yield bus
    EventBus.reset_instance()


def _make_project(tmp_path: Path, n: int = 10) -> None:
    ch = tmp_path / "chapters"
    ch.mkdir(exist_ok=True)
    for i in range(1, n + 1):
        (ch / f"ch{i:03d}.md").write_text(_FILLER, encoding="utf-8")


def test_batch_end_step_emits_alert_and_memory_log(tmp_path: Path, event_bus) -> None:
    """② 行为级：批末监督后 ``supervisor.alert`` **真落盘** + memory **真留痕**。"""
    _make_project(tmp_path)
    wf = _Workflow(tmp_path)
    wf._run_supervisor_batch_end(SimpleNamespace(final_chapter=10, chapters_written=10))

    ns_entries = [e for e in wf.memory.entries if e[0] == "supervisor"]
    assert ns_entries, "批末监督必须落 memory（留痕不是可选项）"
    data = ns_entries[0][2]
    assert isinstance(data, dict) and "issues" in data
    assert any(i["dimension"] == "plot_progress" for i in data["issues"])

    ev_file = tmp_path / ".events" / "events.jsonl"
    assert ev_file.exists(), "supervisor.alert 事件必须经 EventBus 真实落盘"
    types = [
        json.loads(line).get("type")
        for line in ev_file.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert "supervisor.alert" in types, f"事件未落盘：{types}"


def test_batch_end_step_survives_plugin_crash(tmp_path: Path, event_bus, monkeypatch) -> None:
    """③ 插件崩溃 ⇒ 批末收尾不阻断（引擎层吞插件异常 + 方法层显性降级）。"""

    class _Boom(SupervisorPlugin):
        name = "boom"
        check_interval_chapters = 1

        def check(self, project_dir: str) -> list:
            raise RuntimeError("checker exploded")

    def _factory(project_dir: str) -> SupervisorEngine:
        engine = SupervisorEngine(project_dir)
        engine.registry.register(_Boom())
        return engine

    monkeypatch.setattr(
        "agent.core.supervisor.supervisor.create_default_engine", _factory
    )

    wf = _Workflow(tmp_path)
    # 不得抛出 —— 批末收尾（成本汇总/落盘）必须继续
    wf._run_supervisor_batch_end(SimpleNamespace(final_chapter=1, chapters_written=1))
    assert [e[0] for e in wf.memory.entries] == ["supervisor"], (
        "即便插件崩溃，监督步骤本身仍应完成留痕（引擎吞掉插件异常后继续）"
    )


def test_batch_end_step_survives_engine_crash(tmp_path: Path, monkeypatch) -> None:
    """③b 引擎装配本身崩溃（如 import 失败）⇒ 显性降级、不阻断批末。"""

    def _boom_factory(project_dir: str) -> SupervisorEngine:
        raise RuntimeError("engine factory exploded")

    monkeypatch.setattr(
        "agent.core.supervisor.supervisor.create_default_engine", _boom_factory
    )
    wf = _Workflow(tmp_path)
    wf._run_supervisor_batch_end(SimpleNamespace(final_chapter=1, chapters_written=1))
    assert wf.memory.entries == [], "装配崩溃时不得留下半截监督留痕"


# ---------------------------------------------------------------- 接线在位（辅助）
def test_pipeline_run_wires_supervisor_step() -> None:
    """④ ``run()`` 调用点在位 + mixin 定义在位（辅助源码断言；主判据是②的行为级）。"""
    import inspect

    from agent.workflows.pipeline import agentic_pipeline, agentic_pipeline_events

    run_src = inspect.getsource(agentic_pipeline.AgenticPipelineWorkflow.run)
    assert "_run_supervisor_batch_end(result)" in run_src, (
        "run() 批末监督调用点被摘除 ⇒ 监督静默退化为零消费（假把关者复发）"
    )
    mixin_src = inspect.getsource(agentic_pipeline_events._PipelineEventsMixin)
    assert "def _run_supervisor_batch_end" in mixin_src


# ---------------------------------------------------------------- CLI 口
def test_cli_supervisor_check_json_reports_issues(tmp_path: Path) -> None:
    """⑤ CLI --json：信封契约 + 告警如实呈现。"""
    _make_project(tmp_path)
    result = CliRunner().invoke(app, ["supervisor-check", "--json", "-d", str(tmp_path)])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["success"] is True
    dims = [i["dimension"] for i in data["report"]["issues"]]
    assert "plot_progress" in dims, f"实际 dims={dims}"


def test_cli_supervisor_check_empty_project_healthy(tmp_path: Path) -> None:
    """⑤b 空项目（0 章）⇒ 全插件被间隔跳过 ⇒ healthy 通过（exit 0）。"""
    result = CliRunner().invoke(app, ["supervisor-check", "--json", "-d", str(tmp_path)])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["success"] is True
    assert data["report"]["issues"] == []
    assert data["report"]["healthy"] is True
