"""批前逐章契约补齐（``workflows/pipeline/subline_contract``）红线（2026-09-24）。

背景（《灵荒工坊》自动写作失败实证）：subline.md 的逐章行**唯一写入方**是 M3
首轮大纲；``PlannerAgent.replan_batch`` 只产 arc 级规划 ⇒ 开篇窗口（20 章）之后
逐章契约永久缺位 ⇒ 写手按阶段模板自编 ⇒ 同质/注水 ⇒ 评委不合格 ⇒ 回退输入
不变 ⇒ 整窗销毁-重写死循环。

本文件钉死三件事（缺一条则修复无效）：
1. **缺口判定**用与门禁同一个窗口口径（``cur+1 .. cur+20``）；
2. **幂等合并**：只替换同章号旧行，历史逐章行与批次标题**不得被销毁**；
3. **门禁可见**：补上的行必须让 ``check_subline_plot_source`` 在窗口内看见
   （补了但门禁看不见 = 白烧 token，缺陷被掩盖）；
   **写手可见**：``chapter_contract.select_chapter_lines`` 必须取到本章那一行。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.core.story.chapter_contract import (
    HOOKS_SECTION,
    POINTS_SECTION,
    TIERS_SECTION,
    chapter_contract,
    select_chapter_lines,
)
from agent.workflows.pipeline import subline_contract as sc
from agent.workflows.pipeline.plan_consistency import (
    chapter_numbers,
    check_subline_plot_source,
    read_stage_level_supply,
    write_window,
)


def _subline_md(*, batch1_rows: bool = True) -> str:
    """真实形态的 subline.md：支线覆盖 1-150，逐章行只到第 20 章。"""
    head = """---
subline_id: "S01_test"
subline_name: "测试支线"
status: "planned"
---

# 支线设定 · 测试支线

## 支线目标

林凡建立人工作坊流水线并用产量证明五灵根的生产力价值

## 出场角色

林凡、周管事、赵师兄

## 关键冲突

宗门制度性排斥 vs 林凡用实力打破偏见

## 约束

五行吞噬诀每日限三次

## 与主线的关系

铺垫阶段建立核心能力

## 剧集压力曲线

| 阶段 | 章节 | 张力等级 |
|---|---|---|
| 铺垫 | 1-37 | 低 |
| 冲突 | 38-94 | 中 |
| 高潮 | 95-131 | 高 |
| 舒缓 | 132-150 | 低 |
"""
    hooks = "\n".join(
        f"第{i}章：档位=推进｜章首钩子=开场{i}｜章尾钩子=悬念{i}｜爽点=小爽"
        f"｜目标情绪=紧张｜在场=林凡｜禁=无｜验收=读者能说出事件{i}"
        for i in range(1, 21)
    )
    tiers = "\n".join(f"第{i}章：推进" for i in range(1, 21))
    points = "\n".join(f"第{i}章：林凡做了第{i}件事；后果{i}" for i in range(1, 21))
    body = f"""
## 章节钩子设计

批次1（第1-20章）：
{hooks}

## 章节强度档位

批次1（第1-20章）：
{tiers}

## 情节点序列

