"""G2 收紧 LLM 质检判定 —— ReaderAppealScorer 离线断言（P0-1 / P0-2 / P0-3 / P0-5）

覆盖：
- P0-1 prompt 关键词断言（issues / 逐项 / 不得合并 / 豁免 / 80+ / 依据）+ 维度标签不含"明显"。
- P0-2 issues 重算（自报 0 但列举 2 条 → value=2.0；_last_eval 含 2 条）。
- P0-3 severity 口径（high+mid 计入、low 不计 → 计数维 value=2）。
- 评分维无 issues 回退（自报 value 直接采用，行为不变）。

纯离线：构造假 LLM（MagicMock spec=LLMClient）返回指定 JSON 字符串。
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock
from types import SimpleNamespace

from agent.core.infra.prompt_manager import pm
from agent.core.quality.scoring.reader_appeal import (
    _EVAL_DIM_LABELS,
    ReaderAppealScorer,
)


def _fake_llm(json_text: str, *, raise_error: bool = False) -> MagicMock:
    llm = MagicMock()
    if raise_error:
        llm.chat.side_effect = RuntimeError("network down")
    else:
        llm.chat.return_value = SimpleNamespace(
            text=json_text,
        )
    return llm


def _project(tmp_path: Path) -> Path:
    # 用 conftest 搭含章节的样例项目，确保 score() 进入真 LLM 解析路径（非空 text）。
    return __import__("tests.conftest", fromlist=["make_project"]).make_project(
        tmp_path, n_chapters=3
    )


# ============================================================
# P0-1 / P0-5 prompt 关键词断言
# ============================================================
def test_prompt_requires_enumeration() -> None:
    system = pm.get("quality.reader_appeal_eval").system
    for kw in ("issues", "逐项", "不得合并", "豁免", "80+", "依据"):
        assert kw in system, f"prompt 缺少关键词 {kw}"
    # 维度标签不应含"明显"（已改为"逐项列举"）
    joined = " ".join(_EVAL_DIM_LABELS.values())
    assert "明显" not in joined


# ============================================================
# P0-2 issues 重算（防自报偏低漏判）
# ============================================================
def test_issues_recompute_count_dim(tmp_path: Path) -> None:
    d = _project(tmp_path)
    # 自报 value=0 但列举 2 条 high → 以 issues 重算 value=2.0
    llm = _fake_llm(
        '{"value": 0, "rationale": "x", '
        '"issues": [{"type": "人设", "severity": "high", "desc": "x"}, '
        '{"type": "人设", "severity": "high", "desc": "y"}]}'
    )
    scorer = ReaderAppealScorer(llm_client=llm)
    val = scorer.score("character_stability_high", d)
    assert val == 2.0
    last = scorer._last_eval.get("character_stability_high", {})
    assert len(last.get("issues", [])) == 2


# ============================================================
# P0-3 severity 口径（high+mid 计入、low 仅上报）
# ============================================================
def test_severity_gate_count_dim(tmp_path: Path) -> None:
    d = _project(tmp_path)
    # high + mid 计入、low 不计 → value=2
    llm = _fake_llm(
        '{"issues": [{"severity": "high"}, {"severity": "mid"}, {"severity": "low"}]}'
    )
    scorer = ReaderAppealScorer(llm_client=llm)
    val = scorer.score("character_stability_high", d)
    assert val == 2.0


# ============================================================
# 评分维无 issues 回退（行为不变）
# ============================================================
def test_score_dim_no_issues_fallback(tmp_path: Path) -> None:
    d = _project(tmp_path)
    # 评分维无 issues → 回退自报 value=88（与既有 test_score_real_score_dim 行为一致）
    llm = _fake_llm('{"value": 88}')
    scorer = ReaderAppealScorer(llm_client=llm)
    val = scorer.score("coherence", d)
    assert val == 88.0


# ============================================================
# 2026-09-09 形状异常护栏（五灵破归档两次批末误升级复盘）
# 根因：解析"成功"但 dimensions/value 键缺失 → 兜底全 0 → 伪失败
# → L3 SCORE_TOO_LOW → L4 拒绝处置 → 升级人工打断写作。
# ============================================================
def test_score_dim_missing_value_key_offline_default(tmp_path: Path) -> None:
    """评分维输出缺 value 键（形状异常）→ 降级 safe_default（满分），而非伪 0 分。"""
    d = _project(tmp_path)
    llm = _fake_llm('{"rationale": "模型漏答 value 键"}')
    scorer = ReaderAppealScorer(llm_client=llm)
    val = scorer.score("readability", d)
    assert val == 100.0  # readability safe_default=100，不会触发 SCORE_TOO_LOW


def test_score_dim_missing_value_key_count_dim_unaffected(tmp_path: Path) -> None:
    """计数维无 issues 且无 value → 仍按 0 处理（0 条问题），行为不变。"""
    d = _project(tmp_path)
    llm = _fake_llm('{"rationale": "无 issues 无 value"}')
    scorer = ReaderAppealScorer(llm_client=llm)
    val = scorer.score("character_stability_high", d)
    assert val == 0.0


def test_parse_appeal_all_zero_dims_offline_shortcircuit(tmp_path: Path) -> None:
    """迷爱看解析结果六维全 0（dimensions 键缺失）→ llm_used=False 离线占位。"""
    llm = _fake_llm('{"one_liner": "模型漏答 dimensions", "suggestions": []}')
    scorer = ReaderAppealScorer(llm_client=llm)
    rep = scorer.score_chapter("第一章 试炼\n\n林凡睁开眼。")
    assert rep.llm_used is False
    # 类级修复（2026-09-12）：缺键统一按形状异常短路（与"全 0"同族），
    # error 文案随首检出的缺失维度而变，只锚定关键语义。
    assert "形状异常" in (rep.error or "")
    assert rep.source == "offline"


def test_parse_appeal_normal_scores_unaffected(tmp_path: Path) -> None:
    """正常评分路径不受护栏影响：llm_used=True 且分值保留。"""
    dims = '{"hook_strength": 80, "payoff_density": 70, "immersion": 90, ' \
           '"character_arc": 85, "world_novelty": 60, "emotion_curve": 75}'
    llm = _fake_llm('{"dimensions": ' + dims + ', "one_liner": "ok"}')
    scorer = ReaderAppealScorer(llm_client=llm)
    rep = scorer.score_chapter("第一章 试炼\n\n林凡睁开眼。")
    assert rep.llm_used is True
    assert rep.dimensions["immersion"] == 90


# ============================================================
# 2026-09-09 形状异常护栏（五灵破归档两次批末误升级复盘）
# 根因：解析"成功"但 dimensions/value 键缺失 → 兜底全 0 → 伪失败
# → L3 SCORE_TOO_LOW → L4 拒绝处置 → 升级人工打断写作。
# ============================================================
def test_score_dim_missing_value_key_offline_default(tmp_path: Path) -> None:
    """评分维输出缺 value 键（形状异常）→ 降级 safe_default（满分），而非伪 0 分。"""
    d = _project(tmp_path)
    llm = _fake_llm('{"rationale": "模型漏答 value 键"}')
    scorer = ReaderAppealScorer(llm_client=llm)
    val = scorer.score("readability", d)
    assert val == 100.0  # readability safe_default=100，不会触发 SCORE_TOO_LOW


def test_score_dim_missing_value_key_count_dim_unaffected(tmp_path: Path) -> None:
    """计数维无 issues 且无 value → 仍按 0 处理（0 条问题），行为不变。"""
    d = _project(tmp_path)
    llm = _fake_llm('{"rationale": "无 issues 无 value"}')
    scorer = ReaderAppealScorer(llm_client=llm)
    val = scorer.score("character_stability_high", d)
    assert val == 0.0


def test_parse_appeal_all_zero_dims_offline_shortcircuit(tmp_path: Path) -> None:
    """迷爱看解析结果六维全 0（dimensions 键缺失）→ llm_used=False 离线占位。"""
    llm = _fake_llm('{"one_liner": "模型漏答 dimensions", "suggestions": []}')
    scorer = ReaderAppealScorer(llm_client=llm)
    rep = scorer.score_chapter("第一章 试炼\n\n林凡睁开眼。")
    assert rep.llm_used is False
    # 类级修复（2026-09-12）：缺键统一按形状异常短路（与"全 0"同族），
    # error 文案随首检出的缺失维度而变，只锚定关键语义。
    assert "形状异常" in (rep.error or "")
    assert rep.source == "offline"


def test_parse_appeal_normal_scores_unaffected(tmp_path: Path) -> None:
    """正常评分路径不受护栏影响：llm_used=True 且分值保留。"""
    dims = '{"hook_strength": 80, "payoff_density": 70, "immersion": 90, ' \
           '"character_arc": 85, "world_novelty": 60, "emotion_curve": 75}'
    llm = _fake_llm('{"dimensions": ' + dims + ', "one_liner": "ok"}')
    scorer = ReaderAppealScorer(llm_client=llm)
    rep = scorer.score_chapter("第一章 试炼\n\n林凡睁开眼。")
    assert rep.llm_used is True
    assert rep.dimensions["immersion"] == 90
