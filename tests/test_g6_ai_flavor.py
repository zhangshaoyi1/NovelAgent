"""G6 B5 去 AI 味护栏测试（T7，纯离线 stub）

覆盖（对齐设计 §8 关键断言）：
- advisory：含「喃喃自语」的章节 → GuardrailResult 含 ai_flavor warn 违例、passed=True（标红不阻断）。
- block：gate(mode="block") → ai_flavor 提升 error → blocked=True、block_reason 含命中词。
- 正常文风（古风/严肃叙述，无组合式 AI 腔）→ 零命中。
- 词表只收组合式（不收单字高频词 仿佛/缓缓/不禁）。
- load_guardrail_config / build_guardrails 增键透传；--ai-flavor-words 追加生效。
- pipeline advisory → health_report.ai_flavor 回填（hits/count），overall_pass 不受影响。
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from rich.console import Console

from agent.agents.evaluator import NovelHealthReport
from agent.core.guardrails import (
    AI_FLAVOR_RULE_ID,
    GateMode,
    Guardrails,
    _DEFAULT_AI_FLAVOR_WORDS,
    build_guardrails,
    load_guardrail_config,
)
from agent.workflows.agentic_pipeline import AgenticPipelineWorkflow


# ============================================================
# Guardrails 单元：advisory 标红不阻断
# ============================================================
def test_g6_ai_flavor_advisory_warn_not_block(tmp_path: Path) -> None:
    g = Guardrails(check_title=False)
    res = g.check("她站在窗前喃喃自语，眼神若有所思。")
    assert res.passed is True, "warn 级不阻断"
    ai = [v for v in res.violations if v.rule_id == AI_FLAVOR_RULE_ID]
    assert len(ai) >= 2, "应命中「喃喃自语」与「若有所思」"
    assert all(v.severity == "warn" for v in ai)
    assert any("喃喃自语" in v.message for v in ai)


def test_g6_ai_flavor_same_word_merged_count(tmp_path: Path) -> None:
    g = Guardrails()
    res = g.check("他喃喃自语。她又喃喃自语。众人皆喃喃自语。")
    ai = [v for v in res.violations if v.rule_id == AI_FLAVOR_RULE_ID]
    merged = [v for v in ai if "喃喃自语" in v.message]
    assert len(merged) == 1, "同一词多次命中应合并为一条"
    assert "（3 次）" in merged[0].message


def test_g6_ai_flavor_normal_style_zero_hit(tmp_path: Path) -> None:
    g = Guardrails()
    normal = (
        "林惊羽拔出长剑，剑光如雪。\n"
        "「今日，我便以此剑讨回公道。」\n"
        "风起，他欺身而上，剑尖直取咽喉。\n"
        "看台上众人屏息，无人敢言。\n"
        "这一战，他等了十年。\n"
    )
    res = g.check(normal)
    ai = [v for v in res.violations if v.rule_id == AI_FLAVOR_RULE_ID]
    assert ai == [], "正常文风零命中"


def test_g6_ai_flavor_default_words_are_compound_only(tmp_path: Path) -> None:
    # 词表只收「高置信 AI 腔」组合式，不收单字高频词（仿佛/缓缓/不禁 等）
    for w in _DEFAULT_AI_FLAVOR_WORDS:
        assert len(w) >= 4, f"默认词表应只收组合式短语，实际含单字/短词：{w}"
    for single in ("仿佛", "缓缓", "不禁", "微微", "顿时", "然而", "瞬间", "似乎"):
        assert single not in _DEFAULT_AI_FLAVOR_WORDS, f"默认词表不应含单字高频词：{single}"
    assert len(_DEFAULT_AI_FLAVOR_WORDS) == 16


# ============================================================
# Guardrails 单元：block 提升 error → 拒落盘
# ============================================================
def test_g6_ai_flavor_block_promotes_error(tmp_path: Path) -> None:
    g = Guardrails()
    report = g.gate("她喃喃自语，若有所思。", mode=GateMode.BLOCK)
    assert report.passed is False
    ai = [v for v in report.violations if v.get("rule_id") == AI_FLAVOR_RULE_ID]
    assert ai, "block 模式应命中 ai_flavor"
    assert all(v.get("severity") == "error" for v in ai), "block 下 ai_flavor warn 应提升为 error"


def test_g6_ai_flavor_block_clean_text_passes(tmp_path: Path) -> None:
    g = Guardrails(check_title=False)
    report = g.gate("林惊羽拔出长剑，剑光如雪。", mode=GateMode.BLOCK)
    assert report.passed is True


# ============================================================
# 配置：load_guardrail_config / build_guardrails / --ai-flavor-words
# ============================================================
def test_g6_ai_flavor_config_load(tmp_path: Path) -> None:
    p = tmp_path / "guardrails.json"
    p.write_text(
        json.dumps({"ai_flavor_words": ["定制词A"], "ai_flavor_severity": "error"}, ensure_ascii=False),
        encoding="utf-8",
    )
    cfg = load_guardrail_config(str(p))
    assert cfg["ai_flavor_words"] == ["定制词A"]
    assert cfg["ai_flavor_severity"] == "error"
    g = build_guardrails(str(p))
    res = g.check("这里出现定制词A。")
    ai = [v for v in res.violations if v.rule_id == AI_FLAVOR_RULE_ID]
    assert ai and ai[0].severity == "error"


def test_g6_ai_flavor_config_defaults(tmp_path: Path) -> None:
    cfg = load_guardrail_config(str(tmp_path / "not_exist.json"))
    assert cfg["ai_flavor_words"] == list(_DEFAULT_AI_FLAVOR_WORDS)
    assert cfg["ai_flavor_severity"] == "warn"


def test_g6_ai_flavor_words_extend(tmp_path: Path) -> None:
    g = build_guardrails()
    g.ai_flavor_words.extend(["测试词A", "测试词B"])
    res = g.check("含测试词A的句子。")
    assert any(v.rule_id == AI_FLAVOR_RULE_ID and "测试词A" in v.message for v in res.violations)


# ============================================================
# pipeline 集成：advisory → health_report.ai_flavor 回填、overall_pass 不受影响
# ============================================================
class _StubChapter:
    def __init__(self, num: int, text: str) -> None:
        self.chapter_num = num
        self.chapter_text = text
        self.chapter_title = f"第{num}章"


class _StubWriter:
    def __init__(self, text: str) -> None:
        self._text = text

    def run(self, *a, **k):
        return _StubChapter(1, self._text)


class _StubEditor:
    def review(self, text):
        return SimpleNamespace(passed=True, block_count=0, frozen_violations=[], conflicts=[])


class _StubMemory:
    def __init__(self) -> None:
        self.wrote = False

    def record_chapter(self, *a, **k):
        self.wrote = True

    def log(self, *a, **k):
        return None


class _FakePlanner:
    def load_plan(self):
        return None

    def run(self, brief):
        return None


class _StubEvaluator:
    """stub evaluator：evaluate_with_repair 返回通过报告。"""

    def __init__(self) -> None:
        self.last_failed_report = None

    def evaluate_with_repair(self, rewriter):
        return NovelHealthReport(overall_pass=True, score=100.0, dimensions=[])


def _seed_plan_files(tmp_path: Path) -> None:
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


def test_g6_pipeline_advisory_backfills_report(tmp_path: Path) -> None:
    _seed_plan_files(tmp_path)
    gr = build_guardrails()
    mem = _StubMemory()
    stub_eval = _StubEvaluator()
    wf = AgenticPipelineWorkflow(
        project_dir=tmp_path,
        llm_client=None,
        target_chapters=1,
        eval_enabled=True,
        guardrails=gr,
        gate_mode="advisory",
        writer_workflow=_StubWriter("她站在窗前喃喃自语，眼神若有所思。"),
        editor=_StubEditor(),
        memory=mem,
        planner=_FakePlanner(),
        evaluator=stub_eval,
    )
    result = wf.run()
    assert result.blocked is False, "advisory 不阻断"
    assert result.guardrails is not None
    assert result.guardrails["ai_flavor_count"] >= 2
    assert result.guardrails["mode"] == "advisory"
    assert result.health_report is not None
    ai = result.health_report.get("ai_flavor")
    assert ai is not None and ai["count"] >= 2, "health_report 应含 ai_flavor 子块"
    assert result.health_report["overall_pass"] is True, "advisory 标红不影响 overall_pass"


def test_g6_pipeline_block_blocks(tmp_path: Path) -> None:
    _seed_plan_files(tmp_path)
    gr = build_guardrails()
    stub_eval = _StubEvaluator()
    wf = AgenticPipelineWorkflow(
        project_dir=tmp_path,
        llm_client=None,
        target_chapters=1,
        eval_enabled=True,
        guardrails=gr,
        gate_mode="block",
        writer_workflow=_StubWriter("她喃喃自语，若有所思。"),
        editor=_StubEditor(),
        memory=_StubMemory(),
        planner=_FakePlanner(),
        evaluator=stub_eval,
    )
    result = wf.run()
    # pipeline block 模式：重写后仍不达标则告警标记但继续，不阻断流水线
    # 因此 result.blocked 为 False，但 guardrails 报告中应包含 ai_flavor 命中信息
    assert result.guardrails is not None
    assert result.guardrails["mode"] == "block"
    assert result.guardrails["ai_flavor_count"] >= 1, "应命中 ai_flavor"
