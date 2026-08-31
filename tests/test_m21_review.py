"""M21 成书质量评审工作流单元测试（离线，mock LLMClient）

覆盖：
- full / lean / solo 模式下 LLM 调用次数正确（5 / 3 / 1）
- 报告文件生成（.state/review/review-*.md）
- scope 解析（all / latest / 1-10 / 1,3,5 / 混合）
- rubric 选择注入（general / fanqie / qidian / zhihu）
- --json 输出结构（CLI）
- 非法 mode / platform 报错
- workflow 注册
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from typer.testing import CliRunner

from agent.client import LLMClient, LLMResponse
from agent.core.engine.workflow_registry import get_workflow
from agent.workflows.m21_review import (
    GENERAL_RUBRIC,
    MODE_DIMENSIONS,
    M21ReviewWorkflow,
    ReviewIssue,
)

from tests.conftest import make_project


# ============================================================
# 假数据
# ============================================================
FIXED_LLM_JSON = {
    "verdict": "CONCERNS",
    "overall_verdict": "CONCERNS",
    "total_score": 72,
    "issues": [
        {
            "severity": "warn",
            "location": "ch001",
            "description": "节奏略平",
            "suggestion": "加章末钩子",
        },
        {
            "severity": "block",
            "location": "ch003",
            "description": "主线推进过慢",
            "suggestion": "压缩铺垫",
        },
    ],
    "summary": "整体可用",
    "verdict_text": "整体可用，但主线推进偏慢。",
    "recommendations": ["压缩前几章铺垫", "强化章末钩子"],
    "disagreements": [],
}


def make_mock_llm() -> MagicMock:
    """构造返回固定 JSON 的 mock LLM（每个 chat_utility 调用返回同一结果）"""
    llm = MagicMock(spec=LLMClient)
    llm.chat_utility.return_value = LLMResponse(
        text=json.dumps(FIXED_LLM_JSON, ensure_ascii=False)
    )
    return llm


def _build_workflow(tmp_path: Path, mode: str = "full") -> tuple[M21ReviewWorkflow, MagicMock, Path]:
    """搭建含 3 章的样例项目 + mock LLM + 工作流"""
    d = make_project(tmp_path, n_chapters=3)
    llm = make_mock_llm()
    wf = M21ReviewWorkflow(project_dir=d, llm_client=llm)
    return wf, llm, d


# ============================================================
# 工作流注册
# ============================================================
class TestRegistry:
    def test_workflow_registered(self) -> None:
        assert get_workflow("m21_review") is M21ReviewWorkflow

    def test_mode_dimensions_config(self) -> None:
        assert [k for k, _ in MODE_DIMENSIONS["full"]] == [
            "architect",
            "consistency",
            "reader",
            "foreshadow",
        ]
        assert [k for k, _ in MODE_DIMENSIONS["lean"]] == [
            "architect",
            "consistency",
        ]
        assert MODE_DIMENSIONS["solo"] == []


# ============================================================
# LLM 调用次数（full=5 / lean=3 / solo=1）
# ============================================================
class TestModes:
    def test_full_calls_llm_five_times(self, tmp_path: Path) -> None:
        wf, llm, _ = _build_workflow(tmp_path, mode="full")
        report = wf.review(scope="all", mode="full")
        assert llm.chat_utility.call_count == 5  # 4 视角 + 1 裁决
        assert len(report.dimensions) == 4

    def test_lean_calls_llm_three_times(self, tmp_path: Path) -> None:
        wf, llm, _ = _build_workflow(tmp_path, mode="lean")
        report = wf.review(scope="all", mode="lean")
        assert llm.chat_utility.call_count == 3  # 2 视角 + 1 裁决
        assert len(report.dimensions) == 2

    def test_solo_calls_llm_once(self, tmp_path: Path) -> None:
        wf, llm, _ = _build_workflow(tmp_path, mode="solo")
        report = wf.review(scope="all", mode="solo")
        assert llm.chat_utility.call_count == 1  # 单视角综合（verdict 提示词）
        # solo 把综合评审同时呈现为一个视角
        assert len(report.dimensions) == 1
        assert report.dimensions[0].key == "solo"

    def test_report_fields_parsed(self, tmp_path: Path) -> None:
        wf, _, _ = _build_workflow(tmp_path, mode="full")
        report = wf.review(scope="all", mode="full")
        assert report.overall_verdict == "CONCERNS"
        assert report.total_score == 72
        assert report.verdict_text == "整体可用，但主线推进偏慢。"
        assert len(report.recommendations) == 2

    def test_issues_merged_and_block_first(self, tmp_path: Path) -> None:
        """多视角重复问题去重，block 优先排序"""
        wf, _, _ = _build_workflow(tmp_path, mode="full")
        report = wf.review(scope="all", mode="full")
        # 4 视角 + 裁决各产生 [warn, block]；按 description+location 去重后只剩 2 条
        assert len(report.issues) == 2
        assert report.issues[0].severity == "block"
        assert report.issues[1].severity == "warn"


# ============================================================
# 报告文件生成
# ============================================================
class TestReportFile:
    def test_report_file_generated(self, tmp_path: Path) -> None:
        wf, _, d = _build_workflow(tmp_path, mode="full")
        report = wf.review(scope="all", mode="full")
        assert report.report_file is not None
        assert report.report_file.exists()
        files = list((d / ".state" / "review").glob("review-*.md"))
        assert len(files) == 1

    def test_report_markdown_content(self, tmp_path: Path) -> None:
        wf, _, d = _build_workflow(tmp_path, mode="full")
        report = wf.review(scope="all", mode="full")
        content = report.report_file.read_text(encoding="utf-8")
        assert "成书质量评审报告" in content
        assert "综合评定" in content
        assert "结构架构" in content  # 分视角节
        assert "问题清单" in content
        assert "主线推进过慢" in content

    def test_report_to_json_serializable(self, tmp_path: Path) -> None:
        wf, _, _ = _build_workflow(tmp_path, mode="full")
        report = wf.review(scope="all", mode="full")
        data = json.loads(report.to_json())
        assert data["overall_verdict"] == "CONCERNS"
        assert len(data["dimensions"]) == 4
        assert data["issues"][0]["severity"] == "block"

    def test_save_false_no_file(self, tmp_path: Path) -> None:
        wf, _, _ = _build_workflow(tmp_path, mode="full")
        report = wf.review(scope="all", mode="full", save=False)
        assert report.report_file is None


# ============================================================
# scope 解析
# ============================================================
class TestScope:
    def test_parse_scope_all(self, tmp_path: Path) -> None:
        wf, _, _ = _build_workflow(tmp_path)
        assert wf._parse_scope("all", 10) == list(range(1, 11))

    def test_parse_scope_latest(self, tmp_path: Path) -> None:
        wf, _, _ = _build_workflow(tmp_path)
        assert wf._parse_scope("latest", 10) == [10]
        assert wf._parse_scope("latest", 0) == []

    def test_parse_scope_range(self, tmp_path: Path) -> None:
        wf, _, _ = _build_workflow(tmp_path)
        assert wf._parse_scope("1-10", 50) == list(range(1, 11))

    def test_parse_scope_list(self, tmp_path: Path) -> None:
        wf, _, _ = _build_workflow(tmp_path)
        assert wf._parse_scope("1,3,5", 50) == [1, 3, 5]

    def test_parse_scope_mixed(self, tmp_path: Path) -> None:
        wf, _, _ = _build_workflow(tmp_path)
        assert wf._parse_scope("3-5,8", 50) == [3, 4, 5, 8]

    def test_parse_scope_out_of_range_filtered(self, tmp_path: Path) -> None:
        wf, _, _ = _build_workflow(tmp_path)
        # 超过 max_chapter 的章节被过滤；升序去重
        assert wf._parse_scope("2-20", 10) == list(range(2, 11))

    def test_read_scope_latest_only_last_chapter(self, tmp_path: Path) -> None:
        wf, _, _ = _build_workflow(tmp_path)
        text = wf._read_scope("latest")
        assert "第3章" in text
        assert "第1章" not in text

    def test_read_scope_all_concatenates(self, tmp_path: Path) -> None:
        wf, _, _ = _build_workflow(tmp_path)
        text = wf._read_scope("all")
        assert "第1章" in text and "第2章" in text and "第3章" in text

    def test_read_scope_range(self, tmp_path: Path) -> None:
        wf, _, _ = _build_workflow(tmp_path)
        text = wf._read_scope("1,3")
        assert "第1章" in text and "第3章" in text
        assert "第2章" not in text


# ============================================================
# rubric 选择注入
# ============================================================
class TestRubric:
    def _user_content(self, llm: MagicMock, call_index: int = 0) -> str:
        call = llm.chat_utility.call_args_list[call_index]
        messages = call.kwargs["messages"]
        return messages[1]["content"]

    def test_general_rubric_injected(self, tmp_path: Path) -> None:
        wf, llm, _ = _build_workflow(tmp_path, mode="full")
        wf.review(scope="all", mode="full", platform="general")
        assert "通用质量评分标准" in self._user_content(llm, 0)

    def test_fanqie_rubric_injected(self, tmp_path: Path) -> None:
        wf, llm, _ = _build_workflow(tmp_path, mode="full")
        wf.review(scope="all", mode="full", platform="fanqie")
        assert "番茄小说 Quality Rubric" in self._user_content(llm, 0)

    def test_qidian_rubric_injected(self, tmp_path: Path) -> None:
        wf, llm, _ = _build_workflow(tmp_path, mode="full")
        wf.review(scope="all", mode="full", platform="qidian")
        assert "起点中文网 Quality Rubric" in self._user_content(llm, 0)

    def test_zhihu_rubric_injected(self, tmp_path: Path) -> None:
        wf, llm, _ = _build_workflow(tmp_path, mode="full")
        wf.review(scope="all", mode="full", platform="zhihu")
        assert "知乎盐言故事 Quality Rubric" in self._user_content(llm, 0)

    def test_rubric_injected_into_verdict_too(self, tmp_path: Path) -> None:
        """verdict 调用（最后一发）也应携带 rubric"""
        wf, llm, _ = _build_workflow(tmp_path, mode="full")
        wf.review(scope="all", mode="full", platform="fanqie")
        # full 共 5 次调用，最后一次是裁决
        assert "番茄小说 Quality Rubric" in self._user_content(llm, 4)

    def test_dimension_system_prompt_loaded(self, tmp_path: Path) -> None:
        """维度评审的 system prompt 应来自 m21.* 提示词"""
        wf, llm, _ = _build_workflow(tmp_path, mode="full")
        wf.review(scope="all", mode="full")
        call = llm.chat_utility.call_args_list[0]
        system_msg = call.kwargs["messages"][0]["content"]
        assert "故事架构师" in system_msg  # architect.md system

    def test_invalid_platform_raises(self, tmp_path: Path) -> None:
        wf, _, _ = _build_workflow(tmp_path)
        with pytest.raises(ValueError, match="非法 platform"):
            wf.review(scope="all", mode="full", platform="tomato")

    def test_invalid_mode_raises(self, tmp_path: Path) -> None:
        wf, _, _ = _build_workflow(tmp_path)
        with pytest.raises(ValueError, match="非法 mode"):
            wf.review(scope="all", mode="ultra")


# ============================================================
# 降级（LLM 返回非 JSON）
# ============================================================
class TestDegradation:
    def test_non_json_response_degrades(self, tmp_path: Path) -> None:
        d = make_project(tmp_path, n_chapters=2)
        llm = MagicMock(spec=LLMClient)
        llm.chat_utility.return_value = LLMResponse(text="抱歉，我无法生成。")
        wf = M21ReviewWorkflow(project_dir=d, llm_client=llm)
        report = wf.review(scope="all", mode="solo")
        # 降级不阻断：仍返回报告，总评 CONCERNS
        assert report.overall_verdict == "CONCERNS"
        assert report.total_score == 0


# ============================================================
# CLI --json 输出结构
# ============================================================
class TestCLI:
    def _patch_llm(self, monkeypatch: pytest.MonkeyPatch, mock: MagicMock) -> None:
        zero_arg = lambda *a, **kw: mock  # noqa: E731
        monkeypatch.setattr("agent.client.LLMClient", zero_arg)
        monkeypatch.setattr("agent.workflows.m21_review.LLMClient", zero_arg)

    def test_review_book_json_structure(self, tmp_path: Path, monkeypatch) -> None:
        from agent.cli import app

        d = make_project(tmp_path, n_chapters=3)
        self._patch_llm(monkeypatch, make_mock_llm())
        runner = CliRunner()
        result = runner.invoke(app, ["review-book", "--json", "-d", str(d)])
        assert result.exit_code == 0, result.output
        data = json.loads(result.stdout)
        assert data["success"] is True
        for key in (
            "mode",
            "platform",
            "scope",
            "overall_verdict",
            "total_score",
            "dimensions",
            "issues",
            "verdict_text",
            "report_file",
        ):
            assert key in data, f"review-book --json 缺少字段 {key}"
        assert data["mode"] == "full"
        assert data["total_score"] == 72
        assert data["report_file"]

    def test_review_book_json_lean_mode(self, tmp_path: Path, monkeypatch) -> None:
        from agent.cli import app

        d = make_project(tmp_path, n_chapters=3)
        self._patch_llm(monkeypatch, make_mock_llm())
        runner = CliRunner()
        result = runner.invoke(
            app, ["review-book", "--json", "-d", str(d), "--mode", "lean"]
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.stdout)
        assert data["mode"] == "lean"
        assert len(data["dimensions"]) == 2

    def test_review_book_json_no_chapters_error(self, tmp_path: Path, monkeypatch) -> None:
        from agent.cli import app

        d = tmp_path / "empty"
        d.mkdir()
        self._patch_llm(monkeypatch, make_mock_llm())
        runner = CliRunner()
        result = runner.invoke(app, ["review-book", "--json", "-d", str(d)])
        assert result.exit_code == 1, result.output
        data = json.loads(result.stdout)
        assert data["success"] is False
        assert data["error"]["code"] == "no_chapters"

    def test_review_book_rich_output(self, tmp_path: Path, monkeypatch) -> None:
        """非 --json 模式应输出富文本，不崩"""
        from agent.cli import app

        d = make_project(tmp_path, n_chapters=3)
        self._patch_llm(monkeypatch, make_mock_llm())
        runner = CliRunner()
        result = runner.invoke(app, ["review-book", "-d", str(d)])
        assert result.exit_code == 0, result.output
        assert "成书质量评审" in result.output


# ============================================================
# ReviewIssue 规范化
# ============================================================
class TestIssueNormalize:
    def test_invalid_severity_normalized(self, tmp_path: Path) -> None:
        wf, _, _ = _build_workflow(tmp_path)
        issues = wf._parse_issues(
            [
                {"severity": "critical", "description": "x"},
                {"severity": "BLOCK", "description": "y"},
            ]
        )
        assert all(i.severity in ("block", "warn") for i in issues)
        assert any(i.severity == "block" for i in issues)

    def test_issue_to_dict(self) -> None:
        issue = ReviewIssue(severity="block", location="ch001", description="d", suggestion="s")
        d = issue.to_dict()
        assert d["severity"] == "block"
        assert d["location"] == "ch001"
