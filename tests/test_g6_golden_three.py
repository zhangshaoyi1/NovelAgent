"""G6 B4 黄金三章门禁测试（T7，纯离线 stub）

覆盖（对齐设计 §8 关键断言）：
- 低分（前三章 hook_strength < 40 或 total < 60）→ golden_* 失败 → overall_pass=False
  → evaluate_with_repair 直接 escalated=True（附前三章明细 + 人工重写指令），
  **spy trigger_rollback 断言未被调用**（全书 >8 章场景，回退区间不覆盖 1-3）。
- 高分 → 通过、零回溯。
- LLM 抛异常（llm_used=False）→ golden_* 全部 passed=True、golden_three.source="offline"、
  overall_pass 不受影响、不 escalated。
- 三章拼接超 10000 字 → fallback=True、逐维取 min。
- --no-golden-three-gate → 零影响；--golden-three-threshold 覆盖生效。

注意（G5 教训）：stub 注入 score_chapter 的测试必须给临时项目造 chapters/ch*.md 目录，
否则 gate_first_chapters/gate_chapter 走"无章节→离线占位"分支导致假通过。
"""

from __future__ import annotations

from pathlib import Path

from rich.console import Console

from agent.agents.evaluator_agent import EvaluatorAgent
from agent.core.chapters import (
    list_chapter_files,
    read_chapters_text,
    strip_frontmatter,
    take_chapter_files,
)
from agent.core.reader_appeal import (
    APPEAL_DIMENSIONS,
    GOLDEN_DIM_FLOOR,
    GOLDEN_JOIN_CHAR_LIMIT,
    GOLDEN_PASS_LINE,
    ReaderAppealReport,
    ReaderAppealScorer,
    gate_first_chapters,
)


class _StubAppealScorer(ReaderAppealScorer):
    """stub：score_chapter 返回预设报告，绝不触碰 LLM。"""

    def __init__(self, report: ReaderAppealReport) -> None:
        super().__init__(console=Console())
        self._report = report
        self.calls = 0

    def score_chapter(self, chapter_text, *, title="", genre="", synopsis=""):
        self.calls += 1
        return self._report


class _StubSequenceScorer(ReaderAppealScorer):
    """stub：按调用顺序依次返回报告（fallback 每章独立评分取最差用）。"""

    def __init__(self, reports: list[ReaderAppealReport]) -> None:
        super().__init__(console=Console())
        self._reports = list(reports)

    def score_chapter(self, chapter_text, *, title="", genre="", synopsis=""):
        if not self._reports:
            return self._reports[-1]  # pragma: no cover - 不应发生
        return self._reports.pop(0)


def _make_report(dims: dict[str, int], *, llm_used: bool = True) -> ReaderAppealReport:
    return ReaderAppealReport(
        dimensions=dims,
        total_score=ReaderAppealReport._compute_total(dims),
        one_liner="测试一句话感受",
        suggestions=["改进建议1"],
        llm_used=llm_used,
        source="offline" if not llm_used else "llm",
    )


def _chapter_text(n: int) -> str:
    return (
        f"第{n}章，林云踏入山门。\n"
        "他拔出长剑，剑光如雪。\n"
        "「来吧。」他沉声道。\n"
        "远处传来钟声，众人抬头望去。\n"
        "这一战，他等了十年。\n"
    )


def _make_g6_project(tmp_path: Path, n_chapters: int = 10) -> Path:
    """造含 n 章（默认 10，>8 使回溯区间不覆盖 1-3）正文的最小项目。"""
    ch = tmp_path / "chapters"
    ch.mkdir(parents=True, exist_ok=True)
    for n in range(1, n_chapters + 1):
        (ch / f"ch{n:02d}.md").write_text(
            f"---\ntitle: 第{n}章\n---\n{_chapter_text(n)}",
            encoding="utf-8",
        )
    (tmp_path / "world.md").write_text(
        "## 故事简介\n一个用于测试的小世界。", encoding="utf-8"
    )
    return tmp_path


