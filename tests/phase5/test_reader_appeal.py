"""B1 真 LLM 追读力 / 迷爱看评分 —— 离线测试

用 conftest.make_project 搭建项目，注入假 LLM（MagicMock spec=LLMClient），
覆盖：
- score() 离线降级（LLM 异常 → 回退 Evaluator 安全默认）
- score() 真 LLM 解析（计数维 / 评分维 映射正确）
- score_chapter() 真 LLM 解析 6 维 + 总分 + 建议
- score_chapter() 离线降级（返回 llm_used=False 占位报告）
- Evaluator 接线（score_fn 注入后维度由 LLM 实判，source 标记）
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from agent.core.llm_client import LLMClient, LLMResponse
from agent.core.reader_appeal import (
    APPEAL_DIMENSIONS,
    ReaderAppealReport,
    ReaderAppealScorer,
)


def _fake_llm(json_text: str, *, raise_error: bool = False) -> MagicMock:
    llm = MagicMock(spec=LLMClient)
    if raise_error:
        llm.chat_utility.side_effect = RuntimeError("network down")
    else:
        llm.chat_utility.return_value = LLMResponse(
            text=json_text,
            raw={},
            usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        )
    return llm


# ============================================================
# score()
# ============================================================
def test_score_offline_fallback(tmp_path):
    d = __import__("tests.conftest", fromlist=["make_project"]).make_project(tmp_path, n_chapters=3)
    scorer = ReaderAppealScorer(llm_client=_fake_llm("", raise_error=True))
    # 硬计数维 → 0；评分维 → 100
    assert scorer.score("character_stability_high", d) == 0.0
    assert scorer.score("logic_holes", d) == 0.0
    assert scorer.score("coherence", d) == 100.0
    assert scorer.score("readability", d) == 100.0


def test_score_real_count_dim(tmp_path):
    d = __import__("tests.conftest", fromlist=["make_project"]).make_project(tmp_path, n_chapters=3)
    # 人设稳定：LLM 报 2 处崩坏 → value=2（非负）
    llm = _fake_llm('{"value": 2, "rationale": "动机前后矛盾"}')
    scorer = ReaderAppealScorer(llm_client=llm)
    assert scorer.score("character_stability_high", d) == 2.0


def test_score_real_score_dim(tmp_path):
    d = __import__("tests.conftest", fromlist=["make_project"]).make_project(tmp_path, n_chapters=3)
    # 连贯性：LLM 给 72 分 → clamp 0-100
    llm = _fake_llm('{"value": 72, "rationale": "衔接略生硬"}')
    scorer = ReaderAppealScorer(llm_client=llm)
    assert scorer.score("coherence", d) == 72.0


# ============================================================
# score_chapter()
# ============================================================
_CHAPTER = "林寻咬破舌尖，精血沁入镜面。「计算完毕，存活率 0.13%。」他选择笨路。"


def test_score_chapter_real(tmp_path):
    llm = _fake_llm(
        '{"dimensions": {"hook_strength": 90, "payoff_density": 80, '
        '"immersion": 70, "character_arc": 60, "world_novelty": 85, '
        '"emotion_curve": 75}, "one_liner": "开局就上强度", '
        '"suggestions": ["加强情绪铺垫", "钩子可更自然"]}'
    )
    scorer = ReaderAppealScorer(llm_client=llm)
    report = scorer.score_chapter(_CHAPTER, title="第1章", genre="xiuxian")

    assert isinstance(report, ReaderAppealReport)
    assert report.llm_used is True
    for k in APPEAL_DIMENSIONS:
        assert report.dimensions.get(k, 0) >= 0
    assert report.dimensions["hook_strength"] == 90
    assert report.total_score > 0
    assert "开局就上强度" in report.one_liner
    assert len(report.suggestions) == 2


def test_score_chapter_offline(tmp_path):
    llm = _fake_llm("", raise_error=True)
    scorer = ReaderAppealScorer(llm_client=llm)
    report = scorer.score_chapter(_CHAPTER)
    assert report.llm_used is False
    assert report.error != ""
    assert report.total_score == 0
    # 占位维度全 0，但结构完整（不抛异常）
    assert all(v == 0 for v in report.dimensions.values())


def test_score_chapter_clamps_and_defaults(tmp_path):
    llm = _fake_llm(
        '{"dimensions": {"hook_strength": 200, "payoff_density": -5}, '
        '"one_liner": "x", "suggestions": []}'
    )
    scorer = ReaderAppealScorer(llm_client=llm)
    report = scorer.score_chapter(_CHAPTER)
    # 越界值被 clamp 到 0-100；缺失维度补 0
    assert report.dimensions["hook_strength"] == 100
    assert report.dimensions["payoff_density"] == 0
    assert report.dimensions["immersion"] == 0


# ============================================================
# Evaluator 接线
# ============================================================
def test_evaluator_wired_with_scorer(tmp_path):
    d = __import__("tests.conftest", fromlist=["make_project"]).make_project(tmp_path, n_chapters=3)
    # 让 scorer 对连贯性给 72、其余维度给 0/100 兜底
    def fake_score(dimension, project_dir):
        if dimension == "coherence":
            return 72.0
        if dimension in ("coherence", "readability"):
            return 100.0
        return 0.0

    from agent.agents.evaluator_agent import EvaluatorAgent

    ev = EvaluatorAgent(d, score_fn=fake_score)
    report = ev._evaluate_once()
    coh = report.dimension("coherence")
    assert coh is not None
    assert coh.value == 72.0
    # 总分应体现连贯性 72（其余满分 → 综合 < 100）
    assert report.score < 100.0


def test_evaluator_offline_defaults(tmp_path):
    d = __import__("tests.conftest", fromlist=["make_project"]).make_project(tmp_path, n_chapters=3)
    from agent.agents.evaluator_agent import EvaluatorAgent

    ev = EvaluatorAgent(d)  # 无 score_fn → 安全默认
    report = ev._evaluate_once()
    # 离线默认：硬计数维 0、评分维 100（与 ReaderAppealScorer._default_for 一致）
    assert report.dimension("character_stability_high").value == 0.0
    assert report.dimension("setting_consistency_high").value == 0.0
    assert report.dimension("logic_holes").value == 0.0
    assert report.dimension("coherence").value == 100.0
    assert report.dimension("readability").value == 100.0
    # 注：确定性维度（伏笔回收率）取决于样例项目，不在此断言
