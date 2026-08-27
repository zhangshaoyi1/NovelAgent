"""G7 人话总结层测试（T1-T3 验收，纯离线）

覆盖（对齐 PRD §8 / 设计 §8 T1-T3）：
- 注入 2 个失败维 → `_build_summary` 生成 headline/failures（line 含「实测 X ＞ 合格线 Y（差 Z）」
  按 direction 严格计算）/next_steps；建议来源 llm/template 正确标注。
- `to_markdown` 顶部（表格行前）含「## 一句话总结」+ 失败维自然语言句 + 建议（注明来源）；
  **表格结构与 G5/G6 子块逐字节零改动**（diff 快照断言）。
- 全通过 → headline「全部达标」；离线（source=offline 且全通过）→「全部达标（离线通过未实测）」。
- `human_summary=False` → `summary=None`；`to_dict()` 增 `summary` 键、既有 15 键零改动。
- `ReaderAppealReport.summary_lines` + `build_appeal_summary_lines` + `to_markdown` 顶部总结段
  （失败维句 / LLM 建议 / 离线返回 [] / to_dict 增 summary_lines）。

零 LLM、零网络：所有素材直接构造 stub 对象，不触碰 LLMClient。
"""

from __future__ import annotations

from pathlib import Path

from agent.agents.evaluator import DimensionResult, EvaluatorAgent, NovelHealthReport
from agent.core.reader_appeal import (
    APPEAL_DIMENSIONS,
    ReaderAppealReport,
    build_appeal_summary_lines,
)


# ============================================================
# 辅助：构造含失败维的报告
# ============================================================
def _make_failing_report() -> NovelHealthReport:
    """两个失败维（pacing_abnormal 方向<= 越界；coherence 方向>= 未达标）+ 一个通过维。"""
    dims = [
        DimensionResult("pacing_abnormal", "节奏异常", 0.05, 0.03, "<=", False, "computed"),
        DimensionResult("coherence", "连贯性", 70.0, 85.0, ">=", False, "llm/default"),
        DimensionResult("character_stability_high", "人设稳定", 0.0, 0.0, "<=", True, "llm/default"),
    ]
    report = NovelHealthReport(overall_pass=False, score=78.4, dimensions=dims)
    report.appeal = {
        "source": "llm",
        "total_score": 55,
        "threshold": 60,
        "floor": 40,
        "verdict": "勉强能看",
        "dimensions": {},
        "passed": False,
        "one_liner": "钩子偏弱",
        "suggestions": ["建议加强章末悬念", "建议压缩注水段落、匀出节奏"],
    }
    return report


# ============================================================
# 1. _build_summary：失败维归因 + 差距按 direction 严格计算
# ============================================================
def test_build_summary_failures(tmp_path: Path) -> None:
    ev = EvaluatorAgent(tmp_path)
    report = _make_failing_report()
    s = ev._build_summary(report)

    assert s["all_passed"] is False
    assert s["offline"] is False
    assert "全书不达标" in s["headline"]
    assert "2 个维度未过线" in s["headline"]
    assert "78.4" in s["headline"]

    by_dim = {f["dimension"]: f for f in s["failures"]}
    assert set(by_dim.keys()) == {"pacing_abnormal", "coherence"}

    # pacing_abnormal：direction "<="，value 0.05 > threshold 0.03 → 差 = 0.05-0.03 = 0.02
    pace = by_dim["pacing_abnormal"]
    assert pace["gap"] == 0.02
    assert pace["direction"] == "<="
    assert "0.05" in pace["line"] and "0.03" in pace["line"] and "0.02" in pace["line"]
    assert "＞" in pace["line"], "direction<= 未达标应为 实测 ＞ 合格线"

    # coherence：direction ">="，value 70 < threshold 85 → 差 = 85-70 = 15
    coh = by_dim["coherence"]
    assert coh["gap"] == 15.0
    assert coh["direction"] == ">="
    assert "70.0" in coh["line"] and "85.0" in coh["line"] and "15.0" in coh["line"]
    assert "＜" in coh["line"], "direction>= 未达标应为 实测 ＜ 合格线"

    # 建议来源：LLM suggestions 优先（appeal 子块有 suggestions → source=llm）
    assert pace["suggestion_source"] == "llm"
    assert pace["suggestion"] == "建议加强章末悬念"
    assert coh["suggestion_source"] == "llm"
    # next_steps 去重（两条失败维共享同一 LLM 建议池首条）
    assert s["next_steps"] == ["建议加强章末悬念"]


