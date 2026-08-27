"""G6 B6 防注水确定性门测试（T7，纯离线确定性指标，零 LLM）

覆盖（对齐设计 §8 关键断言）：
- 整章重复/车轱辘话 → padding_repetition_abnormal 失败（ratio > 0.30）→ overall_pass=False。
- 正常章节 → 零误报（padding 子块 passed=True）。
- 推进句占比 < 0.25 → padding.info_density.flagged=True、overall_pass **不受影响**（软标红）。
- 离线可用（确定性零 LLM）；--no-padding-gate → padding 子块 None、仅剩余闸决定；
  --padding-threshold 覆盖生效。
"""

from __future__ import annotations

from pathlib import Path

from agent.agents.evaluator import EvaluatorAgent


def _write_chapter(p: Path, name: str, text: str) -> None:
    (p / "chapters" / name).write_text(
        f"---\ntitle: {name}\n---\n{text}", encoding="utf-8"
    )


def _normal_chapter(n: int) -> str:
    return (
        f"第{n}章，林云踏入山门。\n"
        "他拔出长剑，剑光如雪。\n"
        "「来吧。」他沉声道。\n"
        "远处传来钟声，众人抬头望去。\n"
        "这一战，他等了十年。\n"
    )


def _make_padding_project(tmp_path: Path, texts: list[tuple[str, str]]) -> Path:
    """texts: [(文件名, 正文)]"""
    ch = tmp_path / "chapters"
    ch.mkdir(parents=True, exist_ok=True)
    for name, text in texts:
        _write_chapter(tmp_path, name, text)
    return tmp_path


def _repetitive_chapter() -> str:
    """20 句完全相同的句子 → 重复句占比 19/20 = 0.95。"""
    return ("林云大步走向前方。\n" * 20)


def _low_density_chapter() -> str:
    """10 句纯描写、无对话/动作/位移信号 → 推进句占比 0.0 < 0.25。"""
    return (
        "窗外的云很白很轻很柔。\n"
        "山间的风很凉很缓很静。\n"
        "远处的灯火很亮很远很淡。\n"
        "檐下的雨很细很长很密。\n"
        "林间的雾很浓很湿很沉。\n"
        "桥下的水很清很浅很急。\n"
        "田里的麦很黄很厚很满。\n"
        "屋角的蛛网很旧很软很脆。\n"
        "桌上的茶很温很香很涩。\n"
        "天边的霞很红很斜很美。\n"
    )


def _make_evaluator(project: Path, **kwargs) -> EvaluatorAgent:
    # G8（拍板 6）：G6 防注水仅测 padding 门禁；G8 验收维度默认开 → 本测试默认关闭
    return EvaluatorAgent(
        project, appeal_scorer=None, mainline_gate=False, ending_gate=False, **kwargs
    )


# ============================================================
# 1. 重复/车轱辘话 → 硬闸失败 → overall_pass=False
# ============================================================
def test_g6_padding_repetition_hard_gate(tmp_path: Path) -> None:
    p = _make_padding_project(tmp_path, [
        ("ch01.md", _normal_chapter(1)),
        ("ch02.md", _repetitive_chapter()),
    ])
    ev = _make_evaluator(p)
    report = ev._evaluate_once()
    d = report.dimension("padding_repetition_abnormal")
    assert d is not None
    assert d.value > 0.30, f"重复句占比应显著超阈值，实际 {d.value}"
    assert d.passed is False
    assert d.required is True, "重复度是硬闸"
    assert d.direction == "<="
    assert report.overall_pass is False
    # padding 子块同步标注失败
    assert report.padding is not None
    assert report.padding["repetition"]["passed"] is False


def test_g6_padding_repetition_triggers_rollback_or_escalate(tmp_path: Path) -> None:
    """重复度硬闸失败 → overall_pass=False → evaluate_with_repair 走 G1 回溯或 escalated。"""
    p = _make_padding_project(tmp_path, [
        ("ch01.md", _normal_chapter(1)),
        ("ch02.md", _repetitive_chapter()),
    ])
    ev = _make_evaluator(p)
    # 不 spy：验证闭环不挂死，返回 escalated（无状态机可回退时）或通过
    result = ev.evaluate_with_repair(lambda chapters: None)
    # 无状态机进度 → trigger_rollback 返回 None → escalated=True（G1 语义不变）
    assert result.overall_pass is False or result.escalated is True
    if result.escalated:
        assert "人工" in result.escalated_reason or "回退" in result.escalated_reason


# ============================================================
# 2. 正常章节 → 零误报
# ============================================================
def test_g6_padding_normal_zero_false_positive(tmp_path: Path) -> None:
    p = _make_padding_project(tmp_path, [
        ("ch01.md", _normal_chapter(1)),
        ("ch02.md", _normal_chapter(2)),
        ("ch03.md", _normal_chapter(3)),
    ])
    ev = _make_evaluator(p)
    report = ev._evaluate_once()
    d = report.dimension("padding_repetition_abnormal")
    assert d is not None and d.passed is True
    assert d.value <= 0.30
    assert report.padding is not None
    assert report.padding["repetition"]["passed"] is True
    assert report.overall_pass is True


