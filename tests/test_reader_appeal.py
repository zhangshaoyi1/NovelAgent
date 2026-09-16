"""G2 收紧 LLM 质检判定 —— ReaderAppealScorer 离线断言（P0-1 / P0-2 / P0-3 / P0-5）

覆盖：
- P0-1 prompt 关键词断言（issues / 逐项 / 不得合并 / 豁免 / 80+ / 依据）+ 维度标签不含"明显"。
- P0-2 issues 重算（自报 0 但列举 2 条 → value=2.0；_last_eval 含 2 条）。
- P0-3 severity 口径（high+mid 计入、low 不计 → 计数维 value=2）。
- 评分维无 issues 回退（自报 value 直接采用，行为不变）。
- A（2026-09-15）判定口径标尺：提示词判据与门禁线（85/80）同源。
- D（2026-09-15）评委取样窗口 = 回滚窗口 SSOT，且读满整窗。

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


# ============================================================
# 2026-09-16 量纲混淆护栏（登记单 ``20260916_计数维被当分数返回``）
# 根因：计数维偶发返回 0–100 分数（全库 7/309，13–84 完全空档）
# → 阈值 0 + required + 授权整窗回退 ⇒ 单次混淆 = 销毁 5 章。
# 处置：不可信化（confidence=0 ⇒ gate_decision=recheck），不授权回退、不计硬伤。
# ============================================================
def test_count_dim_score_like_value_is_marked_unverified(tmp_path: Path) -> None:
    """★ 计数维返回 95（分数）⇒ **不可信**，不得当成"95 处崩坏"授权回退。

    实测样本：09-16 11:32 灵荒薪传 ``character_stability_high=95.0``
    （confidence=1.0，非降级）正是当日第 5 次回退的触发轮。
    """
    d = _project(tmp_path)
    # issues 为空 ⇒ 走"回退自报 value"分支 ⇒ 95 会直奔阈值 0 的硬闸
    llm = _fake_llm('{"value": 95, "rationale": "主角人设很稳定", "issues": []}')
    scorer = ReaderAppealScorer(llm_client=llm)
    val = scorer.score("character_stability_high", d)

    assert val == 0.0, "越界值不得原样放行（会被读成 95 处崩坏）"
    last = scorer._last_eval.get("character_stability_high", {})
    assert last.get("value") == 0.0, "不得被静默钳制成上界而伪造结论"
    ev = last.get("evidence")
    assert ev is not None and float(getattr(ev, "confidence", 1.0)) == 0.0, (
        "必须携带 confidence=0 证据 ⇒ gate_decision()==recheck，不授权回退"
    )
    assert "量纲混淆" in str(last.get("rationale", "")), "降级理由必须显性可读"


def test_count_dim_legitimate_value_still_counts(tmp_path: Path) -> None:
    """不放松：真正的崩坏（合理条数）照样计 issue（登记单 §六.4）。"""
    d = _project(tmp_path)
    llm = _fake_llm(
        '{"value": 3, "rationale": "x", '
        '"issues": [{"severity": "high"}, {"severity": "high"}, {"severity": "mid"}]}'
    )
    scorer = ReaderAppealScorer(llm_client=llm)
    assert scorer.score("character_stability_high", d) == 3.0


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
# A（2026-09-15）：判定口径标尺 —— 提示词判据必须与门禁线同源
# ============================================================
def test_prompt_has_calibrated_rubric() -> None:
    """提示词须给出与门禁线（coherence≥85 / readability≥80）一致的档位锚点。

    根因 A：旧提示词"只给真正优秀者 80+ / 禁止默认给中间偏上的安全分(70-80)"
    与门禁 85/80 结构冲突 —— 无缺陷的合格文本被夹在"不许给 70-84"与"85+ 须惊艳"
    之间，必然落线 → 整窗回退。此断言锁住标尺锚点存在、且压制指令不再回来。
    """
    system = pm.get("quality.reader_appeal_eval").system
    for kw in ("评分标尺", "达标线", "对号入座", "85", "80+", "先找缺陷"):
        assert kw in system, f"prompt 缺少标尺关键词 {kw}"
    # 与门禁结构冲突的压制指令（把 70-84 划为禁止区）不得回归。
    assert "禁止默认给中间偏上" not in system
    assert "确实出色" not in system
    # 档位必须覆盖达标线的上下边界，评委才可能"对号入座"。
    assert "70-84" in system and "85-91" in system
    assert "65-79" in system and "80-89" in system


# ============================================================
# D（2026-09-15）：评委取样窗口 = 回滚窗口
# ============================================================
def test_eval_window_default_is_rollback_ssot() -> None:
    """D：评委取样窗口缺省 = 回滚窗口 SSOT，且可被显式覆盖。"""
    from agent.core.quality.dimension_registry import EVAL_WINDOW_CHAPTERS

    assert (
        ReaderAppealScorer(llm_client=_fake_llm("{}")).eval_window
        == EVAL_WINDOW_CHAPTERS
    )
    assert ReaderAppealScorer(llm_client=_fake_llm("{}"), eval_window=7).eval_window == 7
    # 非法值兜底为 1，不抛异常（降级不阻断）。
    assert ReaderAppealScorer(llm_client=_fake_llm("{}"), eval_window=0).eval_window == 1


def test_eval_gathers_full_window_not_three(tmp_path: Path) -> None:
    """D：评委必须看到整窗正文 —— 末窗第 4/5 章不能再被漏掉或截断。"""
    d = __import__("tests.conftest", fromlist=["make_project"]).make_project(
        tmp_path, n_chapters=5
    )
    scorer = ReaderAppealScorer(llm_client=_fake_llm("{}"))
    text = scorer._gather_for_eval("coherence", str(d))
    for n in range(1, 6):
        assert f"【ch{n:03d}】" in text, f"整窗第 {n} 章未进入评委样本"
    # prompt 总上限必须容纳整窗，否则"读满全窗"会被静默截断。
    from agent.core.quality.scoring.reader_appeal import (
        _EVAL_PER_CHAPTER_CHARS,
        _PROMPT_CHARS,
    )

    assert _PROMPT_CHARS >= _EVAL_PER_CHAPTER_CHARS * 5


def test_eval_window_override_respected(tmp_path: Path) -> None:
    """D：显式覆盖窗口后，取样范围随之收缩（用户自定义 rollback_window 时同源）。"""
    d = __import__("tests.conftest", fromlist=["make_project"]).make_project(
        tmp_path, n_chapters=5
    )
    scorer = ReaderAppealScorer(llm_client=_fake_llm("{}"), eval_window=3)
    text = scorer._gather_for_eval("coherence", str(d))
    assert "【ch003】" in text
    assert "【ch001】" not in text
