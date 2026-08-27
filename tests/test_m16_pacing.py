"""Pacing 测试（增量 C / T04）

覆盖：
- PacingStore：add/get/dedup/load/save/clear + 损坏降级为空
- PacingTracker.extract：正常抽取 / llm=None 降级为空
- PacingTracker.reconcile：合并债务 + 记录爽点密度
- track-pacing 命令 CLI 集成：注入假 LLMClient，验证命令注册 + JSON 信封 + pacing.json 落盘
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

from agent.cli import app
from agent.client import LLMClient, LLMResponse
from agent.core.pacing_store import Debt, PacingStore
from agent.workflows.m16_pacing import PacingExtraction, PacingTracker
from typer.testing import CliRunner

from tests.conftest import CHAPTER_TEXT, _build_minimal_project, make_project


# ============================================================
# 假 LLMClient（抽取 Hook/CoolPoint/MicroPayoff/Debt）
# ============================================================
_EXTRACTION_JSON = {
    "hooks": ["开头悬念钩子"],
    "cool_points": ["爽点A", "爽点B"],
    "micro_payoffs": ["小揭示"],
    "debts": [
        {"id": "D-01", "desc": "镜面乱码", "kind": "foreshadow", "planted_ch": 3}
    ],
}


class _FakeExtractLLM:
    """假 LLMClient：chat_utility 返回固定的追读力抽取 JSON"""

    def __init__(self, *args, **kwargs) -> None:
        pass

    def chat_utility(self, messages, **kwargs) -> LLMResponse:
        return LLMResponse(
            text=json.dumps(_EXTRACTION_JSON, ensure_ascii=False),
            raw={},
            usage={},
        )


# ============================================================
# PacingStore
# ============================================================
class TestPacingStore:
    def test_add_and_get_open_debts(self, tmp_path: Path) -> None:
        store = PacingStore(tmp_path)
        store.add_debt(Debt(id="D-1", desc="x", kind="foreshadow", planted_ch=2))
        debts = store.get_open_debts()
        assert len(debts) == 1
        assert debts[0].id == "D-1"
        assert debts[0].kind == "foreshadow"

    def test_add_dedup_by_id(self, tmp_path: Path) -> None:
        store = PacingStore(tmp_path)
        store.add_debt(Debt(id="D-1", desc="x"))
        store.add_debt(Debt(id="D-1", desc="y"))  # 同 id 不重复
        assert len(store.get_open_debts()) == 1

    def test_load_missing_returns_empty(self, tmp_path: Path) -> None:
        ledger = PacingStore(tmp_path).load()
        assert ledger.open_debts == []
        assert ledger.cool_density == []

    def test_corrupt_file_degrades_to_empty(self, tmp_path: Path) -> None:
        f = tmp_path / ".state" / "pacing.json"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("{not valid json", encoding="utf-8")
        ledger = PacingStore(tmp_path).load()
        assert ledger.open_debts == []
        assert ledger.cool_density == []

    def test_clear_resets_ledger(self, tmp_path: Path) -> None:
        store = PacingStore(tmp_path)
        store.add_debt(Debt(id="D-1"))
        store.clear()
        assert store.get_open_debts() == []


# ============================================================
# PacingTracker
# ============================================================
class TestPacingTracker:
    def test_extract_returns_pacing_extraction(self, tmp_path: Path) -> None:
        d = make_project(tmp_path, n_chapters=1)
        tracker = PacingTracker(d, llm=_FakeExtractLLM())
        ext = tracker.extract("林寻 逃亡 推演 撕开缺口")
        assert isinstance(ext, PacingExtraction)
        assert ext.hooks == ["开头悬念钩子"]
        assert len(ext.cool_points) == 2
        assert len(ext.debts) == 1
        assert ext.debts[0].id == "D-01"

    def test_extract_llm_none_degrades_empty(self, tmp_path: Path) -> None:
        d = make_project(tmp_path, n_chapters=1)
        # 显式 llm=None → 降级为空抽取（不抛异常、不阻断）
        tracker = PacingTracker(d, llm=None)
        ext = tracker.extract("任意正文")
        assert ext.hooks == []
        assert ext.cool_points == []
        assert ext.debts == []

    def test_reconcile_merges_debts_and_density(self, tmp_path: Path) -> None:
        d = make_project(tmp_path, n_chapters=1)
        tracker = PacingTracker(d, llm=_FakeExtractLLM())
        store = PacingStore(d)
        ext = tracker.extract("林寻 逃亡")
        ledger = tracker.reconcile([], ext)
        store.save(ledger)

        loaded = PacingStore(d).load()
        assert len(loaded.open_debts) == 1
        assert loaded.open_debts[0].id == "D-01"
        # 爽点密度记录本章爽点数量
        assert loaded.cool_density == [2.0]

    def test_reconcile_dedup_across_chapters(self, tmp_path: Path) -> None:
        d = make_project(tmp_path, n_chapters=1)
        tracker = PacingTracker(d, llm=_FakeExtractLLM())
        store = PacingStore(d)
        # 同一 debt 抽取两次（模拟两章都埋下同一伏笔），调用方负责落盘
        ledger1 = tracker.reconcile([], tracker.extract("a"))
        store.save(ledger1)
        ledger2 = tracker.reconcile([], tracker.extract("b"))
        store.save(ledger2)
        assert len(store.load().open_debts) == 1


# ============================================================
# track-pacing 命令 CLI 集成
# ============================================================
class TestTrackPacingCommand:
    def test_track_pacing_range_json(self, tmp_path: Path, monkeypatch) -> None:
        d = make_project(tmp_path, n_chapters=2)
        monkeypatch.setattr(
            "agent.workflows.m16_pacing.LLMClient", _FakeExtractLLM
        )

        runner = CliRunner()
        result = runner.invoke(
            app, ["track-pacing", "--range", "1-2", "--json", "-d", str(d)]
        )
        assert result.exit_code == 0, result.output

        data = json.loads(result.stdout)
        assert data["success"] is True
        assert data["chapters"] == [1, 2]
        # 每章 2 个爽点 → 共 4
        assert len(data["cool_points"]) == 4
        # 两章都埋下同一 D-01 伏笔 → 账本去重后仅 1 条债务
        assert len(data["debts"]) == 1
        assert data["debts"][0]["id"] == "D-01"

        # pacing.json 已落盘（去重后 1 条开放债务；爽点密度每章 1 条）
        ledger = PacingStore(d).load()
        assert len(ledger.open_debts) == 1
        assert len(ledger.cool_density) == 2

    def test_track_pacing_all_when_no_range(self, tmp_path: Path, monkeypatch) -> None:
        d = make_project(tmp_path, n_chapters=3)
        monkeypatch.setattr(
            "agent.workflows.m16_pacing.LLMClient", _FakeExtractLLM
        )
        runner = CliRunner()
        result = runner.invoke(app, ["track-pacing", "--json", "-d", str(d)])
        assert result.exit_code == 0, result.output
        data = json.loads(result.stdout)
        assert data["chapters"] == [1, 2, 3]

    def test_track_pacing_no_world_errors(self, tmp_path: Path, monkeypatch) -> None:
        d = tmp_path / "empty"
        d.mkdir()
        monkeypatch.setattr(
            "agent.workflows.m16_pacing.LLMClient", _FakeExtractLLM
        )
        runner = CliRunner()
        result = runner.invoke(app, ["track-pacing", "--json", "-d", str(d)])
        assert result.exit_code == 1
        data = json.loads(result.stdout)
        assert data["success"] is False
        assert data["error"]["code"] == "no_world"


# 静默未使用导入告警
_ = (CHAPTER_TEXT, _build_minimal_project, MagicMock, LLMClient)