# ============================================================
# 0. T1 公共 helper 单测（chapters.py）
# ============================================================
def test_chapters_helper_first_and_last(tmp_path: Path) -> None:
    p = _make_g6_project(tmp_path, n_chapters=5)
    files = list_chapter_files(p)
    assert [f.name for f in files] == ["ch01.md", "ch02.md", "ch03.md", "ch04.md", "ch05.md"]
    # side="first" 取前 3；side="last" 取末 1
    first3 = take_chapter_files(files, side="first", n=3)
    last1 = take_chapter_files(files, side="last", n=1)
    assert [f.name for f in first3] == ["ch01.md", "ch02.md", "ch03.md"]
    assert [f.name for f in last1] == ["ch05.md"]
    texts = read_chapters_text(p, side="first", n=3)
    assert len(texts) == 3
    assert "林云踏入山门" in texts[0]
    assert not texts[0].startswith("---"), "read_chapters_text 应已去 frontmatter"


def test_strip_frontmatter() -> None:
    assert strip_frontmatter("---\ntitle: x\n---\n正文") == "\n正文"
    assert strip_frontmatter("无 frontmatter 正文") == "无 frontmatter 正文"


# ============================================================
# 1. 低分（hook < 40 或 total < 60）→ escalated 且不触发回退
# ============================================================
def test_g6_golden_low_floor_escalates_no_rollback(tmp_path: Path) -> None:
    dims = {k: 80 for k in APPEAL_DIMENSIONS}
    dims["hook_strength"] = 30  # 单维 < 40 触底；其余高 → 综合 ≈ 66 ≥ 60
    stub = _StubAppealScorer(_make_report(dims))
    ev = EvaluatorAgent(
        _make_g6_project(tmp_path), appeal_scorer=None,
        golden_scorer=stub, golden_three_gate=True,
        golden_three_threshold=60, golden_three_floor=40,
    )
    report = ev._evaluate_once()
    hook = report.dimension("golden_hook_strength")
    assert hook is not None and hook.passed is False, "hook_strength < 40 应判失败"
    assert report.overall_pass is False

    # spy trigger_rollback：断言 golden 失败分支绝不调用回退
    calls: list = []
    original = ev.trigger_rollback

    def spy(*a, **k):
        calls.append((a, k))
        return original(*a, **k)

    ev.trigger_rollback = spy
    result = ev.evaluate_with_repair(lambda chapters: None)
    assert result.escalated is True, "golden 失败应直接 escalated"
    assert calls == [], "golden 失败绝不允许触发 trigger_rollback（无效回退）"
    assert "请人工重写第 1-3 章" in result.escalated_reason
    assert "金三·钩子强度" in result.escalated_reason
    assert result.rollback_attempts == 0, "不消耗回溯预算"


def test_g6_golden_low_total_escalates(tmp_path: Path) -> None:
    dims = {k: 30 for k in APPEAL_DIMENSIONS}  # 综合=30 < 60 且每维 < 40
    stub = _StubAppealScorer(_make_report(dims))
    ev = EvaluatorAgent(
        _make_g6_project(tmp_path), appeal_scorer=None,
        golden_scorer=stub, golden_three_gate=True,
    )
    result = ev.evaluate_with_repair(lambda chapters: None)
    total = result.dimension("golden_total")
    assert total is not None and total.passed is False
    assert result.escalated is True
    assert "请人工重写第 1-3 章" in result.escalated_reason
    assert "金三·综合" in result.escalated_reason
    # 明细含前三章各维得分与综合分
    assert "综合分" in result.escalated_reason


# ============================================================
# 2. 高分 → 通过、零回溯
# ============================================================
def test_g6_golden_high_passes_zero_rollback(tmp_path: Path) -> None:
    dims = {k: 80 for k in APPEAL_DIMENSIONS}
    stub = _StubAppealScorer(_make_report(dims))
    ev = EvaluatorAgent(
        _make_g6_project(tmp_path), appeal_scorer=None,
        golden_scorer=stub, golden_three_gate=True,
    )
    called: list[list[int]] = []

    def rewriter(chapter_nums: list[int]) -> None:
        called.append(list(chapter_nums))

    report = ev.evaluate_with_repair(rewriter)
    gdims = [d for d in report.dimensions if d.name.startswith("golden_")]
    assert gdims, "应注入 golden_* 维度"
    assert all(d.passed for d in gdims), "高分应全部判通过"
    assert report.overall_pass is True
    assert called == [], "高分不应触发回溯"
    assert report.rollback_attempts == 0
    assert report.escalated is False