批次1（第1-20章）：
{points}
"""
    return head + (body if batch1_rows else "")


def _make_project(tmp_path: Path, content: str, *, total_written: int, name: str = "S01_test") -> Path:
    d = tmp_path / "sublines" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "subline.md").write_text(content, encoding="utf-8")
    state = tmp_path / ".state" / "state.json"
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(
        json.dumps({"progress": {"total_written": total_written, "current_chapter": total_written}}),
        encoding="utf-8",
    )
    return tmp_path


class _CollectConsole:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def print(self, *a, **k) -> None:  # noqa: ANN002, ANN003
        self.lines.append(" ".join(str(x) for x in a))


def _draft_for(lo: int, hi: int) -> sc.ChapterContractDraft:
    return sc.ChapterContractDraft(
        chapter_hooks="\n".join(
            f"第{i}章：档位=推进｜章首钩子=新开场{i}｜章尾钩子=新悬念{i}｜爽点=新爽{i}"
            f"｜目标情绪=期待｜在场=林凡｜禁=无｜验收=读者能说出新事件{i}"
            for i in range(lo, hi + 1)
        ),
        chapter_tiers="\n".join(f"第{i}章：推进" for i in range(lo, hi + 1)),
        plot_points="\n".join(f"第{i}章：林凡做了新事{i}；后果新{i}" for i in range(lo, hi + 1)),
    )


def _read(proj: Path, name: str = "S01_test") -> str:
    return (proj / "sublines" / name / "subline.md").read_text(encoding="utf-8")


# ---------------------------------------------------------------- 单元：行规范化
def test_normalize_lines_keeps_only_in_range_chapter_lines() -> None:
    raw = (
        "批次3（第41-60章）：\n"
        "- 第41章：档位=推进｜章首钩子=a\n"
        "第42章：档位=垫片｜章首钩子=b\n"
        "第42章：档位=日常｜章首钩子=重复\n"
        "第61章：档位=推进｜章首钩子=越界\n"
        "第40章：档位=推进｜章首钩子=不足\n"
        "这是一句解释，不含章标记\n"
        "第45章 缺冒号不算\n"
    )
    got = sc._normalize_lines(raw, lo=41, hi=60)
    assert got == [
        "第41章：档位=推进｜章首钩子=a",
        "第42章：档位=垫片｜章首钩子=b",
    ], got


# ---------------------------------------------------------------- 单元：幂等合并
def test_merge_section_replaces_same_chapter_and_keeps_history() -> None:
    content = _subline_md()
    merged = sc._merge_section(
        content, HOOKS_SECTION, ["第5章：档位=高潮｜章首钩子=改写后的第5章"]
    )
    assert "改写后的第5章" in merged
    assert "开场5" not in merged, "同章号旧行未被替换"
    # 窗口外历史行与批次标题**不得被销毁**（不可逆销毁＝比缺陷更贵）
    assert "开场4" in merged and "开场20" in merged
    assert "批次1（第1-20章）：" in merged
    # 幂等：再合并同一行，内容不变
    again = sc._merge_section(merged, HOOKS_SECTION, ["第5章：档位=高潮｜章首钩子=改写后的第5章"])
    assert again == merged


def test_merge_section_creates_missing_section_at_end() -> None:
    content = _subline_md(batch1_rows=False)
    assert "## 章节强度档位" not in content
    merged = sc._merge_section(content, TIERS_SECTION, ["第41章：推进"])
    assert "## 章节强度档位" in merged
    assert "第41章：推进" in merged


# ---------------------------------------------------------------- 端到端：补齐
def test_ensure_fills_window_gap_and_is_visible_to_gate_and_writer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """★ 核心红线：21 章以后（续写态）必须补出窗口内逐章契约，且**三端可见**。

    - 门禁 ``check_subline_plot_source``：窗口内不再报"没有逐章契约行"，
      也不再记阶段级供给计数（那是"供给侧未收敛"的信号，补上了就该消失）；
    - 写手 ``select_chapter_lines``：必须拿到**本章那一行**，而不是回退前文；
    - 文件：历史批次行保留（不销毁）。
    """
    proj = _make_project(tmp_path, _subline_md(), total_written=40)
    assert write_window(proj) == (41, 60)
    # 补前：门禁在窗口 41-60 内确实看不见逐章行（复现缺陷）
    console_before = _CollectConsole()
    check_subline_plot_source(proj, console=console_before)
    assert any("没有逐章契约行" in ln for ln in console_before.lines), console_before.lines
    assert (proj / ".state" / "plan_gate_stage_level.jsonl").exists()

    calls: list[tuple[int, int]] = []

    def _fake(llm, messages, *, lo, hi):  # noqa: ANN001
        calls.append((lo, hi))
        return _draft_for(lo, hi)

    monkeypatch.setattr(sc, "_draft_for_range", _fake)
    console = _CollectConsole()
    notes = sc.ensure_window_contracts(proj, summary="（摘要）", llm=object(), console=console)

    assert calls == [(41, 60)], f"缺口区间应恰为 41-60，实际 {calls}"
    assert notes and "41-60" in notes[0], notes
    merged = _read(proj)

    # ① 门禁可见
    assert chapter_numbers(merged) >= set(range(41, 61))
    console_after = _CollectConsole()
    assert check_subline_plot_source(proj, console=console_after) == []
    assert not any("没有逐章契约行" in ln for ln in console_after.lines), console_after.lines
    assert read_stage_level_supply(proj)["total"] == 1, "补上后不得再记阶段级供给（计数应停止增长）"

    # ② 写手可见（本章那一行，不是回退/整段）
    hooks, points = chapter_contract(merged, chapter_num=45)
    assert "新开场45" in hooks, hooks
    assert "新事45" in points, points
    assert "尚无逐章契约" not in hooks
    assert select_chapter_lines(merged, TIERS_SECTION, chapter_num=45).strip() == "第45章：推进"

    # ③ 历史不销毁
    assert "开场20" in merged and "批次1（第1-20章）：" in merged
    assert chapter_numbers(merged) >= set(range(1, 21))


def test_ensure_is_idempotent_and_skips_llm_when_no_gap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """窗口内已全覆盖 ⇒ **零 LLM 调用**（幂等重跑的关键：批前每批都会跑一次）。"""
    proj = _make_project(tmp_path, _subline_md(), total_written=40)

    def _fake(llm, messages, *, lo, hi):  # noqa: ANN001
        return _draft_for(lo, hi)

    monkeypatch.setattr(sc, "_draft_for_range", _fake)
    sc.ensure_window_contracts(proj, llm=object(), console=_CollectConsole())
    once = _read(proj)

    def _boom(*a, **k):  # noqa: ANN002, ANN003
        raise AssertionError("窗口无缺口时不得再调 LLM")

    monkeypatch.setattr(sc, "_draft_for_range", _boom)
    notes = sc.ensure_window_contracts(proj, llm=object(), console=_CollectConsole())
    assert notes == [], notes
    assert _read(proj) == once, "重跑不应改动文件"


def test_ensure_degrades_and_keeps_file_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """生成失败 ⇒ 显性降级、文件原样（增强项不得把写作拖停，也不得留半截文件）。"""
    proj = _make_project(tmp_path, _subline_md(), total_written=40)
    before = _read(proj)

    def _boom(*a, **k):  # noqa: ANN002, ANN003
        raise RuntimeError("provider down")

    monkeypatch.setattr(sc, "_draft_for_range", _boom)
    console = _CollectConsole()
    notes = sc.ensure_window_contracts(proj, llm=object(), console=console)
    assert notes == []
    assert _read(proj) == before
    assert any("逐章契约补齐失败" in ln for ln in console.lines), console.lines
    assert not list((proj / "sublines" / "S01_test").glob("*.tmp")), "临时文件未清理"


def test_ensure_skips_subline_outside_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """远期支线（151-300）不得因"窗口内缺行"被补 —— 与门禁的作用域同口径。"""
    far = _subline_md().replace("| 铺垫 | 1-37 | 低 |", "| 铺垫 | 151-188 | 低 |").replace(
        "| 冲突 | 38-94 | 中 |", "| 冲突 | 189-244 | 中 |"
    ).replace("| 高潮 | 95-131 | 高 |", "| 高潮 | 245-281 | 高 |").replace(
        "| 舒缓 | 132-150 | 低 |", "| 舒缓 | 282-300 | 低 |"
    )
    proj = _make_project(tmp_path, far, total_written=40)

    def _boom(*a, **k):  # noqa: ANN002, ANN003
        raise AssertionError("窗口外支线不得调 LLM")

    monkeypatch.setattr(sc, "_draft_for_range", _boom)
    assert sc.ensure_window_contracts(proj, llm=object(), console=_CollectConsole()) == []


# ---------------------------------------------------------------- 接线：与复规划解耦
def test_maybe_replan_supplies_contract_even_if_replan_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """★ 供给补齐与复规划**并列**：复规划挂了也必须补（否则死循环照旧）。

    复规划只是"重排弧线"，它从不写逐章行；两条链共用一个入口但不互为前提。
    """
    from agent.workflows.pipeline import batch_replan as br

    proj = _make_project(tmp_path, _subline_md(), total_written=40)
    (proj / ".state" / "plan.json").write_text(
        json.dumps({"total_chapters": 150, "episode_tree": []}, ensure_ascii=False),
        encoding="utf-8",
    )
    (proj / "chapters").mkdir(exist_ok=True)
    for i in range(1, 41):
        (proj / "chapters" / f"ch{i:03d}.md").write_text(f"第{i}章正文", encoding="utf-8")

    def _replan_boom(*a, **k):  # noqa: ANN002, ANN003
        raise RuntimeError("planner unavailable")

    monkeypatch.setattr("agent.agents.planner.PlannerAgent", _replan_boom)
    # 补齐链自己会按需 create_gateway()；本用例只验接线，注入哑对象避免真装配
    monkeypatch.setattr("agent.client.gateway_adapter.create_gateway", lambda *a, **k: object())

    def _fake(llm, messages, *, lo, hi):  # noqa: ANN001
        return _draft_for(lo, hi)

    monkeypatch.setattr(sc, "_draft_for_range", _fake)
    ok = br.maybe_replan(proj, console=_CollectConsole())
    assert ok is False, "复规划失败应返回 False（沿用既有计划）"
    assert chapter_numbers(_read(proj)) >= set(range(41, 61)), "复规划失败时逐章契约仍须补齐"