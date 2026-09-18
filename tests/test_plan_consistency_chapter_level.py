"""红线：写前闸必须检查细纲**粒度**，不能只检查「段存在」（2026-09-18）。

背景
----
``check_subline_plot_source`` 原先只判「情节点序列 / 章节钩子设计 两小节**存在且非空**」
⇒ **一份 180 章只给 4 行阶段模板的支线也「通过」**。这正是 2026-09-18 那条 P0
（章级意图缺位，净推进 0 章）**没能在启动时被拦住**的原因：闸门只查"有没有这个段"，
不查"这个段够不够章级粒度"，缺陷于是一路走到写手才炸。

判据分层（设计意图，本文件的断言逐条对应）
------------------------------------------
1. 段缺失 ⇒ fail-fast（既有行为，保留）
2. **无逐章契约行（纯阶段模板）⇒ fail-fast** ← 本次新增，09-18 P0 的回归断言
   - 作用域＝**即将写的章节窗口**：远期支线（如 181-420 章的 S02）只告警，
     否则一条还未轮到写的支线会冻住整本书
   - 豁免 ``allow_stage_level=True``：放行但**必须落盘留痕**；留痕失败＝未豁免
3. 开篇窗口覆盖率不足 ⇒ **只告警**（分批写作的合法中间态）
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from agent.workflows.pipeline.plan_consistency import (
    check_subline_plot_source,
    prepare_for_write,
)

# 实验前《灵荒薪传》S01 的真实形态：150 章只有 4 行阶段模板
_STAGE_ONLY = """---
subline_id: "S01"
---

# 支线设定

## 剧集压力曲线

| 阶段 | 章节 | 张力等级 |
|---|---|---|
| 铺垫 | 1-40 | 低 |
| 冲突 | 41-90 | 中 |

## 章节钩子设计

铺垫阶段：章尾=日常小悬念（弱）
冲突阶段：章尾=危机升级（中）

## 情节点序列