# ============================================================
# 3. 离线短路（llm_used=False）→ 全部 passed、source="offline"、不 escalated
# ============================================================
def test_g6_golden_offline_short_circuit(tmp_path: Path) -> None:
    stub = _StubAppealScorer(
        _make_report({k: 0 for k in APPEAL_DIMENSIONS}, llm_used=False)
    )
    ev = EvaluatorAgent(
        _make_g6_project(tmp_path), appeal_scorer=None,
        golden_scorer=stub, golden_three_gate=True,
        golden_three_threshold=75, golden_three_floor=45,  # CLI 覆盖后离线仍须通过
    )
    report = ev.evaluate_with_repair(lambda chapters: None)
    gdims = [d for d in report.dimensions if d.name.startswith("golden_")]
    assert len(gdims) == len(APPEAL_DIMENSIONS) + 1
    for d in gdims:
        assert d.passed is True, f"{d.name} 离线应强制通过"
        assert d.source == "offline"
        if d.name == "golden_total":
            assert d.value == float(75), "离线综合 value 应取 CLI 覆盖后阈值"
        else:
            assert d.value == float(45), "离线单维 value 应取 CLI 覆盖后 floor"
    assert report.overall_pass is True
    assert report.escalated is False, "离线短路禁止误 escalated"
    assert report.golden_three is not None
    assert report.golden_three["source"] == "offline"
    assert report.golden_three["passed"] is True


# ============================================================
# 4. 三章拼接超 10000 字 → fallback 每章独立取最差
# ============================================================
def test_g6_golden_join_fallback_worst(tmp_path: Path) -> None:
    # 每章 > 4000 字 → 三章拼接 > 10000 → fallback
    ch = tmp_path / "chapters"
    ch.mkdir(parents=True, exist_ok=True)
    for n in range(1, 4):
        (ch / f"ch0{n}.md").write_text(
            f"---\ntitle: 第{n}章\n---\n" + ("这是超长章节正文内容用于触发回退分支。" * 400),
            encoding="utf-8",
        )
    dims_a = {k: 90 for k in APPEAL_DIMENSIONS}
    dims_b = {k: 70 for k in APPEAL_DIMENSIONS}
    dims_c = {k: 50 for k in APPEAL_DIMENSIONS}
    dims_c["emotion_curve"] = 30
    stub = _StubSequenceScorer([
        _make_report(dims_a),
        _make_report(dims_b),
        _make_report(dims_c),
    ])
    report = gate_first_chapters(stub, tmp_path, 3)
    assert report.fallback is True
    assert report.chapters_scored == 3
    assert report.dimensions["hook_strength"] == 50, "逐维取 min"
    assert report.dimensions["emotion_curve"] == 30, "逐维取 min（最差章）"
    assert report.dimensions["world_novelty"] == 50
    assert report.total_score == ReaderAppealReport._compute_total(dims_c), "total 取 min"


def test_g6_golden_join_under_limit_single_call(tmp_path: Path) -> None:
    stub = _StubAppealScorer(_make_report({k: 80 for k in APPEAL_DIMENSIONS}))
    report = gate_first_chapters(stub, _make_g6_project(tmp_path, n_chapters=3), 3)
    assert report.fallback is False
    assert report.chapters_scored == 1
    assert stub.calls == 1, "拼接 ≤10000 字应一次评分"


def test_g6_golden_no_chapters_placeholder(tmp_path: Path) -> None:
    stub = _StubAppealScorer(_make_report({k: 80 for k in APPEAL_DIMENSIONS}))
    report = gate_first_chapters(stub, tmp_path, 3)
    assert report.llm_used is False
    assert report.error == "no chapters dir"
    assert report.source == "offline"


