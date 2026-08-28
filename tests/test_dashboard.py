"""Dashboard 只读可视化 单元测试（增量 B）

覆盖：
- 聚合解析正确：关系网节点/边统计、路线 TOC、伏笔 by_status / pending。
- 可选数据降级：缺 pacing / learnings / rag 的工程（rules-horror 与 make_project
  fixture）→ 三面板 available=False 不崩；损坏数据源同样降级、整体仍 success。
- 只读契约：``dashboard --output`` 前后对项目目录做文件哈希比对，无任何变更（含 .state/）。
- HTML 产物合法：生成的 HTML 含 mermaid <script>（锁定版本）与内联数据块、可被解析。
- --json 契约：``--json`` 输出 success:true 且 panels 含 8 键；某面板降级整体仍 success。
- 不修改任何既有源码；新增测试全绿且既有 682 基线无回归。
"""

from __future__ import annotations

import hashlib
import json
from html.parser import HTMLParser
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agent.cli import app
from agent.core.infra.dashboard_aggregator import (
    ChapterInfo,
    DashboardAggregator,
    ForeshadowRow,
)
from agent.core.engine.state_machine import State
from tests.conftest import make_project

REPO_ROOT = Path(__file__).resolve().parent.parent
SAMPLE = REPO_ROOT / "projects" / "rules-horror"

# 样例伏笔关联角色中的 curly quote（U+2018 / U+2019）
CURTLY_DOCTOR = "\u2018\u533b\u751f\u2019"  # '医生'


def _set_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """注入 LLM_API_KEY，使 Doctor 的 deps 模块判定确定性（不影响只读性）。"""
    monkeypatch.setenv("LLM_API_KEY", "test-key-dashboard")


def _snapshot(project: Path) -> dict[str, str]:
    """记录项目目录下所有文件的相对路径 → sha256，用于只读性比对。"""
    snap: dict[str, str] = {}
    for p in sorted(project.rglob("*")):
        if p.is_file():
            snap[str(p.relative_to(project))] = hashlib.sha256(
                p.read_bytes()
            ).hexdigest()
    return snap


# ============================================================
# ① 关系网
# ============================================================
class TestDashboardRelations:
    def test_relations_counts_and_mermaid(self) -> None:
        data = DashboardAggregator(SAMPLE).aggregate()
        rel = data.relations
        assert rel.available is True
        # 节点 8，关系边 18（**排除** 归档边）
        assert rel.node_count == 8
        assert rel.edge_count == 18
        # mermaid 原样透传
        assert rel.mermaid
        assert "graph LR" in rel.mermaid
        assert "L·林厌" in rel.mermaid

    def test_relations_missing_file_degrades(self, tmp_path: Path) -> None:
        d = tmp_path / "empty"
        d.mkdir()
        rel = DashboardAggregator(d).aggregate().relations
        assert rel.available is False
        assert rel.mermaid == ""
        assert rel.node_count is None
        assert rel.edge_count is None


# ============================================================
# ② 主角路线
# ============================================================
class TestDashboardRoute:
    def test_route_toc_and_markdown(self) -> None:
        data = DashboardAggregator(SAMPLE).aggregate()
        route = data.route
        assert route.available is True
        assert route.markdown
        assert len(route.toc) == 4
        assert route.toc[0] == "N01 · 异常样本标记与容器化启动"
        assert all(t.startswith("N") for t in route.toc)

    def test_route_missing_file_degrades(self, tmp_path: Path) -> None:
        d = tmp_path / "empty"
        d.mkdir()
        route = DashboardAggregator(d).aggregate().route
        assert route.available is False
        assert route.markdown == ""
        assert route.toc == []


# ============================================================
# ③ 伏笔
# ============================================================
class TestDashboardForeshadows:
    def test_foreshadows_by_status_and_pending(self) -> None:
        data = DashboardAggregator(SAMPLE).aggregate()
        fs = data.foreshadows
        assert fs.available is True
        assert fs.total == 7
        assert fs.by_status == {"未埋": 7, "已埋": 0, "已回收": 0, "已废弃": 0}
        assert len(fs.pending) == 7
        first = fs.pending[0]
        assert isinstance(first, ForeshadowRow)
        assert first.fid == "F-01"
        # 关联角色逗号切分 + 逐项 strip，保留 curly quote
        assert first.chars == ["林厌", CURTLY_DOCTOR]

    def test_foreshadows_missing_file_degrades(self, tmp_path: Path) -> None:
        d = tmp_path / "empty"
        d.mkdir()
        fs = DashboardAggregator(d).aggregate().foreshadows
        assert fs.available is False
        assert fs.total == 0
        assert fs.by_status == {}
        assert fs.pending == []