铺垫阶段：主角适应环境，埋下第一个伏笔
冲突阶段：与对手正面交锋
"""

_CHAPTER_HOOKS = "\n".join(
    f"第{i}章：章首钩子=开场{i}｜章尾钩子=悬念{i}｜爽点=小爽｜目标情绪=紧张"
    for i in range(1, 21)
)
_CHAPTER_LEVEL = _STAGE_ONLY.replace(
    "铺垫阶段：章尾=日常小悬念（弱）\n冲突阶段：章尾=危机升级（中）", _CHAPTER_HOOKS
)


def _make_project(
    tmp_path: Path,
    content: str,
    *,
    total_written: int | None = None,
    subline_id: str = "S01_test",
) -> Path:
    d = tmp_path / "sublines" / subline_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "subline.md").write_text(content, encoding="utf-8")
    if total_written is not None:
        state = tmp_path / ".state" / "state.json"
        state.parent.mkdir(parents=True, exist_ok=True)
        state.write_text(
            json.dumps({"progress": {"total_written": total_written}}, ensure_ascii=False),
            encoding="utf-8",
        )
    return tmp_path


class _CollectConsole:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def print(self, *a, **k) -> None:  # noqa: ANN002, ANN003
        self.lines.append(" ".join(str(x) for x in a))


def test_stage_level_plumbing_fails_fast(tmp_path: Path) -> None:
    """★ 09-18 P0 回归断言：纯阶段模板必须 fail-fast（不是「段存在就放行」）。"""
    errs = check_subline_plot_source(_make_project(tmp_path, _STAGE_ONLY))
    assert errs, "阶段模板被放行了 —— 章级意图缺位会再次走到写手"
    assert any("阶段级" in e and "逐章契约行" in e for e in errs), errs
    assert any("--allow-stage-level" in e for e in errs), "错误信息未给出修复路径"


def test_chapter_level_lines_pass(tmp_path: Path) -> None:
    """带逐章契约行的细纲必须通过。"""
    assert not check_subline_plot_source(_make_project(tmp_path, _CHAPTER_LEVEL))


def test_stage_level_exemption_leaves_ledger(tmp_path: Path) -> None:
    """显式豁免放行，但**必须落盘留痕**（口子不得静默）。"""
    proj = _make_project(tmp_path, _STAGE_ONLY)
    assert not check_subline_plot_source(proj, allow_stage_level=True)
    ledger = proj / ".state" / "plan_gate_waivers.jsonl"
    assert ledger.exists(), "豁免未留痕 —— 显式豁免必须可审计"
    rec = json.loads(ledger.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert rec["gate"] == "stage_level_exempt"
    assert rec["subline"] == "S01_test"


def test_exemption_denied_without_stage_lines(tmp_path: Path) -> None:
    """申请豁免却连阶段行都没有 ⇒ 豁免不成立（不能拿豁免掩盖空细纲）。"""
    bare = re.sub(r"铺垫阶段：.*|冲突阶段：.*", "（无内容）", _STAGE_ONLY)
    errs = check_subline_plot_source(_make_project(tmp_path, bare), allow_stage_level=True)
    assert errs and any("豁免不成立" in e for e in errs), errs


def test_exemption_denied_when_ledger_unwritable(tmp_path: Path) -> None:
    """留痕失败 ⇒ 视为未豁免（静默放行比不放行更危险）。"""
    proj = _make_project(tmp_path, _STAGE_ONLY)
    (proj / ".state").write_text("占位：让 mkdir 失败", encoding="utf-8")
    errs = check_subline_plot_source(proj, allow_stage_level=True)
    assert errs and any("留痕落盘失败" in e for e in errs), errs


def test_far_future_subline_does_not_block(tmp_path: Path) -> None:
    """作用域断言：尚未进入写作窗口的支线只告警，不冻住整本书。"""
    far = _STAGE_ONLY.replace("| 铺垫 | 1-40 | 低 |", "| 铺垫 | 181-238 | 低 |").replace(
        "| 冲突 | 41-90 | 中 |", "| 冲突 | 239-324 | 中 |"
    )
    proj = _make_project(tmp_path, far, total_written=5)
    console = _CollectConsole()
    errs = check_subline_plot_source(proj, console=console)
    assert not errs, f"远期支线挡住了当前写作：{errs}"
    assert any("尚未进入写作窗口" in ln for ln in console.lines), console.lines


def test_in_window_subline_still_blocks(tmp_path: Path) -> None:
    """对照：窗口内的支线（1-150）仍必须 fail-fast —— 作用域收准≠判据放宽。"""
    proj = _make_project(tmp_path, _STAGE_ONLY, total_written=5)
    assert check_subline_plot_source(proj), "窗口内的阶段级支线被放行"


def test_low_coverage_warns_but_passes(tmp_path: Path) -> None:
    """覆盖率不足只告警（分批写作的合法中间态）。"""
    few = _STAGE_ONLY.replace(
        "铺垫阶段：章尾=日常小悬念（弱）\n冲突阶段：章尾=危机升级（中）",
        "\n".join(f"第{i}章：章尾钩子=悬念{i}" for i in range(1, 6)),
    )
    console = _CollectConsole()
    errs = check_subline_plot_source(_make_project(tmp_path, few), console=console)
    assert not errs, f"分批写作被误拦：{errs}"
    assert any("低于开篇窗口" in ln for ln in console.lines), console.lines


def test_prepare_for_write_forwards_exemption(tmp_path: Path) -> None:
    """豁免必须能从写前统一入口透传（否则新判据没有出口＝judge-and-trap）。"""
    proj = _make_project(tmp_path, _STAGE_ONLY)
    assert prepare_for_write(proj), "默认应严格"
    assert not prepare_for_write(proj, allow_stage_level=True), "豁免未透传"
