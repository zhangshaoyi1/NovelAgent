"""红线：写前闸必须检查细纲**粒度**，不能只检查「段存在」（2026-09-18）。

背景
----
``check_subline_plot_source`` 原先只判「情节点序列 / 章节钩子设计 两小节**存在且非空**」
⇒ **一份 180 章只给 4 行阶段模板的支线也「通过」**。这正是 2026-09-18 那条 P0
（章级意图缺位，净推进 0 章）**没能在启动时被拦住**的原因：闸门只查"有没有这个段"，
不查"这个段够不够章级粒度"，缺陷于是一路走到写手才炸。

判据分层（设计意图，本文件的断言逐条对应）
------------------------------------------
0. **作用域＝即将写的窗口**（``cur+1 .. cur+20``）：支线区间与窗口不相交 ⇒ 只告警；
   判据 2/3 的分子分母**只算窗口内**
1. 段缺失 ⇒ fail-fast（既有行为，保留）
2. **窗口内无逐章契约行（纯阶段模板）⇒ 告警 + 记入计数台账**（**不阻断**）
   - ⚠ 判定量必须是「窗口内的行数」而不是「全文件的行数」：初版用全文件计数时，
     "1-5 章逐章 + 6 章起阶段模板"（窗口 6-25 内一条行都没有）**照样通过** ——
     同一缺陷类（只判有没有、不判窗口内够不够）在同一修复里踩了两次，
     第二次是**端到端在真实项目上跑闸**才暴露的（见 ``test_partial_...``）
   - ★ **强度校准（C 方案，2026-09-18 端到端影响面审计后拍板）**：本条初版是
     fail-fast，但审计发现它会拦 **49/53 支线、含 6 本在写的书**，而它们
     **恰恰靠阶段级供给写成了 150–350 章**（``五灵破归档`` 211 / ``灵荒薪传`` 59，
     支线逐章行均为 0）⇒「阶段级 ⇒ 卡死」不成立，它不是确定性不变量；
     且 ``chapter_contract`` 明确把阶段级作为**兼容回退**支持。
     ⇒ 用硬拦守它＝动作强度超过判据可达性（纪律 #2/#13）。
     现为「告警 + **可下降的计数**」，P0 真解在供给侧（v4 逐章 ``5ee400d``）。
   - 豁免 ``allow_stage_level=True``：C 方案下它**不决定能否开工**，只表示运维
     **显式确认**并写 ``.state/plan_gate_waivers.jsonl`` 审计链
3. 窗口内覆盖率不足 ⇒ **只告警**（分批写作的合法中间态）

仍保留 fail-fast 的两条
----------------------
- 判据 1（剧情源段整体缺失）
- **计数台账落盘失败**（不留痕则计数不可信 ⇒ 宁可拒绝开工也不静默，纪律 #18）
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

# ★ 端到端验收发现的真实形态（灵荒薪传-重启 S01）：1-5 章逐章契约、第 6 章起阶段模板。
#   全文件有 5 行逐章 ⇒ 初版「全文件计数」判据静默放行，而窗口 6-25 内一行都没有。
_PARTIAL_CHAPTERS = "\n".join(f"第{i}章：章尾钩子=悬念{i}" for i in range(1, 6))
_PARTIAL_CHAPTER_LEVEL = _STAGE_ONLY.replace(
    "铺垫阶段：章尾=日常小悬念（弱）\n冲突阶段：章尾=危机升级（中）", _PARTIAL_CHAPTERS
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


def test_stage_level_supply_warns_and_counts(tmp_path: Path) -> None:
    """★ 09-18 P0 的防线（C 方案）：纯阶段模板**必须告警且记入计数台账**，但不阻断。

    强度校准说明：本条初版断言 fail-fast，端到端影响面审计后下调为「告警 + 计数」——
    阶段级供给是 ``chapter_contract`` 明确支持的兼容回退，且 6 本在写的书靠它
    写成了 150–350 章 ⇒ 它不是确定性不变量（详见模块 docstring 与登记单）。
    **"不阻断"不等于"静默"**：告警文本必须点名缺陷与收敛目标，且计数必须落盘。
    """
    proj = _make_project(tmp_path, _STAGE_ONLY, total_written=5)
    console = _CollectConsole()
    errs = check_subline_plot_source(proj, console=console)
    assert not errs, f"C 方案下阶段级供给不应阻断开工：{errs}"
    assert any("阶段级" in ln and "逐章契约行" in ln for ln in console.lines), console.lines
    assert any("只告警、不阻断" in ln for ln in console.lines), "未标注强度"
    assert any("累计 1 次" in ln for ln in console.lines), f"未把计数打进告警：{console.lines}"
    ledger = proj / ".state" / "plan_gate_stage_level.jsonl"
    assert ledger.exists(), "计数未落盘 —— 可下降的计数是 C 方案的唯一抓手"
    assert "收敛目标" in " ".join(console.lines), "告警未给出收敛目标"


def test_stage_level_supply_count_is_readable_and_decreases(tmp_path: Path) -> None:
    """计数必须有**消费者**（纪律 #7）：public 读取器能返回按支线累计的次数。"""
    from agent.workflows.pipeline.plan_consistency import read_stage_level_supply

    proj = _make_project(tmp_path, _STAGE_ONLY, total_written=5)
    assert read_stage_level_supply(proj)["total"] == 0, "无台账时应为 0 且不抛"
    check_subline_plot_source(proj)
    check_subline_plot_source(proj)
    got = read_stage_level_supply(proj)
    assert got["total"] == 2, got
    assert got["by_subline"] == {"S01_test": 2}, got


