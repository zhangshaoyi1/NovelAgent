"""F-8（2026-09-07）回归测试：门禁打回重写章号锚定 + 预算窗口口径

背景（五灵破归档 ch13/12 超章事故实证）：
1. Pipeline 门禁（编辑一致性 BLOCK / Guardrails block）打回重写时调用
   ``writer.run(rewrite_hint=...)``，而 Writer 每次 ``run()`` 都按
   ``total_written + 1`` 取新章号 → 上一章刚落盘，"重写"实际写成**下一章**，
   每打回一次悄悄多写一章（ch9/ch11/ch13 即打回溢出产物）。
2. TraceStore 落盘跨轮累计 token，预算判定直接用 ``totals()`` 会把历史轮次
   消耗计入本轮预算 → 旧项目开局即误判超预算（乱降档/乱熔断，
   实测 16:28 轮开局 used=468K > 预算 360K）。
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch


class _FakeTracer:
    def __init__(self, tokens: float) -> None:
        self._tokens = tokens

    def totals(self) -> dict:
        return {"tokens_total": self._tokens}


def _make_plan_fixture(tmp_path: Path) -> None:
    """预置完整规划产物（复用 phase5 离线模式，编排器幂等跳过真实规划）。"""
    (tmp_path / "world.md").write_text(
        "# 测试书\n\n题材：xiuxian\n体量：短篇\n", encoding="utf-8"
    )
    (tmp_path / "discussion.md").write_text("# 脉络讨论（测试占位）\n", encoding="utf-8")
    (tmp_path / "architecture.md").write_text(
        "---\nconfirmed: true\ntheme: 测试\ncore_conflict: 测试\nworld_building: 测试\n"
        "power_system: 测试\nmajor_plotlines: 测试\ncharacter_arcs: 测试\n"
        "pacing: 测试\ntone: 测试\n---\n\n# 故事架构（测试）\n",
        encoding="utf-8",
    )
    (tmp_path / "outline.md").write_text(
        "---\nsublines: []\n---\n\n# 故事大纲（测试）\n", encoding="utf-8"
    )
    chars_dir = tmp_path / "characters"
    chars_dir.mkdir(parents=True, exist_ok=True)
    (chars_dir / "主角.md").write_text(
        "# 主角\n\n- identity: 测试\n- core_motivation: 测试\n", encoding="utf-8"
    )


# ============================================================
# 1. Guardrails block 打回重写 → 必须锚定原章号（不溢出成下一章）
# ============================================================
def test_gate_rewrite_anchors_chapter_num(tmp_path: Path) -> None:
    from agent.core.quality.guardrails import Guardrails
    from agent.workflows.pipeline.agentic_pipeline import AgenticPipelineWorkflow

    calls: list[dict] = []

    class _Ch:
        def __init__(self, text: str) -> None:
            self.chapter_num = 1
            self.chapter_text = text
            self.chapter_title = "第一章"
            self.quality_passed = True

    class _StubWriter:
        def run(self, rewrite_hint=None, chapter_num=None):
            calls.append({"hint": rewrite_hint, "chapter_num": chapter_num})
            if len(calls) == 1:
                return _Ch("这一章含有违规词内容。")  # 首次命中禁用词 → 门禁打回
            return _Ch("干净正文，没有违规词。")  # 重写通过

    class _StubMemory:
        def record_chapter(self, *a, **k):
            pass

    _make_plan_fixture(tmp_path)
    wf = AgenticPipelineWorkflow(
        project_dir=tmp_path,
        llm_client=None,
        target_chapters=1,
        eval_enabled=False,
        guardrails=Guardrails(banned_words=["违规词"]),
        gate_mode="block",
        writer_workflow=_StubWriter(),
        memory=_StubMemory(),
    )
    result = wf.run()
    assert len(calls) == 2, "门禁打回应恰好触发一次重写"
    assert calls[0]["chapter_num"] is None, "首次写章无需锚定"
    assert calls[1]["chapter_num"] == 1, "打回重写必须锚定原章号（F-8）"
    assert calls[1]["hint"], "打回重写应携带修正提示"
    assert result.chapters_written == 1, "打回重写不应多计章数"


# ============================================================
# 2. _used_tokens = totals() - 基线（窗口差值口径）
# ============================================================
def test_used_tokens_subtracts_baseline(tmp_path: Path) -> None:
    from agent.workflows.pipeline.agentic_pipeline import AgenticPipelineWorkflow

    wf = AgenticPipelineWorkflow(tmp_path, console=None)
    assert wf._usage_baseline == 0.0, "默认基线 0（G4 兼容：used == totals）"
    with patch(
        "agent.core.llmops.trace.get_tracer", return_value=_FakeTracer(3_200_000)
    ):
        wf._usage_baseline = 1_000_000.0
        assert wf._used_tokens() == 2_200_000.0
        # 基线大于累计（异常场景）→ 钳制 0，不为负
        wf._usage_baseline = 9_000_000_000.0
        assert wf._used_tokens() == 0.0


# ============================================================
# 3. run() 开头快照基线（跨轮累计历史被剔除）
# ============================================================
def test_run_snapshots_usage_baseline(tmp_path: Path) -> None:
    from agent.workflows.pipeline.agentic_pipeline import AgenticPipelineWorkflow

    wf = AgenticPipelineWorkflow(tmp_path, console=None, eval_enabled=False)
    wf._emit_event = lambda *a, **k: None
    wf._emit_failure = lambda *a, **k: None
    wf._finalize_cost = lambda r: None
    wf._finalize_g9 = lambda r: None

    def _stop() -> None:
        raise RuntimeError("stop-after-baseline")

    wf._ensure_setting_set = _stop  # 基线快照后立即中止（规划阶段异常路径）

    with patch(
        "agent.core.llmops.trace.get_tracer", return_value=_FakeTracer(3_200_000)
    ):
        with patch(
            "agent.workflows.pipeline.plan_consistency.prepare_for_write",
            return_value=[],
        ):
            result = wf.run()
    assert result.blocked is True  # 走到 _ensure_setting_set 异常退出
    assert wf._usage_baseline == 3_200_000.0, "run() 开头必须快照本轮基线"
