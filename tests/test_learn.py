"""Learn 测试（增量 E / T05）

覆盖：
- LearningStore：add/list/clear + 同 category+text 去重 + 损坏降级为空
- LearningMiner.extract：正常提炼 / llm=None 降级空 / extract_and_save 落盘去重
- learn 命令 CLI 集成：add / list / clear / extract（注入假 LLMClient）
- M5 _load_context 注入 learnings（ctx["learnings"] / learnings_text）
"""

from __future__ import annotations

import json
from pathlib import Path

from agent.cli import app
from agent.core.learning_store import Learning, LearningStore
from agent.client import LLMResponse
from agent.workflows.m17_learn import LearningMiner
from typer.testing import CliRunner

from tests.conftest import _build_minimal_project, make_project


# ============================================================
# 假 LLMClient（提炼技法）
# ============================================================
_LEARN_JSON = {
    "learnings": [
        {"category": "hook", "text": "用『数据化绝境』开场立住反差"},
        {"category": "pacing", "text": "被动转主动结构，章末反转埋饵"},
    ]
}


class _FakeLearnLLM:
    def __init__(self, *args, **kwargs) -> None:
        pass

    def chat_utility(self, messages, **kwargs) -> LLMResponse:
        return LLMResponse(
            text=json.dumps(_LEARN_JSON, ensure_ascii=False),
            raw={},
            usage={},
        )


# ============================================================
# LearningStore
# ============================================================
class TestLearningStore:
    def test_add_and_list(self, tmp_path: Path) -> None:
        store = LearningStore(tmp_path)
        item = store.add("hook", "用数据化绝境开场")
        assert item.id
        assert item.category == "hook"
        items = store.list()
        assert len(items) == 1
        assert items[0].text == "用数据化绝境开场"

    def test_add_dedup_by_category_text(self, tmp_path: Path) -> None:
        store = LearningStore(tmp_path)
        store.add("hook", "A")
        store.add("hook", "A")  # 重复
        store.add("pacing", "A")  # 不同类别，保留
        assert len(store.list()) == 2

    def test_clear_returns_count(self, tmp_path: Path) -> None:
        store = LearningStore(tmp_path)
        store.add("hook", "A")
        store.add("pacing", "B")
        assert store.clear() == 2
        assert store.list() == []

    def test_corrupt_file_degrades_empty(self, tmp_path: Path) -> None:
        f = tmp_path / ".state" / "learnings" / "learnings.json"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("{broken", encoding="utf-8")
        assert LearningStore(tmp_path).load() == []


# ============================================================
# LearningMiner
# ============================================================
class TestLearningMiner:
    def test_extract_llm_none_returns_empty(self, tmp_path: Path) -> None:
        d = make_project(tmp_path, n_chapters=1)
        miner = LearningMiner(d, llm=None)
        assert miner.extract([1]) == []

    def test_extract_returns_learnings(self, tmp_path: Path) -> None:
        d = make_project(tmp_path, n_chapters=2)
        miner = LearningMiner(d, llm=_FakeLearnLLM())
        items = miner.extract([1, 2])
        assert len(items) == 2
        assert items[0].category == "hook"
        assert items[0].source_chapters == [1, 2]

    def test_extract_and_save_persists(self, tmp_path: Path) -> None:
        d = make_project(tmp_path, n_chapters=2)
        miner = LearningMiner(d, llm=_FakeLearnLLM())
        extracted = miner.extract_and_save([1, 2])
        assert len(extracted) == 2
        # 落盘成功
        loaded = LearningStore(d).load()
        assert len(loaded) == 2
        assert all(x.id for x in loaded)


# ============================================================
# learn 命令 CLI 集成
# ============================================================
class TestLearnCommand:
    def test_add_and_list_json(self, tmp_path: Path) -> None:
        d = _build_minimal_project(tmp_path)
        runner = CliRunner()
        # add
        r1 = runner.invoke(
            app, ["learn", "--action", "add", "--category", "hook",
                  "--text", "数据化绝境开场", "--json", "-d", str(d)]
        )
        assert r1.exit_code == 0, r1.output
        assert json.loads(r1.stdout)["success"] is True
        # list
        r2 = runner.invoke(app, ["learn", "--action", "list", "--json", "-d", str(d)])
        assert r2.exit_code == 0, r2.output
        data = json.loads(r2.stdout)
        assert data["count"] == 1
        assert data["learnings"][0]["category"] == "hook"

    def test_extract_json(self, tmp_path: Path, monkeypatch) -> None:
        d = make_project(tmp_path, n_chapters=2)
        monkeypatch.setattr("agent.cli.commands.learn.LLMClient", _FakeLearnLLM)
        runner = CliRunner()
        r = runner.invoke(
            app, ["learn", "--action", "extract", "--range", "1-2", "--json", "-d", str(d)]
        )
        assert r.exit_code == 0, r.output
        data = json.loads(r.stdout)
        assert data["success"] is True
        assert data["extracted"] == 2
        # 已落盘
        assert len(LearningStore(d).load()) == 2

    def test_clear_json(self, tmp_path: Path) -> None:
        d = make_project(tmp_path, n_chapters=1)
        store = LearningStore(d)
        store.add("hook", "A")
        runner = CliRunner()
        r = runner.invoke(app, ["learn", "--action", "clear", "--json", "-d", str(d)])
        assert r.exit_code == 0, r.output
        assert json.loads(r.stdout)["cleared"] == 1
        assert store.list() == []

    def test_add_requires_text(self, tmp_path: Path) -> None:
        d = make_project(tmp_path, n_chapters=1)
        runner = CliRunner()
        r = runner.invoke(app, ["learn", "--action", "add", "--json", "-d", str(d)])
        assert r.exit_code == 1
        assert json.loads(r.stdout)["error"]["code"] == "missing_text"

    def test_bad_action(self, tmp_path: Path) -> None:
        d = make_project(tmp_path, n_chapters=1)
        runner = CliRunner()
        r = runner.invoke(app, ["learn", "--action", "bogus", "--json", "-d", str(d)])
        assert r.exit_code == 1
        assert json.loads(r.stdout)["error"]["code"] == "bad_action"


# ============================================================
# M5 注入 learnings
# ============================================================
class TestM5LearningsInjection:
    def test_load_context_includes_learnings(self, tmp_path: Path) -> None:
        from agent.workflows.m5_write_chapter import M5WriteChapterWorkflow

        d = _build_minimal_project(tmp_path)
        LearningStore(d).add("hook", "用数据化绝境开场立住反差")
        wf = M5WriteChapterWorkflow(project_dir=d, llm_client=_FakeLearnLLM())
        ctx = wf._load_context()
        assert "learnings" in ctx
        assert len(ctx["learnings"]) == 1
        assert ctx["learnings"][0]["category"] == "hook"
        # learnings_text 非空（非默认提示语）
        assert "数据化绝境" in ctx["learnings_text"]

    def test_load_context_empty_when_no_learnings(self, tmp_path: Path) -> None:
        from agent.workflows.m5_write_chapter import M5WriteChapterWorkflow

        d = _build_minimal_project(tmp_path)
        wf = M5WriteChapterWorkflow(project_dir=d, llm_client=_FakeLearnLLM())
        ctx = wf._load_context()
        assert ctx["learnings"] == []
        assert ctx["learnings_text"] == "（暂无已沉淀的写法记忆）"