# ============================================================
# 3. 信息密度软标红：不进 overall_pass
# ============================================================
def test_g6_padding_info_density_soft_flag(tmp_path: Path) -> None:
    p = _make_padding_project(tmp_path, [
        ("ch01.md", _low_density_chapter()),
    ])
    ev = _make_evaluator(p)
    report = ev._evaluate_once()
    # info_density 不建 DimensionResult（红线：不进 overall_pass）
    assert report.dimension("info_density_abnormal") is None
    assert report.dimension("padding_repetition_abnormal").passed is True
    assert report.padding is not None
    assert report.padding["info_density"]["flagged"] is True, "推进句占比 <0.25 应软标红"
    assert report.padding["info_density"]["advancing_ratio"] < 0.25
    assert report.overall_pass is True, "info_density 软标红不影响 overall_pass"


def test_g6_padding_info_density_normal_not_flagged(tmp_path: Path) -> None:
    p = _make_padding_project(tmp_path, [
        ("ch01.md", _normal_chapter(1)),
    ])
    ev = _make_evaluator(p)
    report = ev._evaluate_once()
    assert report.padding["info_density"]["flagged"] is False


# ============================================================
# 4. 离线可用（确定性零 LLM）
# ============================================================
def test_g6_padding_works_offline(tmp_path: Path) -> None:
    # 无任何 LLM 配置也能算出确定性指标（不抛异常）
    p = _make_padding_project(tmp_path, [
        ("ch01.md", _normal_chapter(1)),
        ("ch02.md", _repetitive_chapter()),
    ])
    ev = _make_evaluator(p)
    ratio, stat = ev._metric_repetition()
    assert ratio > 0.30
    assert stat["total_sentences"] > 0
    dens, dstat = ev._metric_info_density()
    assert dens >= 0.0
    assert dstat["total_sentences"] > 0


# ============================================================
# 5. --no-padding-gate → padding 子块 None、仅剩余闸决定
# ============================================================
def test_g6_padding_gate_disabled(tmp_path: Path) -> None:
    p = _make_padding_project(tmp_path, [
        ("ch01.md", _normal_chapter(1)),
        ("ch02.md", _repetitive_chapter()),
    ])
    ev = _make_evaluator(p, padding_gate=False)
    report = ev._evaluate_once()
    assert report.dimension("padding_repetition_abnormal") is None, "关闭门禁不注入 padding 维度"
    assert report.padding is None, "关闭门禁 padding 子块为 None"
    # 对照组：同项目门禁开启时重复度硬闸失败 → 差异确由 padding 闸引起
    ev_on = _make_evaluator(p, padding_gate=True)
    rep_on = ev_on._evaluate_once()
    assert rep_on.dimension("padding_repetition_abnormal").passed is False
    assert rep_on.padding is not None


# ============================================================
# 6. --padding-threshold 覆盖
# ============================================================
def test_g6_padding_threshold_override(tmp_path: Path) -> None:
    # 构造重复句占比 ~0.15 的项目：8 句正常 + 1 句重复？用可控计数：总句 20，其中 2 句与先前重复
    # 更可控：2 章，每章 10 句，其中某章重复 2 句 → 占比 2/20=0.10
    text = (
        "林云大步走向前方。\n"
        "他停下脚步，望向远方。\n"
        "远处传来一声钟响。\n"
        "林云大步走向前方。\n"  # 重复第 1 句（同字符集）→ 重复
        "他握紧拳头，深吸一口气。\n"
        "夜色渐渐笼罩了山道。\n"
        "林云大步走向前方。\n"  # 重复 → 重复
        "风从谷底吹上来，冷得刺骨。\n"
        "他终于迈出最后一步。\n"
    )
    p = _make_padding_project(tmp_path, [
        ("ch01.md", text),
        ("ch02.md", _normal_chapter(2)),
    ])
    # 宽松 0.50：占比 ~0.10 ≤ 0.50 → 通过
    ev_loose = _make_evaluator(p, padding_threshold=0.50)
    rep_loose = ev_loose._evaluate_once()
    assert rep_loose.dimension("padding_repetition_abnormal").passed is True
    # 严格 0.05：占比 ~0.10 > 0.05 → 失败
    ev_strict = _make_evaluator(p, padding_threshold=0.05)
    rep_strict = ev_strict._evaluate_once()
    d_strict = rep_strict.dimension("padding_repetition_abnormal")
    assert d_strict.passed is False
    assert d_strict.threshold == 0.05


# ============================================================
# 7. padding 子块结构 + to_markdown 小节
# ============================================================
def test_g6_padding_subblock_and_markdown(tmp_path: Path) -> None:
    p = _make_padding_project(tmp_path, [
        ("ch01.md", _normal_chapter(1)),
        ("ch02.md", _low_density_chapter()),
    ])
    ev = _make_evaluator(p)
    report = ev._evaluate_once()
    pd = report.padding
    assert pd is not None
    assert "repetition" in pd and "info_density" in pd
    md = report.to_markdown()
    assert "防注水（B6）" in md
    assert "重复句占比" in md
