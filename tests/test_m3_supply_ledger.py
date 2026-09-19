"""Task #43 方案 C 红线：逐支线供给完整度台账（2026-09-20）。

问题（真实 LLM 取证，n=1）
────────────────────────────────────────────────────────────────────
M3 规划三条款叠加过载（逐章 × 分批滚动 × 每条支线）⇒ LLM 在输出预算内
只兑现第 7 条、整条丢弃第 8/9 条：5 条支线只有 1 条有 chapter_hooks
（4/5=0），chapter_tiers / plot_points **5/5 全缺**，且**无报错无日志**
（纪律 #21 静默失真）。两个字段都有真实消费者：
  · plot_points → subline.md「情节点序列」→ 写手提示词 ×3 处
    + reader_appeal 读者吸引力评分；
  · chapter_tiers → subline.md「每章强度档位表」小节 → v6 档位解析链。

本红线钉住方案 C 的契约（可见性；供给分层方案 B 见提示词 v7）：
  R1 逐章行计数：`第N章：` 形态，批次标注行不计
  R2 判定三态：full / partial / hooks_missing（显性，不猜）
  R3 台账真落盘：JSONL 每行一支线、含 ts 与三字段读数
  R4 集成行为：渲染完成后写台账 + 有缺口必告警（console 行为级）
  R5 不阻断：台账写入失败 ⇒ 显性告警，M3 产出正常返回
  R6 提示词契约：v7 供给分层声明在场（chapter_hooks > chapter_tiers > plot_points）

配套（方案 B）提示词 ``prompts/m3/outline.md`` v7：优先序总声明 +
chapter_tiers/plot_points「只对已给 hooks 的支线给出，其余空串」。
"""

from __future__ import annotations

import io
import json
from pathlib import Path

from rich.console import Console

from agent.workflows.planning.m3_outline import (
    SUPPLY_LEDGER_RELPATH,
    M3OutlineWorkflow,
)

# ── R1/R2：纯函数契约 ────────────────────────────────────────────────────


class TestCountingAndVerdict:
    def test_count_v6_lines(self) -> None:
        text = (
            "第1章：档位=日常｜章首钩子=a\n"
            "第2章：档位=推进｜章首钩子=b\n"
            "第 3 章：档位=高潮\n"  # 容忍空格
        )
        assert M3OutlineWorkflow._count_chapter_lines(text) == 3

    def test_count_ignores_batch_marker(self) -> None:
        r"""`批次2（第21-40章）：` 不是逐章行（`第\d+章` 后无冒号）。"""
        text = "批次2（第21-40章）：\n第21章：档位=推进\n"
        assert M3OutlineWorkflow._count_chapter_lines(text) == 1

    def test_count_tolerates_empty(self) -> None:
        for junk in ("", None, "只有阶段描述，无逐章行"):
            assert M3OutlineWorkflow._count_chapter_lines(junk) == 0  # type: ignore[arg-type]

    def test_verdict_three_states(self) -> None:
        f = M3OutlineWorkflow._supply_verdict
        assert f(20, 20, 20) == "full"
        assert f(20, 0, 0) == "partial"
        assert f(20, 20, 0) == "partial"
        assert f(0, 0, 0) == "hooks_missing"
        assert f(0, 20, 20) == "hooks_missing", "hooks 缺 ⇒ 一票否决"


# ── R3/R4/R5：落盘与集成行为 ─────────────────────────────────────────────


def _make_workflow(tmp_path: Path) -> tuple[M3OutlineWorkflow, io.StringIO]:
    """最小宿主：tmp_path + stub llm + 可读 console。"""
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=200)
    wf = M3OutlineWorkflow(
        project_dir=tmp_path,
        llm_client=object(),  # 不触 create_gateway
        console=console,
    )
    return wf, buf


def _subline(name: str, hooks: str, tiers: str, plot: str) -> dict:
    return {
        "subline_name": name,
        "goal": "g",
        "characters": "c",
        "conflicts": "cf",
        "constraints": "cs",
        "mainline_relation": "mr",
        "pressure_curve": {"setup": "1-3", "conflict": "4-6", "climax": "7-8", "relief": "9"},
        "chapter_hooks": hooks,
        "chapter_tiers": tiers,
        "plot_points": plot,
    }


_HOOKS_FULL = (
    "第1章：档位=日常｜章首钩子=a\n第2章：档位=推进｜章首钩子=b"
)
_TIERS_FULL = "第1章：日常\n第2章：推进"
_PLOT_FULL = "第1章：甲做了乙\n第2章：丙发现丁"