def test_build_summary_template_fallback(tmp_path: Path) -> None:
    """无 LLM suggestions（appeal/golden 子块为空）→ 维度级模板兜底，来源=template。"""
    ev = EvaluatorAgent(tmp_path)
    report = NovelHealthReport(
        overall_pass=False,
        score=50.0,
        dimensions=[
            DimensionResult("logic_holes", "逻辑漏洞", 2.0, 0.0, "<=", True, "llm/default"),
        ],
    )
    s = ev._build_summary(report)
    f = s["failures"][0]
    assert f["suggestion_source"] == "template"
    assert f["reason"] == "存在逻辑漏洞，建议修复因果硬伤"
    assert "逻辑漏洞" in f["suggestion"]


# ============================================================
# 2. 全通过 / 离线 / 未知维度兜底
# ============================================================
def test_build_summary_all_passed(tmp_path: Path) -> None:
    ev = EvaluatorAgent(tmp_path)
    report = NovelHealthReport(
        overall_pass=True,
        score=95.0,
        dimensions=[
            DimensionResult("pacing_abnormal", "节奏异常", 0.01, 0.03, "<=", False, "computed"),
        ],
    )
    s = ev._build_summary(report)
    assert s["headline"] == "全部达标"
    assert s["failures"] == []
    assert s["next_steps"] == []
    assert s["all_passed"] is True
    assert s["offline"] is False


def test_build_summary_offline_all_passed(tmp_path: Path) -> None:
    ev = EvaluatorAgent(tmp_path)
    report = NovelHealthReport(
        overall_pass=True,
        score=90.0,
        dimensions=[
            DimensionResult("appeal_hook_strength", "迷·钩子强度", 40.0, 40.0, ">=", False, "offline"),
        ],
    )
    s = ev._build_summary(report)
    assert s["headline"] == "全部达标（离线通过未实测）"
    assert s["offline"] is True
    assert s["all_passed"] is True


# ============================================================
# 3. to_markdown 顶部总结段 + 表格/子块逐字节零改动
# ============================================================
def test_markdown_summary_section_before_table(tmp_path: Path) -> None:
    ev = EvaluatorAgent(tmp_path)
    report = _make_failing_report()
    md_without = report.to_markdown()  # summary=None：不插总结段（基线）
    report.summary = ev._build_summary(report)
    md = report.to_markdown()

    assert "## 一句话总结" in md
    assert "**全书不达标：2 个维度未过线（综合分 78.4/100）**" in md
    assert "节奏异常：实测 0.05 ＞ 合格线 0.03（差 0.02）" in md
    assert "连贯性：实测 70.0 ＜ 合格线 85.0（差 15.0）" in md
    assert "建议（来自 LLM）：建议加强章末悬念" in md
    assert "下一步：建议加强章末悬念" in md

    # 总结段必须在表格之前
    assert md.index("## 一句话总结") < md.index("| 维度 | 指标 | 实测 | 合格线 | 达标 |")

    # 表格结构与 G5/G6 子块逐字节零改动：从表头开始的后半部分与无总结版完全一致
    def _table_section(md_text: str) -> str:
        lines = md_text.splitlines()
        idx = next(i for i, l in enumerate(lines) if l.startswith("| 维度 | 指标 |"))
        return "\n".join(lines[idx:])

    assert _table_section(md) == _table_section(md_without), (
        "总结段插入后表格/G5/G6 子块必须逐字节不变"
    )


def test_markdown_summary_none_skips_section(tmp_path: Path) -> None:
    """summary=None（如 human_summary=False）→ 总结段整段跳过，表格原样。"""
    report = NovelHealthReport(overall_pass=True, score=100.0, dimensions=[])
    md = report.to_markdown()
    assert "## 一句话总结" not in md
    assert "| 维度 | 指标 | 实测 | 合格线 | 达标 |" in md


# ============================================================
# 4. _evaluate_once 接线 + human_summary 开关
# ============================================================
def test_evaluate_once_fills_summary_by_default(tmp_path: Path) -> None:
    # G8（拍板 6）：G7 人话总结仅测 summary 层；G8 验收维度默认开 → 本测试关闭
    ev = EvaluatorAgent(tmp_path, human_summary=True, mainline_gate=False, ending_gate=False)
    report = ev._evaluate_once()
    assert report.summary is not None
    assert report.summary["all_passed"] is True
    assert report.summary["headline"] == "全部达标"


