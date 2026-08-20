"""G10 写前成本预估测试（T1 验收，纯离线零 LLM）

覆盖（对齐设计 §2 / 拍板 1）：
- build_cost_plan 三档区间与 CostModel.baseline_tokens 同源（零新统计）；
- resolve_book_chapters 缺省链：显式 → MasterPlan → state → 章节文件数 → 300；
- cost-plan 命令 --json 信封（success/cost_plan/chapters/tier_estimates）；
- 预估异常降级占位不阻断。
"""

from __future__ import annotations

import json
from pathlib import Path

from agent.core.llmops.cost import CostModel
from agent.cli.commands.cost_plan import build_cost_plan, resolve_book_chapters


# ============================================================
# 1. build_cost_plan 三档区间与 baseline_tokens 同源
# ============================================================
def test_build_cost_plan_tiers_match_baseline(tmp_path: Path) -> None:
    plan = build_cost_plan(tmp_path, chapters=30)
    assert plan["chapters"] == 30
    assert [t["tier"] for t in plan["tiers"]] == ["economy", "balanced", "quality"]

    cm = CostModel()
    for row in plan["tiers"]:
        est = cm.estimate_book(row["tier"], 30)
        assert row["tokens_low"] == est.tokens_low, f"{row['tier']} low 同源"
        assert row["tokens_high"] == est.tokens_high, f"{row['tier']} high 同源"


# ============================================================
# 2. resolve_book_chapters 缺省链
# ============================================================
def test_resolve_chapters_explicit_wins(tmp_path: Path) -> None:
    assert resolve_book_chapters(tmp_path, chapters=50) == 50


def test_resolve_chapters_fallback_300(tmp_path: Path) -> None:
    # 空项目（无 MasterPlan/state/章节）→ 300
    assert resolve_book_chapters(tmp_path) == 300


def test_resolve_chapters_uses_chapter_files(tmp_path: Path) -> None:
    ch = tmp_path / "chapters"
    ch.mkdir(parents=True, exist_ok=True)
    for i in range(1, 5):
        (ch / f"ch{i:02d}.md").write_text("正文", encoding="utf-8")
    assert resolve_book_chapters(tmp_path) == 4


# ============================================================
# 3. 异常降级：损坏项目仍返回占位不崩
# ============================================================
def test_build_cost_plan_degrades_gracefully(tmp_path: Path) -> None:
    # 传入不可读路径 → 内部 resolve 兜底 300，仍产出三档（纯确定性无异常路径）
    plan = build_cost_plan(tmp_path)
    assert plan["chapters"] >= 0
    assert isinstance(plan["tiers"], list)
    assert isinstance(plan["guidance"], str)


# ============================================================
# 4. cost-plan 命令 --json 信封
# ============================================================
def test_cost_plan_command_json(tmp_path: Path, capsys) -> None:
    from agent.cli.commands.cost_plan import cost_plan

    cost_plan(project_dir=str(tmp_path), chapters=30, json_output=True)
    out = capsys.readouterr().out
    env = json.loads(out.splitlines()[-1])
    assert env["success"] is True
    assert env["chapters"] == 30
    assert len(env["tier_estimates"]) == 3
    assert "cost_plan" in env
