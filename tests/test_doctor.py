"""健康体检 doctor 单元测试（增量 F / T03）

覆盖：
- 健康项目：structure / state / db 全 ok，is_healthy=True
- 缺失产物（world.md）：structure 报错并给出修复命令
- 坏 state.json：state 模块报错，修复命令指向 reset-state
- 长篇章节缺失 RAG 索引：rag 模块 warn，修复命令指向 reindex
- CLI：doctor --json 成功路径与不健康路径的 JSON 信封
- 只读性：doctor 不修改任何项目文件
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from agent.cli import app
from agent.core.infra.doctor import Doctor
from agent.core.engine.state_machine import State
from typer.testing import CliRunner

from tests.conftest import make_project


def _set_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """注入 LLM_API_KEY，使 doctor 的 deps 模块判定为 ok（确定性）"""
    monkeypatch.setenv("LLM_API_KEY", "test-key-doctor")


# ============================================================
# 健康项目
# ============================================================
class TestDoctorHealthy:
    def test_healthy_project_all_ok(self, tmp_path: Path, monkeypatch) -> None:
        """完备项目（WRITING + 3 章）应无 error/warn（rag 信息为 info）"""
        _set_api_key(monkeypatch)
        d = make_project(tmp_path, n_chapters=3, state=State.WRITING)
        checks = Doctor(d).check()

        errors = [c for c in checks if c.status == "error"]
        warns = [c for c in checks if c.status == "warn"]
        assert errors == [], f"不应有 error：{errors}"
        assert warns == [], f"不应有 warn：{warns}"

        # 关键模块均为 ok
        modules = {c.module for c in checks}
        for mod in ("structure", "state", "db", "deps"):
            assert mod in modules
        structure_ok = [c for c in checks if c.module == "structure" and c.status == "ok"]
        assert structure_ok, "structure 模块应有 ok 项"
        assert Doctor.is_healthy(checks)

    def test_healthy_project_json_shape(self, tmp_path: Path, monkeypatch) -> None:
        """Doctor 结果可经 doctor_to_dict 序列化，字段齐全"""
        _set_api_key(monkeypatch)
        d = make_project(tmp_path, n_chapters=3, state=State.WRITING)
        from agent.core.infra.doctor import doctor_to_dict

        data = doctor_to_dict(Doctor(d).check())
        assert isinstance(data, list) and data
        for item in data:
            assert set(item.keys()) == {"module", "status", "detail", "fix_command"}


# ============================================================
# 缺失产物
# ============================================================
class TestDoctorMissingProducts:
    def test_missing_world_md_reports_error_with_fix(self, tmp_path: Path, monkeypatch) -> None:
        """删除 world.md 后，structure 应报 error 并给出 start 修复命令"""
        _set_api_key(monkeypatch)
        d = make_project(tmp_path, n_chapters=3, state=State.WRITING)
        (d / "world.md").unlink()

        checks = Doctor(d).check()
        world_check = [
            c for c in checks if c.module == "structure" and "world.md" in c.detail
        ]
        assert world_check, "应存在 world.md 缺失的 structure 检查项"
        item = world_check[0]
        assert item.status == "error"
        assert item.fix_command.startswith("novel-agent start -d ")
        assert str(d) in item.fix_command

    def test_missing_chapters_dir_reports_error(self, tmp_path: Path, monkeypatch) -> None:
        """WRITING 阶段 chapters/ 为空：structure 报 error 并指向 write"""
        _set_api_key(monkeypatch)
        d = make_project(tmp_path, n_chapters=3, state=State.WRITING)
        import shutil

        shutil.rmtree(d / "chapters")

        checks = Doctor(d).check()
        ch_check = [
            c for c in checks if c.module == "structure" and c.status == "error"
            and "chapters" in c.detail
        ]
        assert ch_check, "chapters 为空应报 error"
        assert ch_check[0].fix_command.startswith("novel-agent write -d ")


# ============================================================
# 坏状态机
# ============================================================
class TestDoctorBadState:
    def test_bad_state_json_reports_error(self, tmp_path: Path, monkeypatch) -> None:
        """state.json 非法 JSON：state 模块 error，修复命令指向 reset-state"""
        _set_api_key(monkeypatch)
        d = make_project(tmp_path, n_chapters=3, state=State.WRITING)
        (d / ".state" / "state.json").write_text("{ this is not json", encoding="utf-8")

        checks = Doctor(d).check()
        state_check = [c for c in checks if c.module == "state" and c.status == "error"]
        assert state_check, "坏 state.json 应报 error"
        assert "reset-state" in state_check[0].fix_command
        assert not Doctor.is_healthy(checks)


# ============================================================
# RAG 索引
# ============================================================
class TestDoctorRag:
    def test_missing_rag_index_long_project_warns(self, tmp_path: Path, monkeypatch) -> None:
        """长篇章节（>=10）缺 RAG 索引：rag 模块 warn，修复命令指向 reindex"""
        _set_api_key(monkeypatch)
        d = make_project(tmp_path, n_chapters=12, state=State.WRITING)

        checks = Doctor(d).check()
        rag_check = [c for c in checks if c.module == "rag"]
        assert rag_check, "应包含 rag 模块检查项"
        item = rag_check[0]
        assert item.status == "warn"
        assert "reindex" in item.fix_command

    def test_rag_index_present_ok(self, tmp_path: Path, monkeypatch) -> None:
        """存在 .state/rag/index.json：rag 模块 ok（不触发 --ping 也不联网）"""
        _set_api_key(monkeypatch)
        d = make_project(tmp_path, n_chapters=12, state=State.WRITING)
        rag_dir = d / ".state" / "rag"
        rag_dir.mkdir(parents=True, exist_ok=True)
        (rag_dir / "index.json").write_text("{}", encoding="utf-8")

        checks = Doctor(d).check()
        rag_check = [c for c in checks if c.module == "rag"]
        assert rag_check and rag_check[0].status == "ok"


# ============================================================
# CLI --json
# ============================================================
class TestDoctorCli:
    def test_doctor_json_healthy(self, tmp_path: Path, monkeypatch) -> None:
        """doctor --json 健康项目：success=true / healthy=true / checks[] 非空"""
        _set_api_key(monkeypatch)
        d = make_project(tmp_path, n_chapters=3, state=State.WRITING)

        runner = CliRunner()
        result = runner.invoke(app, ["doctor", "--json", "-d", str(d)])
        assert result.exit_code == 0, result.output

        data = json.loads(result.stdout)
        assert data["success"] is True
        assert data["healthy"] is True
        assert isinstance(data["checks"], list) and data["checks"]

    def test_doctor_json_unhealthy_bad_state(self, tmp_path: Path, monkeypatch) -> None:
        """doctor --json 坏 state.json：success=true（体检已执行）但 healthy=false"""
        _set_api_key(monkeypatch)
        d = make_project(tmp_path, n_chapters=3, state=State.WRITING)
        (d / ".state" / "state.json").write_text("{ broken", encoding="utf-8")

        runner = CliRunner()
        result = runner.invoke(app, ["doctor", "--json", "-d", str(d)])
        assert result.exit_code == 0, result.output

        data = json.loads(result.stdout)
        assert data["success"] is True
        assert data["healthy"] is False
        assert any(
            c["module"] == "state" and c["status"] == "error" for c in data["checks"]
        )


# ============================================================
# 只读性
# ============================================================
class TestDoctorReadOnly:
    def test_doctor_does_not_modify_files(self, tmp_path: Path, monkeypatch) -> None:
        """doctor 仅读取，不应改动任何项目文件内容（含 state.json）"""
        _set_api_key(monkeypatch)
        d = make_project(tmp_path, n_chapters=3, state=State.WRITING)
        state_file = d / ".state" / "state.json"
        before = state_file.read_text(encoding="utf-8")

        Doctor(d).check()

        after = state_file.read_text(encoding="utf-8")
        assert after == before, "doctor 不应修改 state.json"
        assert (d / "world.md").exists()