# ============================================================
# 5. --no-golden-three-gate → 零影响
# ============================================================
def test_g6_golden_gate_disabled(tmp_path: Path) -> None:
    dims = {k: 0 for k in APPEAL_DIMENSIONS}  # 若门禁开必失败
    stub = _StubAppealScorer(_make_report(dims))
    ev = EvaluatorAgent(
        _make_g6_project(tmp_path), appeal_scorer=None,
        golden_scorer=stub, golden_three_gate=False,
    )
    report = ev._evaluate_once()
    gdims = [d for d in report.dimensions if d.name.startswith("golden_")]
    assert gdims == [], "关闭门禁不应注入 golden_*"
    assert report.golden_three is None
    assert report.overall_pass is True, "仅七维（无章节时全过）决定"


# ============================================================
# 6. --golden-three-threshold / --golden-three-floor 覆盖
# ============================================================
def test_g6_golden_threshold_override(tmp_path: Path) -> None:
    dims = {k: 65 for k in APPEAL_DIMENSIONS}  # 综合=65
    stub = _StubAppealScorer(_make_report(dims))
    # 默认 60：65 ≥ 60 → golden_total 通过
    ev_default = EvaluatorAgent(
        _make_g6_project(tmp_path), appeal_scorer=None,
        golden_scorer=stub, golden_three_gate=True, golden_three_threshold=60,
    )
    rep_def = ev_default._evaluate_once()
    assert rep_def.dimension("golden_total").passed is True
    assert rep_def.overall_pass is True
    # 更严 75：65 < 75 → golden_total 失败
    ev_strict = EvaluatorAgent(
        _make_g6_project(tmp_path), appeal_scorer=None,
        golden_scorer=stub, golden_three_gate=True, golden_three_threshold=75,
    )
    rep_strict = ev_strict._evaluate_once()
    assert rep_strict.dimension("golden_total").passed is False
    assert rep_strict.overall_pass is False


def test_g6_golden_floor_override(tmp_path: Path) -> None:
    dims = {k: 80 for k in APPEAL_DIMENSIONS}
    dims["payoff_density"] = 50  # 默认 floor 40 通过；更严 floor 55 失败
    stub = _StubAppealScorer(_make_report(dims))
    ev_loose = EvaluatorAgent(
        _make_g6_project(tmp_path), appeal_scorer=None,
        golden_scorer=stub, golden_three_gate=True, golden_three_floor=40,
    )
    assert ev_loose._evaluate_once().dimension("golden_payoff_density").passed is True
    ev_strict = EvaluatorAgent(
        _make_g6_project(tmp_path), appeal_scorer=None,
        golden_scorer=stub, golden_three_gate=True, golden_three_floor=55,
    )
    assert ev_strict._evaluate_once().dimension("golden_payoff_density").passed is False


# ============================================================
# 7. 常量与 golden_three 子块结构
# ============================================================
def test_g6_golden_constants() -> None:
    assert GOLDEN_PASS_LINE == 60
    assert GOLDEN_DIM_FLOOR == 40
    assert GOLDEN_JOIN_CHAR_LIMIT == 10000


def test_g6_golden_three_subblock(tmp_path: Path) -> None:
    dims = {k: 80 for k in APPEAL_DIMENSIONS}
    stub = _StubAppealScorer(_make_report(dims))
    ev = EvaluatorAgent(
        _make_g6_project(tmp_path), appeal_scorer=None,
        golden_scorer=stub, golden_three_gate=True,
    )
    report = ev._evaluate_once()
    g = report.golden_three
    assert g is not None
    assert g["source"] == "llm"
    assert g["mode"] == "join"
    assert g["total_score"] == 80
    assert g["threshold"] == 60
    assert g["floor"] == 40
    assert g["passed"] is True
    assert set(g["dimensions"].keys()) == set(APPEAL_DIMENSIONS)
    assert g["dimensions"]["hook_strength"]["score"] == 80
    md = report.to_markdown()
    assert "黄金三章" in md
