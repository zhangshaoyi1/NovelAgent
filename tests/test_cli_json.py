"""CLI --json 输出验证（T12）

验证 5 个命令的 --json 输出合法且字段齐全：
- write --json 成功路径：mock LLM + 模拟 safe-delete 垫片（os.remove 抛错），
  断言 8 字段齐全、clear_draft 阶段 safe_remove 在 shim 下不崩、退出码 0。
- write --json 错误路径：no_world（退出码 1）/ 门禁拒绝（退出码 2）→ JSON 错误信封。
- status --json：已初始化输出 {state,mode,progress,available_commands}；
  未初始化输出 {success:false,error:{code:not_initialized}}（退出码 0）。
- export --json：先 write 出一章，再 export --json，断言 {success,chapters,total_chars,output_file}。
- adjust-relation / adjust-route --json：mock LLM 下断言含 conflicts 字段。

所有测试遵循现有风格：复用 conftest 的 _build_minimal_project / _build_mock_llm，
CLI 用 typer.testing.CliRunner 驱动，LLMClient 通过 monkeypatch 替换为 mock。
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import agent.utils
import pytest
from agent.cli import app
from agent.client import LLMClient, LLMResponse
from agent.core.engine.state_machine import State
from typer.testing import CliRunner

from tests.conftest import _build_minimal_project, _build_mock_llm


def _raise_os_error(*args: object, **kwargs: object) -> None:
    """模拟 WorkBuddy safe-delete 垫片 FAIL_CLOSED：删除操作抛 OSError"""
    raise OSError("simulated safe-delete shim FAIL_CLOSED")


def _patch_llm(monkeypatch: pytest.MonkeyPatch, mock: MagicMock) -> None:
    """将各工作流模块与冲突仲裁器里的 LLMClient 替换为返回 mock 的无参可调用对象，
    确保 CLI 路径完全不触碰真实 LLM / 网络。"""
    zero_arg = lambda *a, **kw: mock  # noqa: E731
    monkeypatch.setattr("agent.workflows.m5_write_chapter.LLMClient", zero_arg)
    monkeypatch.setattr("agent.client.LLMClient", zero_arg)
    monkeypatch.setattr("agent.workflows.m6_adjust.LLMClient", zero_arg)
    monkeypatch.setattr("agent.workflows.m11_export.LLMClient", zero_arg)


# ============================================================
# write --json 成功路径（关键：safe_remove 在 shim 下不崩）
# ============================================================
class TestWriteJson:
    def test_write_json_success_with_safe_remove_shim(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """write --json 在 mock LLM + safe-delete 垫片（os.remove 抛错）下：
        - 输出合法 JSON 且含全部 8 字段；
        - 退出码 0。
        这直接证明「去掉 env -u CODEBUDDY_SESSION_ID 后 write 不再崩溃」。
        AgenticWriteWorkflow（默认 auto 模式）不涉及 draft.wip 清理，因此不检查 .bak。"""
        d = _build_minimal_project(tmp_path)  # CHARACTER_DESIGN → /write 允许
        mock = _build_mock_llm()
        _patch_llm(monkeypatch, mock)

        # 模拟 WorkBuddy safe-delete 垫片拦截 os.remove（FAIL_CLOSED）
        monkeypatch.setattr(agent.utils.os, "remove", _raise_os_error)

        runner = CliRunner()
        # 走 AgenticWriteWorkflow（默认 auto 模式）
        result = runner.invoke(app, ["write", "--json", "-d", str(d)])

        assert result.exit_code == 0, result.output
        data = json.loads(result.stdout)
        assert data["success"] is True
        # 8 字段齐全
        for key in (
            "chapter",
            "title",
            "word_count",
            "quality_passed",
            "revision_attempts",
            "subline",
            "route_node",
        ):
            assert key in data, f"缺少字段 {key}"
        assert data["chapter"] == 1
        assert data["quality_passed"] is True

    def test_write_json_no_world_exits_1_with_error_envelope(
        self, tmp_path: Path
    ) -> None:
        """未初始化/缺 world.md 的项目：write --json 输出错误信封，退出码 1。"""
        d = tmp_path / "empty"
        d.mkdir()
        runner = CliRunner()
        result = runner.invoke(app, ["write", "--json", "-d", str(d)])
        assert result.exit_code == 1, result.output
        data = json.loads(result.stdout)
        assert data["success"] is False
        assert data["error"]["code"] == "no_world"
        assert "message" in data["error"]

    def test_write_json_gate_rejected_exits_2_with_error_envelope(
        self, tmp_path: Path
    ) -> None:
        """门禁阶段（INIT 不允许 /write）：write --json 输出 gate_rejected 错误信封，退出码 2。"""
        d = _build_minimal_project(tmp_path, state=State.INIT)
        runner = CliRunner()
        result = runner.invoke(app, ["write", "--json", "-d", str(d)])
        assert result.exit_code == 2, result.output
        data = json.loads(result.stdout)
        assert data["success"] is False
        assert data["error"]["code"] == "gate_rejected"
        assert "message" in data["error"]


# ============================================================
# status --json（无需 LLM，可靠）
# ============================================================
class TestStatusJson:
    def test_status_json_initialized(self, tmp_path: Path) -> None:
        d = _build_minimal_project(tmp_path, state=State.WRITING)
        runner = CliRunner()
        result = runner.invoke(app, ["status", "--json", "-d", str(d)])
        assert result.exit_code == 0, result.output
        data = json.loads(result.stdout)
        for key in ("state", "mode", "progress", "available_commands"):
            assert key in data, f"status --json 缺少字段 {key}"
        assert data["state"] == "WRITING"
        assert isinstance(data["available_commands"], list)

    def test_status_json_not_initialized(self, tmp_path: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(app, ["status", "--json", "-d", str(tmp_path)])
        # 未初始化：输出错误信封，退出码保持 0（status 设计如此）
        assert result.exit_code == 0, result.output
        data = json.loads(result.stdout)
        assert data["success"] is False
        assert data["error"]["code"] == "not_initialized"
        assert "message" in data["error"]


# ============================================================
# export --json（先 write 一章，再 export）
# ============================================================
class TestExportJson:
    def test_export_json_after_write(self, tmp_path: Path, monkeypatch) -> None:
        d = _build_minimal_project(tmp_path)  # CHARACTER_DESIGN
        mock = _build_mock_llm()
        _patch_llm(monkeypatch, mock)

        runner = CliRunner()
        # 先写出第 1 章（状态转入 WRITING，满足 export 门禁）
        w = runner.invoke(app, ["write", "-d", str(d)])
        assert w.exit_code == 0, w.output

        # 再 export --json（markdown 默认）
        result = runner.invoke(app, ["export", "--json", "-d", str(d)])
        assert result.exit_code == 0, result.output
        data = json.loads(result.stdout)
        for key in ("success", "chapters", "total_chars", "output_file"):
            assert key in data, f"export --json 缺少字段 {key}"
        assert data["chapters"] >= 1
        assert data["total_chars"] > 0
        assert data["output_file"]


# ============================================================
# adjust-relation / adjust-route --json（mock LLM，验证含 conflicts）
# ============================================================
def _build_m6_mock() -> MagicMock:
    """仿 test_m6_adjust 的 mock：根据系统 prompt 决定返回路线/关系/影响 JSON。

    仅用于验证 CLI --json 输出结构（含 conflicts 字段），不关心具体数据。
    """
    import json as _json

    route_json = {
        "root_node": "寒门弃徒",
        "nodes": [
            {
                "id": "N01",
                "chapter_range": "1-15",
                "milestone": "太虚镜初启",
                "main_branch": {"title": "拒献同门", "result": "修补版锻体术", "growth": "炼气一层"},
                "alt_branches": [],
            }
        ],
    }
    graph_json = {
        "nodes": [
            {"id": "linxun", "label": "林寻", "group": "protagonist"},
            {"id": "taixu", "label": "太虚镜", "group": "supporting"},
        ],
        "edges": [
            {
                "from": "linxun",
                "to": "taixu",
                "type": "共生/殉道",
                "intensity": 10,
                "since": "S01",
                "note": "工具→挚友",
                "archived": False,
            },
            {
                "from": "linxun",
                "to": "taixu",
                "type": "技术同盟",
                "intensity": 5,
                "since": "ch002",
                "note": "新边",
                "archived": False,
            },
        ],
    }
    impact_json = {
        "field_conflicts": [],
        "affected_characters": [],
        "affected_chapters": [],
        "golden_finger_risk": "",
        "timeline_conflicts": [],
        "recommendations": [{"option": "保留原设定改章节", "detail": "后续体现"}],
    }

    def creative_side(*args: object, **kwargs: object) -> LLMResponse:
        msgs = kwargs.get("messages") or (args[0] if args else [])
        sys_msg = ""
        for m in msgs:
            if isinstance(m, dict) and m.get("role") == "system":
                sys_msg = str(m.get("content", ""))
                break
        text = (
            _json.dumps(route_json, ensure_ascii=False)
            if ("主角成长路线" in sys_msg or "protagonist_route" in sys_msg)
            else _json.dumps(graph_json, ensure_ascii=False)
        )
        return LLMResponse(text=text, raw={}, usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2})

    def utility_side(*args: object, **kwargs: object) -> LLMResponse:
        return LLMResponse(
            text=_json.dumps(impact_json, ensure_ascii=False),
            raw={},
            usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        )

    llm = MagicMock(spec=LLMClient)
    llm.chat_creative.side_effect = creative_side
    llm.chat_utility.side_effect = utility_side
    return llm


class TestAdjustJson:
    def test_adjust_relation_json(self, tmp_path: Path, monkeypatch) -> None:
        d = _build_minimal_project(tmp_path, state=State.WRITING)
        mock = _build_m6_mock()
        _patch_llm(monkeypatch, mock)
        runner = CliRunner()
        result = runner.invoke(
            app,
            ["adjust-relation", "--json", "-d", str(d), "--intent", "赵无极对林寻转为暗中赏识"],
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.stdout)
        assert data["success"] is True
        for key in ("nodes_count", "new_edges_count", "archived_edges_count", "conflicts"):
            assert key in data, f"adjust-relation --json 缺少字段 {key}"
        assert "high" in data["conflicts"] and "medium" in data["conflicts"] and "low" in data["conflicts"]

    def test_adjust_route_json(self, tmp_path: Path, monkeypatch) -> None:
        d = _build_minimal_project(tmp_path, state=State.WRITING)
        mock = _build_m6_mock()
        _patch_llm(monkeypatch, mock)
        runner = CliRunner()
        result = runner.invoke(
            app,
            ["adjust-route", "--json", "-d", str(d), "--intent", "让主角在N02加入执法堂"],
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.stdout)
        assert data["success"] is True
        for key in ("current_node_id", "old_route_archived", "new_nodes_count", "conflicts"):
            assert key in data, f"adjust-route --json 缺少字段 {key}"
        assert "high" in data["conflicts"] and "medium" in data["conflicts"] and "low" in data["conflicts"]