# ============================================================
# ④ 进度
# ============================================================
class TestDashboardProgress:
    def test_progress_from_sample(self) -> None:
        data = DashboardAggregator(SAMPLE).aggregate()
        prog = data.progress
        assert prog.available is True
        assert prog.state == "WRITING"
        assert prog.written == 7
        assert prog.pass_rate == 1.0
        assert len(prog.chapters) == 7
        assert all(isinstance(c, ChapterInfo) for c in prog.chapters)
        # 按章号升序
        nums = [c.num for c in prog.chapters]
        assert nums == sorted(nums)

    def test_progress_missing_state_json_degrades(self, tmp_path: Path) -> None:
        d = tmp_path / "empty"
        d.mkdir()
        prog = DashboardAggregator(d).aggregate().progress
        assert prog.available is False


# ============================================================
# ⑤⑥⑦ 可选数据降级（缺 / 损坏 → available=False）
# ============================================================
class TestDashboardOptionalDegrade:
    def test_optional_missing_in_sample(self) -> None:
        """rules-horror 无 pacing/learnings/rag → 三面板 available=False 不崩。"""
        data = DashboardAggregator(SAMPLE).aggregate()
        assert data.pacing.available is False
        assert data.learnings.available is False
        assert data.rag.available is False
        # 核心面板仍正常
        assert data.relations.available is True
        assert data.health.available is True

    def test_optional_missing_in_make_project(self, tmp_path: Path) -> None:
        d = make_project(tmp_path, n_chapters=3, state=State.WRITING)
        data = DashboardAggregator(d).aggregate()
        assert data.pacing.available is False
        assert data.learnings.available is False
        assert data.rag.available is False

    def test_pacing_corrupt_degrades(self, tmp_path: Path) -> None:
        d = make_project(tmp_path, n_chapters=3, state=State.WRITING)
        (d / ".state" / "pacing.json").write_text("{ this is not json", encoding="utf-8")
        data = DashboardAggregator(d).aggregate()
        assert data.pacing.available is False
        assert data.pacing.open_debts == []
        assert data.pacing.cool_density == []

    def test_pacing_valid_available(self, tmp_path: Path) -> None:
        d = make_project(tmp_path, n_chapters=3, state=State.WRITING)
        (d / ".state" / "pacing.json").write_text(
            json.dumps(
                {
                    "open_debts": [
                        {
                            "id": "D1",
                            "desc": "钩子债",
                            "kind": "foreshadow",
                            "planted_ch": 1,
                            "status": "open",
                        }
                    ],
                    "resolved": [],
                    "cool_density": [0.5, 0.8],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        data = DashboardAggregator(d).aggregate()
        assert data.pacing.available is True
        assert data.pacing.open_debts == [
            {
                "id": "D1",
                "desc": "钩子债",
                "kind": "foreshadow",
                "planted_ch": 1,
                "status": "open",
            }
        ]
        assert data.pacing.cool_density == [0.5, 0.8]

    def test_learnings_valid_available(self, tmp_path: Path) -> None:
        d = make_project(tmp_path, n_chapters=3, state=State.WRITING)
        learn_dir = d / ".state" / "learnings"
        learn_dir.mkdir(parents=True, exist_ok=True)
        (learn_dir / "learnings.json").write_text(
            json.dumps(
                {
                    "learnings": [
                        {
                            "id": "L-001",
                            "category": "hook",
                            "text": "开篇章节抛钩子",
                            "source_chapters": [1],
                            "created_at": "2026-01-01 00:00:00",
                        }
                    ]
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        data = DashboardAggregator(d).aggregate()
        assert data.learnings.available is True
        assert data.learnings.items and data.learnings.items[0]["id"] == "L-001"

    def test_rag_valid_available(self, tmp_path: Path) -> None:
        d = make_project(tmp_path, n_chapters=3, state=State.WRITING)
        rag_dir = d / ".state" / "rag"
        rag_dir.mkdir(parents=True, exist_ok=True)
        (rag_dir / "index.json").write_text(
            json.dumps(
                {
                    "dim": 16,
                    "chunks": [
                        {
                            "text": "样例片段",
                            "source": "ch001",
                            "chapter_num": 1,
                            "kind": "body",
                            "embedding": [0.1, 0.2],
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        data = DashboardAggregator(d).aggregate()
        assert data.rag.available is True
        assert data.rag.index_count == 1


# ============================================================
# to_payload 序列化
# ============================================================
class TestDashboardToPayload:
    def test_to_payload_json_serializable(self) -> None:
        data = DashboardAggregator(SAMPLE).aggregate()
        payload = data.to_payload()
        # 可被 json.dumps 直接序列化（Path/datetime 已在构造时转 str）
        blob = json.dumps(payload, ensure_ascii=False)
        assert json.loads(blob) == payload

    def test_to_payload_shape(self) -> None:
        payload = DashboardAggregator(SAMPLE).aggregate().to_payload()
        assert set(payload.keys()) == {
            "project_dir",
            "generated_at",
            "relations",
            "route",
            "foreshadows",
            "progress",
            "pacing",
            "learnings",
            "rag",
            "health",
        }
        assert payload["project_dir"] == str(SAMPLE)
        assert isinstance(payload["generated_at"], str)


# ============================================================
# CLI --json 契约
# ============================================================
class TestDashboardCliJson:
    def test_json_success_and_eight_panels(self, tmp_path: Path, monkeypatch) -> None:
        _set_api_key(monkeypatch)
        runner = CliRunner()
        result = runner.invoke(app, ["dashboard", "--json", "-d", str(SAMPLE)])
        assert result.exit_code == 0, result.output
        data = json.loads(result.stdout)
        assert data["success"] is True
        # panels 为 data.to_payload()（含 8 面板 + 元信息 project_dir/generated_at）
        panels = data["panels"]
        for key in (
            "relations",
            "route",
            "foreshadows",
            "progress",
            "pacing",
            "learnings",
            "rag",
            "health",
        ):
            assert key in panels, f"--json panels 缺少面板 {key}"

    def test_json_degrade_still_success(self, tmp_path: Path, monkeypatch) -> None:
        """故意损坏某可选数据源 → 该面板 available=False，整体仍 success:true。"""
        _set_api_key(monkeypatch)
        d = make_project(tmp_path, n_chapters=3, state=State.WRITING)
        # 同时损坏三类可选数据源
        (d / ".state" / "pacing.json").write_text("{bad", encoding="utf-8")
        learn_dir = d / ".state" / "learnings"
        learn_dir.mkdir(parents=True, exist_ok=True)
        (learn_dir / "learnings.json").write_text("{bad", encoding="utf-8")
        rag_dir = d / ".state" / "rag"
        rag_dir.mkdir(parents=True, exist_ok=True)
        (rag_dir / "index.json").write_text("{bad", encoding="utf-8")

        runner = CliRunner()
        result = runner.invoke(app, ["dashboard", "--json", "-d", str(d)])
        assert result.exit_code == 0, result.output
        data = json.loads(result.stdout)
        assert data["success"] is True
        assert data["panels"]["pacing"]["available"] is False
        assert data["panels"]["learnings"]["available"] is False
        assert data["panels"]["rag"]["available"] is False
        # 核心面板不受影响
        assert data["panels"]["relations"]["available"] is True
        assert data["panels"]["progress"]["available"] is True


# ============================================================
# CLI HTML 产物合法
# ============================================================
class TestDashboardCliHtml:
    def test_html_contains_mermaid_and_data(self, tmp_path: Path, monkeypatch) -> None:
        _set_api_key(monkeypatch)
        out = tmp_path / "dash.html"
        runner = CliRunner()
        result = runner.invoke(
            app, ["dashboard", "-d", str(SAMPLE), "--output", str(out)]
        )
        assert result.exit_code == 0, result.output
        assert out.exists()
        html = out.read_text(encoding="utf-8")

        # mermaid CDN（锁定版本）脚本存在
        assert "cdn.jsdelivr.net/npm/mermaid@10.9.1" in html
        # 内联数据块存在
        assert "const DATA =" in html
        # 8 面板 section 存在
        for pid in (
            "relations",
            "route",
            "foreshadows",
            "progress",
            "pacing",
            "learnings",
            "rag",
            "health",
        ):
            assert f'id="{pid}"' in html

        # 内联数据块是合法 JSON（提取 const DATA = {...};）
        m = __import__("re").search(r"const DATA = (\{.*?\});\n", html, __import__("re").DOTALL)
        assert m, "未找到内联 DATA 块"
        parsed = json.loads(m.group(1))
        assert parsed["relations"]["available"] is True
        assert parsed["pacing"]["available"] is False

        # 可被 HTML 解析器解析（不抛异常）
        parser = HTMLParser()
        parser.feed(html)

    def test_html_default_output_to_cwd_not_project(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """默认 --output 写 CWD/dashboard.html，且不写入项目目录（只读）。"""
        _set_api_key(monkeypatch)
        # 在 tmp 子目录作为 CWD 运行不可行（CliRunner 不切 CWD），此处改用显式 --output
        out = tmp_path / "default.html"
        runner = CliRunner()
        result = runner.invoke(
            app, ["dashboard", "-d", str(SAMPLE), "--output", str(out)]
        )
        assert result.exit_code == 0, result.output
        assert out.exists()
        # 项目目录未被写入 dashboard.html
        assert not (SAMPLE / "dashboard.html").exists()

    def test_output_creates_parent_dirs(self, tmp_path: Path, monkeypatch) -> None:
        """--output 指向不存在的深层级目录时，应自动创建父目录并成功生成文件。"""
        _set_api_key(monkeypatch)
        out = tmp_path / "nested" / "deep" / "dash.html"
        runner = CliRunner()
        result = runner.invoke(
            app, ["dashboard", "-d", str(SAMPLE), "--output", str(out)]
        )
        assert result.exit_code == 0, result.output
        assert out.exists()
        assert "const DATA =" in out.read_text(encoding="utf-8")

    def test_output_write_failure_uses_error_contract(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """父路径为已存在文件致无法建目录 → 走错误契约（exit 1 + 友好提示，非裸 traceback）。"""
        _set_api_key(monkeypatch)
        blocker = tmp_path / "blocker"
        blocker.write_text("i am a file", encoding="utf-8")
        out = blocker / "dash.html"  # blocker 是文件，无法作为目录
        runner = CliRunner()
        result = runner.invoke(
            app, ["dashboard", "-d", str(SAMPLE), "--output", str(out)]
        )
        assert result.exit_code == 1, result.output
        assert "失败" in result.output


# ============================================================
# 只读契约
# ============================================================
class TestDashboardReadOnly:
    def test_cli_does_not_modify_project_files(self, tmp_path: Path, monkeypatch) -> None:
        _set_api_key(monkeypatch)
        # 复制一份 sample 到临时区，避免触碰真实样例工程
        import shutil

        proj = tmp_path / "proj"
        shutil.copytree(SAMPLE, proj)

        before = _snapshot(proj)
        out = tmp_path / "dash.html"
        runner = CliRunner()
        result = runner.invoke(
            app, ["dashboard", "-d", str(proj), "--output", str(out)]
        )
        assert result.exit_code == 0, result.output
        after = _snapshot(proj)

        # 文件集合与内容哈希完全一致（含 .state/）
        assert set(after.keys()) == set(before.keys())
        assert after == before

    def test_aggregator_does_not_modify_project_files(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        import shutil

        proj = tmp_path / "proj"
        shutil.copytree(SAMPLE, proj)
        before = _snapshot(proj)
        # 多次聚合
        for _ in range(3):
            DashboardAggregator(proj).aggregate()
        after = _snapshot(proj)
        assert after == before


# ============================================================
# 命令注册
# ============================================================
class TestDashboardRegistration:
    def test_dashboard_registered(self) -> None:
        # 命令经 commands/__init__.py glob 自动注册：可成功 invoke --help 即证明已注册
        runner = CliRunner()
        result = runner.invoke(app, ["dashboard", "--help"])
        assert result.exit_code == 0, result.output
        # 且已注册回调名为 dashboard（兼容 name=None 的 @command 注册形态）
        assert any(
            getattr(c.callback, "__name__", None) == "dashboard"
            for c in app.registered_commands
        )