def test_chapter_level_lines_pass(tmp_path: Path) -> None:
    """带逐章契约行的细纲必须通过。"""
    assert not check_subline_plot_source(_make_project(tmp_path, _CHAPTER_LEVEL))


def test_stage_level_exemption_leaves_ledger(tmp_path: Path) -> None:
    """``--allow-stage-level`` 在 C 方案下是**运维显式确认**，必须落盘留痕（审计链不得缺）。"""
    proj = _make_project(tmp_path, _STAGE_ONLY, total_written=5)
    console = _CollectConsole()
    errs = check_subline_plot_source(proj, allow_stage_level=True, console=console)
    assert not errs, errs
    ledger = proj / ".state" / "plan_gate_waivers.jsonl"
    assert ledger.exists(), "确认未留痕 —— 显式确认必须可审计"
    rec = json.loads(ledger.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert rec["gate"] == "stage_level_exempt"
    assert rec["subline"] == "S01_test"
    assert any("已按 --allow-stage-level 显式确认" in ln for ln in console.lines), console.lines


def test_ack_without_stage_lines_records_and_warns(tmp_path: Path) -> None:
    """申请确认却连阶段行都没有 ⇒ 仍开工，但告警必须**点名**（不许拿确认掩盖空细纲）。"""
    bare = re.sub(r"铺垫阶段：.*|冲突阶段：.*", "（无内容）", _STAGE_ONLY)
    proj = _make_project(tmp_path, bare, total_written=5)
    console = _CollectConsole()
    errs = check_subline_plot_source(proj, allow_stage_level=True, console=console)
    assert not errs, errs
    assert any("连阶段级情节点/钩子行都没有" in ln for ln in console.lines), console.lines


def test_stage_level_count_ledger_unwritable_is_fatal(tmp_path: Path) -> None:
    """★ 唯一保留的 fail-fast 分支：计数台账写不了 ⇒ 拒绝开工。

    依据（纪律 #18「缺留痕＝没有闸」）：C 方案把判据强度押在**计数**上，
    计数不可信则"可下降的计数"整体失效 ⇒ 宁可拒绝开工也不静默放行。
    """
    proj = _make_project(tmp_path, _STAGE_ONLY, total_written=5)
    # 把台账路径占成**目录** ⇒ 追加写必然失败（比权限位更可移植）
    bad = proj / ".state" / "plan_gate_stage_level.jsonl"
    bad.mkdir(parents=True, exist_ok=True)
    errs = check_subline_plot_source(proj)
    assert errs and any("计数留痕落盘失败" in e for e in errs), errs


def test_far_future_subline_does_not_block(tmp_path: Path) -> None:
    """作用域断言：尚未进入写作窗口的支线只告警，不冻住整本书。"""
    far = _STAGE_ONLY.replace("| 铺垫 | 1-40 | 低 |", "| 铺垫 | 181-238 | 低 |").replace(
        "| 冲突 | 41-90 | 中 |", "| 冲突 | 239-324 | 中 |"
    )
    proj = _make_project(tmp_path, far, total_written=5)
    console = _CollectConsole()
    errs = check_subline_plot_source(proj, console=console)
    assert not errs, f"远期支线挡住了当前写作：{errs}"
    assert any("不属于本次写作窗口" in ln for ln in console.lines), console.lines


def test_in_window_stage_level_is_counted_not_blocked(tmp_path: Path) -> None:
    """对照：窗口内的阶段级支线 ⇒ 报出来 + 计数，但**不冻书**（C 方案的正面断言）。"""
    proj = _make_project(tmp_path, _STAGE_ONLY, total_written=5)
    console = _CollectConsole()
    errs = check_subline_plot_source(proj, console=console)
    assert not errs, f"C 方案下窗口内阶段级供给不应阻断：{errs}"
    assert (proj / ".state" / "plan_gate_stage_level.jsonl").exists()


def test_partial_chapter_lines_do_not_cover_window(tmp_path: Path) -> None:
    """★ 端到端验收发现：1-5 章逐章 + 6 章起阶段模板 ⇒ 窗口 6-25 内无行 ⇒ 必须报出来。

    初版判据量是「全文件有逐章行」（5 行 > 0）⇒ **静默放行**，而即将写的窗口
    6-25 内一条逐章行都没有 —— 这正是 09-18 P0（净推进 0 章）的成因。
    断言手段：告警/错误必须点名**窗口号**，证明判定量真的收到了窗口上
    （"报得准"是强度校准的前提：连说什么都说不对，就无从谈告警还是拦截）。
    """
    proj = _make_project(tmp_path, _PARTIAL_CHAPTER_LEVEL, total_written=5)
    console = _CollectConsole()
    errs = check_subline_plot_source(proj, console=console)
    assert any("写作窗口 6-25" in ln for ln in console.lines), (errs, console.lines)
    assert (proj / ".state" / "plan_gate_stage_level.jsonl").exists(), "未计入台账"


def test_full_window_coverage_passes(tmp_path: Path) -> None:
    """对照：窗口 1-20 被逐章契约全覆盖 ⇒ 通过（收准作用域≠把判据变严）。"""
    proj = _make_project(tmp_path, _CHAPTER_LEVEL, total_written=0)
    console = _CollectConsole()
    errs = check_subline_plot_source(proj, console=console)
    assert not errs, f"窗口已全覆盖仍被拦：{errs}"
    assert not console.lines, f"窗口已全覆盖仍告警：{console.lines}"


def test_low_coverage_warns_but_passes(tmp_path: Path) -> None:
    """覆盖率不足只告警（分批写作的合法中间态）。"""
    few = _STAGE_ONLY.replace(
        "铺垫阶段：章尾=日常小悬念（弱）\n冲突阶段：章尾=危机升级（中）",
        "\n".join(f"第{i}章：章尾钩子=悬念{i}" for i in range(1, 6)),
    )
    console = _CollectConsole()
    errs = check_subline_plot_source(_make_project(tmp_path, few), console=console)
    assert not errs, f"分批写作被误拦：{errs}"
    assert any("低于" in ln and "90%" in ln for ln in console.lines), console.lines


def test_prepare_for_write_forwards_exemption(tmp_path: Path) -> None:
    """``--allow-stage-level`` 必须能从写前统一入口透传到**审计链**（口子不得静默）。

    C 方案下它不决定能否开工，所以断言落在「确认是否被记录」而非「是否放行」——
    行为级：默认无 waivers 台账，显式确认后必须多出一条。
    """
    proj = _make_project(tmp_path, _STAGE_ONLY, total_written=5)
    assert not prepare_for_write(proj)  # C 方案：阶段级不再阻断
    assert not (proj / ".state" / "plan_gate_waivers.jsonl").exists(), "未确认却写了审计链"
    assert not prepare_for_write(proj, allow_stage_level=True)
    ledger = proj / ".state" / "plan_gate_waivers.jsonl"
    assert ledger.exists(), "显式确认未透传 —— 审计链缺失"


def test_pipeline_preflight_forwards_stage_level_exemption(tmp_path: Path) -> None:
    """★ 端到端实测缺陷（2026-09-18）：pipeline 内部那次 prepare_for_write
    必须与 CLI 层**同语义**地透传 `allow_stage_level`。

    实测（真实运行 `autowrite --allow-stage-level`）：CLI 层已打印
    「已按 --allow-stage-level 显式豁免并留痕」，紧接着 pipeline 自己报
    「✗ 规划校验失败」⇒ **0 章写出**（pipeline 在 run() 内会再跑一次本校验，
    用于覆盖 Web / 直调入口，此前没带开关）。
    ⇒ 声明了却走不通的口子＝陷阱。

    断言口径（C 方案下）：两层**必须都读到同一个确认值** —— 默认不留审计链，
    显式确认后必须留，且 pipeline 层的结果与 CLI 层一致。
    """
    from agent.workflows.pipeline.agentic_pipeline import AgenticPipelineWorkflow

    proj = _make_project(tmp_path, _STAGE_ONLY, total_written=5)

    def _bare(allow: bool) -> AgenticPipelineWorkflow:
        # 只测该方法的透传语义，故绕过重型构造（llm/planner/editor 等）
        p = object.__new__(AgenticPipelineWorkflow)
        p.project_dir = proj
        p.console = _CollectConsole()
        p._plan_gate_allow_stage_level = allow
        return p

    ledger = proj / ".state" / "plan_gate_waivers.jsonl"
    assert not _bare(False)._preflight_plan_gate()
    assert not ledger.exists(), "pipeline 层在未确认时写了审计链"
    assert not _bare(True)._preflight_plan_gate()
    assert ledger.exists(), (
        "pipeline 层未透传 allow_stage_level —— CLI 的显式确认到不了审计链"
        "（实测曾导致 0 章写出）"
    )
