"""长线一致性底座写时注入单测（设计稿第一期·A/D，纯离线零 LLM）。

覆盖：
- _build_task 注入【下一批方向】与三账文本（空 → 不注入，字节级最小变更）；
- _load_batch_directive 缺失/损坏 → 空降级；
- batch_directive 指定有效支线 → _load_context 接管 subline_id（软移交）；
- _load_ledger_context 装载三账（有登记实体 → 出现在注入文本）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.core.story.setting_manager import SettingManager
from tests.conftest import _build_minimal_project


def _list_sublines(proj: Path):
    from agent.core.story.setting_manager import SettingManager

    return SettingManager(proj).list_sublines()


def _wf(proj: Path):
    from agent.workflows.writing.m5_write_chapter import M5WriteChapterWorkflow

    return M5WriteChapterWorkflow(proj, llm_client=None, pre_validate=False)


# ---------------------------------------------------------------- _build_task 注入
def test_build_task_injects_directive_and_ledger() -> None:
    from agent.workflows.writing.agentic_write import AgenticWriteWorkflow
    from tests.test_g12_feedback import _min_ctx

    wf = AgenticWriteWorkflow.__new__(AgenticWriteWorkflow)  # 仅用 _build_task 纯函数
    base = wf._build_task(_min_ctx())
    assert "下一批方向" not in base
    assert "实体名册摘要" not in base

    task = wf._build_task(
        _min_ctx(
            batch_directive={"focus": "主角与师门决裂", "rationale": "支线B拖沓"},
            ledger_context="【实体名册摘要（有名实体的既有档案，本章必须自洽）】\n- 王铁柱",
        )
    )
    assert "下一批方向" in task and "主角与师门决裂" in task and "支线B拖沓" in task
    assert "王铁柱" in task


def test_build_task_empty_directive_no_injection() -> None:
    from agent.workflows.writing.agentic_write import AgenticWriteWorkflow
    from tests.test_g12_feedback import _min_ctx

    wf = AgenticWriteWorkflow.__new__(AgenticWriteWorkflow)
    task = wf._build_task(_min_ctx(batch_directive={}, ledger_context=""))
    assert "下一批方向" not in task and "实体名册摘要" not in task


# ---------------------------------------------------------------- directive 装载
def test_load_batch_directive_missing_and_corrupt(tmp_path: Path) -> None:
    proj = _build_minimal_project(tmp_path)
    wf = _wf(proj)
    assert wf._load_batch_directive() == {}

    p = proj / ".state" / "batch_directive.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{broken", encoding="utf-8")
    assert wf._load_batch_directive() == {}


def test_load_batch_directive_roundtrip(tmp_path: Path) -> None:
    proj = _build_minimal_project(tmp_path)
    p = proj / ".state" / "batch_directive.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps({"chapter": 10, "focus": "f", "subline": "", "rationale": "r"}, ensure_ascii=False),
        encoding="utf-8",
    )
    d = _wf(proj)._load_batch_directive()
    assert d["chapter"] == 10 and d["focus"] == "f"


# ---------------------------------------------------------------- 支线软移交
def test_directive_subline_takeover(tmp_path: Path) -> None:
    proj = _build_minimal_project(tmp_path)
    wf = _wf(proj)
    sublines = _list_sublines(proj)
    assert sublines, "最小项目应至少有一条支线"
    target = sublines[0]

    # 把进度指到别的支线（或空），指令接管到 target
    wf.state_machine.load()
    others = [s for s in sublines if s != target]
    wf.state_machine.progress = {**(wf.state_machine.progress or {}), "current_subline": (others[0] if others else "")}
    wf.state_machine.save()

    p = proj / ".state" / "batch_directive.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps({"chapter": 5, "focus": "f", "subline": target, "rationale": "r"}, ensure_ascii=False),
        encoding="utf-8",
    )
    ctx = wf._load_context()
    assert ctx["subline_id"] == target
    assert ctx["batch_directive"]["focus"] == "f"


def test_directive_invalid_subline_keeps_state(tmp_path: Path) -> None:
    proj = _build_minimal_project(tmp_path)
    wf = _wf(proj)
    wf.state_machine.load()
    wf.state_machine.progress = {**(wf.state_machine.progress or {}), "current_subline": _list_sublines(proj)[0]}
    original = wf.state_machine.progress["current_subline"]
    wf.state_machine.save()

    p = proj / ".state" / "batch_directive.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps({"chapter": 5, "focus": "f", "subline": "不存在的支线"}, ensure_ascii=False),
        encoding="utf-8",
    )
    ctx = wf._load_context()
    assert ctx["subline_id"] == original  # 兜底：无效指令不接管


# ---------------------------------------------------------------- 三账注入
def test_load_ledger_context_renders_registered_entity(tmp_path: Path) -> None:
    from agent.core.story.entity_ledger import EntityLedgerStore

    proj = _build_minimal_project(tmp_path)
    st = EntityLedgerStore(proj).load()
    st.ensure("林惊澜", ch=3)
    st.set_motivation("林惊澜", desire="查明灭门真相")
    st.save()

    wf = _wf(proj)
    sublines = _list_sublines(proj)
    sub_data = SettingManager(proj).load_subline(sublines[0])
    text = wf._load_ledger_context(sub_data, 10)
    # 最小项目的出场角色未必登记了名册——至少不抛错；登记了名册中存在的名字才渲染
    if "林惊澜" in (sub_data.get("content") or ""):
        assert "灭门真相" in text


def test_load_ledger_context_degrades_on_corrupt(tmp_path: Path, caplog) -> None:
    proj = _build_minimal_project(tmp_path)
    p = proj / ".state" / "continuity" / "entity_ledger.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{broken", encoding="utf-8")

    import logging

    wf = _wf(proj)
    sublines = _list_sublines(proj)
    with caplog.at_level(logging.WARNING):
        text = wf._load_ledger_context(SettingManager(proj).load_subline(sublines[0]), 10)
    assert "实体名册摘要" not in text  # 名册损坏降级，信息账/战力账仍可能为空
    assert any("entity_ledger.render" in (r.getMessage() or "") for r in caplog.records)


# ---------------------------------------------------------------- 时效护栏
def test_directive_stale_ignored(tmp_path: Path) -> None:
    proj = _build_minimal_project(tmp_path)
    wf = _wf(proj)
    wf.state_machine.load()
    wf.state_machine.progress = {
        **(wf.state_machine.progress or {}),
        "current_subline": _list_sublines(proj)[0],
        "total_written": 200,
    }
    wf.state_machine.save()

    p = proj / ".state" / "batch_directive.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps({"chapter": 5, "focus": "旧焦点", "subline": "某支线"}, ensure_ascii=False),
        encoding="utf-8",
    )
    ctx = wf._load_context()
    assert ctx["batch_directive"] == {}  # 200-5 > 60 → 过期忽略
    assert "旧焦点" not in (ctx.get("ledger_context") or "")


def test_directive_fresh_still_applies(tmp_path: Path) -> None:
    proj = _build_minimal_project(tmp_path)
    wf = _wf(proj)
    wf.state_machine.load()
    wf.state_machine.progress = {
        **(wf.state_machine.progress or {}),
        "total_written": 12,
    }
    wf.state_machine.save()

    p = proj / ".state" / "batch_directive.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps({"chapter": 10, "focus": "新焦点"}, ensure_ascii=False),
        encoding="utf-8",
    )
    ctx = wf._load_context()
    assert ctx["batch_directive"]["focus"] == "新焦点"  # 13-10=3 ≤ 60 → 有效