def test_human_summary_false_no_summary(tmp_path: Path) -> None:
    ev = EvaluatorAgent(tmp_path, human_summary=False)
    report = ev._evaluate_once()
    assert report.summary is None
    md = report.to_markdown()
    assert "## 一句话总结" not in md


# ============================================================
# 5. to_dict：summary 键只增不删
# ============================================================
def test_to_dict_summary_field() -> None:
    report = NovelHealthReport(overall_pass=True, score=100.0)
    report.summary = {"headline": "全部达标", "failures": [], "next_steps": [],
                      "offline": False, "all_passed": True}
    d = report.to_dict()
    assert d["summary"] == report.summary
    for key in ("overall_pass", "score", "dimensions", "rolled_back", "rollback_attempts",
                "escalated", "escalated_reason", "repair", "notes", "appeal",
                "golden_three", "ai_flavor", "padding"):
        assert key in d, f"既有键 {key} 必须保留（只增不删）"
    assert len(d) == 14, "既有 13 键 + 新增 summary = 14"


# ============================================================
# 6. ReaderAppealReport.summary_lines + build_appeal_summary_lines
# ============================================================
def _make_appeal_report(dims: dict[str, int], *, llm_used: bool = True,
                        suggestions: list[str] | None = None) -> ReaderAppealReport:
    return ReaderAppealReport(
        dimensions=dims,
        total_score=ReaderAppealReport._compute_total(dims),
        one_liner="测试一句话感受",
        suggestions=suggestions or [],
        llm_used=llm_used,
        source="llm" if llm_used else "offline",
    )


def test_appeal_summary_lines_fail_dim() -> None:
    dims = {"hook_strength": 35, "payoff_density": 50, "immersion": 60,
            "character_arc": 60, "world_novelty": 60, "emotion_curve": 60}
    report = _make_appeal_report(dims, suggestions=["加强章末悬念"])
    lines = build_appeal_summary_lines(report)
    assert any("综合分" in ln and "未达合格线 60" in ln for ln in lines)
    assert any("钩子强度：实测 35 ＜ 触底线 40（差 5）" in ln for ln in lines)
    assert any("来自 LLM" in ln for ln in lines)
    assert any("加强章末悬念" in ln for ln in lines)


def test_appeal_summary_lines_all_pass_template() -> None:
    dims = {k: 80 for k in APPEAL_DIMENSIONS}
    report = _make_appeal_report(dims, suggestions=[])
    lines = build_appeal_summary_lines(report)
    assert any("下一步建议（模板）" in ln for ln in lines)


def test_appeal_summary_lines_offline_empty() -> None:
    dims = {k: 0 for k in APPEAL_DIMENSIONS}
    report = _make_appeal_report(dims, llm_used=False)
    assert build_appeal_summary_lines(report) == [], "离线（llm_used=False）应返回 []"


def test_appeal_markdown_summary_section() -> None:
    dims = {"hook_strength": 35, "payoff_density": 50, "immersion": 60,
            "character_arc": 60, "world_novelty": 60, "emotion_curve": 60}
    report = _make_appeal_report(dims, suggestions=["加强章末悬念"])
    report.summary_lines = build_appeal_summary_lines(report)
    md = report.to_markdown()
    assert "## 一句话总结" in md
    assert "钩子强度：实测 35 ＜ 触底线 40（差 5）" in md
    assert md.index("## 一句话总结") < md.index("| 维度 | 得分 |")
    # 表格原样
    assert "| 维度 | 得分 |" in md


def test_appeal_offline_markdown_unchanged() -> None:
    dims = {k: 0 for k in APPEAL_DIMENSIONS}
    report = _make_appeal_report(dims, llm_used=False)
    md = report.to_markdown()
    assert "不可用" in md, "离线分支原样（已有人话，不重复）"
    assert "## 一句话总结" not in md


def test_appeal_to_dict_summary_lines() -> None:
    dims = {k: 80 for k in APPEAL_DIMENSIONS}
    report = _make_appeal_report(dims)
    report.summary_lines = ["a", "b"]
    d = report.to_dict()
    assert d["summary_lines"] == ["a", "b"]
    for key in ("dimensions", "total_score", "one_liner", "suggestions",
                "llm_used", "error", "source"):
        assert key in d, f"既有键 {key} 必须保留（只增不删）"