class TestLedgerIntegration:
    def test_ledger_written_and_gap_warned(self, tmp_path: Path) -> None:
        """R3+R4：一 full 一 hooks_missing ⇒ 台账 2 行 + console 告警含支线名。"""
        wf, buf = _make_workflow(tmp_path)
        sublines = [
            _subline("青囊觉醒", _HOOKS_FULL, _TIERS_FULL, _PLOT_FULL),
            _subline("家族迷局", "", "", ""),  # hooks_missing
        ]
        paths = wf._render_and_save_sublines(sublines)

        assert len(paths) == 2, "M3 产出本身不得受台账影响"
        ledger = tmp_path / SUPPLY_LEDGER_RELPATH
        assert ledger.exists(), "台账必须真落盘"
        rows = [json.loads(l) for l in ledger.read_text(encoding="utf-8").splitlines() if l.strip()]
        assert len(rows) == 2
        by_name = {r["subline"]: r for r in rows}
        assert all(
            {"ts", "subline", "hooks_lines", "tiers_lines", "plot_lines", "verdict"} <= set(r)
            for r in rows
        ), "台账行字段契约"
        assert by_name["S01_青囊觉醒"]["verdict"] == "full"
        assert by_name["S02_家族迷局"]["verdict"] == "hooks_missing"
        out = buf.getvalue()
        assert "家族迷局" in out, "缺口必须告警（含支线名）"
        assert "hooks=0" in out

    def test_full_supply_no_warning(self, tmp_path: Path) -> None:
        """全部完整 ⇒ 不打印告警（少噪音）。"""
        wf, buf = _make_workflow(tmp_path)
        wf._render_and_save_sublines(
            [_subline("青囊觉醒", _HOOKS_FULL, _TIERS_FULL, _PLOT_FULL)]
        )
        assert "供给不完整" not in buf.getvalue()

    def test_ledger_failure_does_not_block(self, tmp_path: Path, monkeypatch) -> None:
        """R5（真实失败路径）：台账写入目标不可写 ⇒ 内层兜底捕获 + 显性警告，
        M3 渲染正常返回。"""
        wf, buf = _make_workflow(tmp_path)
        import agent.workflows.planning.m3_outline as mod

        # 让台账路径指向一个**已存在的目录** ⇒ open("a") 抛 PermissionError。
        # ⚠ 不能只改路径不占位：open("a") 会把不存在的路径创建为文件写成功
        #   （实测踩过——那样根本走不进 except，告警永不出现在 console）。
        monkeypatch.setattr(mod, "SUPPLY_LEDGER_RELPATH", Path(".state"))
        (tmp_path / ".state").mkdir()
        paths = wf._render_and_save_sublines(
            [_subline("青囊觉醒", _HOOKS_FULL, _TIERS_FULL, _PLOT_FULL)]
        )
        assert len(paths) == 1, "台账失败不阻断 M3 产出"
        assert "不影响 M3 产出" in buf.getvalue(), "失败必须显性化（纪律 #1）"

    def test_ledger_failure_defense_in_depth(self, tmp_path: Path, monkeypatch) -> None:
        """R5b（防御深度）：即使内层兜底被绕过，集成层兜底也必须拦住
        （「由 XX 兜底」须验证过——纪律 #13③；两层各被一条红线钉住）。"""
        wf, buf = _make_workflow(tmp_path)

        def _boom(rows):
            raise OSError("disk full")

        monkeypatch.setattr(wf, "_write_supply_ledger", _boom)
        paths = wf._render_and_save_sublines(
            [_subline("青囊觉醒", _HOOKS_FULL, _TIERS_FULL, _PLOT_FULL)]
        )
        assert len(paths) == 1, "台账失败不阻断 M3 产出"
        assert "不影响 M3 产出" in buf.getvalue(), "失败必须显性化（纪律 #1）"


# ── R6：方案 B 提示词契约（v7 供给分层） ─────────────────────────────────


class TestPromptV7Contract:
    def test_priority_declaration_present(self) -> None:
        """v7 总声明在场：三字段优先序（防提示词回退到「三字段全同覆盖」）。"""
        text = (
            Path(__file__).resolve().parents[1]
            / "src/agent/prompts/m3/outline.md"
        ).read_text(encoding="utf-8")
        assert "chapter_hooks` > `chapter_tiers` > `plot_points" in text, (
            "v7 供给优先序声明丢失——三字段全同覆盖 = 实测 LLM 整条丢弃的根因"
        )
        assert "写空串" in text, "显性空串约定丢失"
        # v6 行形态约定不得被 v7 变更破坏
        assert "档位" in text and "批次" in text

    def test_v7_does_not_weaken_hooks_coverage(self) -> None:
        """chapter_hooks 的每支线覆盖要求必须仍在（主链路不放松）。"""
        text = (
            Path(__file__).resolve().parents[1]
            / "src/agent/prompts/m3/outline.md"
        ).read_text(encoding="utf-8")
        assert "每一条支线都必须执行本条的逐章要求" in text
