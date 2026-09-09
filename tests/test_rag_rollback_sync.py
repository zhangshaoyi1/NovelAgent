"""RAG 索引与回滚/重写的同步测试（2026-09-09）

背景事故：
- 写章侧与召回侧都以「.state/rag 目录是否存在」为开关，而建目录的唯一途径是
  手动 reindex 命令 → 从未执行过的项目（五灵破归档 70 章）永远零召回、零索引。
- ``agentic_write._maybe_index`` 用 ``hasattr(retriever, "index_chapter")`` 判断，
  而 Retriever 从未定义该方法 → 死分支，agentic 路径从不建索引。
- 回滚只 move 章节文件，不清理索引 → 已归档章节的切片残留，重写后新旧两版
  正文同时被召回（幽灵召回）。
- VectorStore 只有 add 没有 delete/upsert → 重写同章后旧切片永不消失。

本文件覆盖上述四条的修复：ensure 自举、Retriever.index_chapter 委托、
drop_chapters + 回滚联动、index_chapter 幂等、doctor 陈旧度。
"""

from __future__ import annotations

import json
from pathlib import Path

from agent.core.infra.doctor import Doctor
from agent.core.rag.indexer import Indexer
from agent.core.rag.retriever import Retriever
from agent.core.rag.vector_store import LocalVectorStore
from agent.workflows.evaluation.m10_rollback import M10RollbackWorkflow

from tests.conftest import FakeEmbedder, make_project


def _patch_embedder(monkeypatch) -> None:
    """把 Indexer 的默认 embedder 换成假的，避免测试联网"""
    monkeypatch.setattr(
        "agent.core.rag.indexer.Indexer._default_embedder",
        staticmethod(lambda: FakeEmbedder()),
    )


def _indexed_chapters(d: Path) -> set[int]:
    store = LocalVectorStore(d / ".state" / "rag" / "index.json")
    store.load()
    return {c.chapter_num for c in store.chunks if c.kind == "chapter"}


class TestDropChapters:
    def test_drop_chapters_removes_rolled_back_chunks(self, tmp_path: Path) -> None:
        d = make_project(tmp_path, n_chapters=3)
        idx = Indexer(d, embedder=FakeEmbedder())
        idx.reindex()
        assert _indexed_chapters(d) == {1, 2, 3}

        removed = idx.drop_chapters([2, 3])
        assert removed > 0
        # 内存与磁盘都应只剩第 1 章
        assert {c.chapter_num for c in idx.store.chunks if c.kind == "chapter"} == {1}
        assert _indexed_chapters(d) == {1}

    def test_drop_chapters_keeps_settings(self, tmp_path: Path) -> None:
        """删除章节切片不应误伤设定类切片（world/outline/角色…）"""
        d = make_project(tmp_path, n_chapters=2)
        idx = Indexer(d, embedder=FakeEmbedder())
        idx.reindex()
        before = [c for c in idx.store.chunks if c.kind != "chapter"]
        assert before, "样例项目应含设定类切片"

        idx.drop_chapters([1, 2])
        after = [c for c in idx.store.chunks if c.kind != "chapter"]
        assert len(after) == len(before)

    def test_drop_chapters_noop_when_absent(self, tmp_path: Path) -> None:
        d = make_project(tmp_path, n_chapters=1)
        idx = Indexer(d, embedder=FakeEmbedder())
        idx.reindex()
        assert idx.drop_chapters([99]) == 0


class TestIndexChapterIdempotent:
    def test_reindexing_same_chapter_does_not_duplicate(self, tmp_path: Path) -> None:
        """重写同章（回滚后重写/改稿）不应让切片翻倍"""
        d = make_project(tmp_path, n_chapters=2)
        idx = Indexer(d, embedder=FakeEmbedder())
        idx.reindex()
        ch_file = d / "chapters" / "ch002.md"

        idx.index_chapter(ch_file, "旧版正文：林寻 逃亡 推演。")
        after_first = len([c for c in idx.store.chunks if c.chapter_num == 2])

        idx.index_chapter(ch_file, "新版正文：林寻 反杀 追兵，撕开缺口。")
        after_second = len([c for c in idx.store.chunks if c.chapter_num == 2])

        assert after_first > 0
        assert after_second == after_first, "同章重复索引应幂等，切片数不应增长"

    def test_reindexed_chapter_returns_new_text(self, tmp_path: Path) -> None:
        """重写后召回命中的应是新版正文，旧版不得残留"""
        d = make_project(tmp_path, n_chapters=2)
        idx = Indexer(d, embedder=FakeEmbedder())
        idx.reindex()
        ch_file = d / "chapters" / "ch002.md"

        idx.index_chapter(ch_file, "旧版标记词：青冥剑断。")
        idx.index_chapter(ch_file, "新版标记词：赤霄枪出。")

        hits = Retriever(d, embedder=FakeEmbedder()).retrieve("标记词", top_k=10)
        texts = "".join(h.text for h in hits)
        assert "旧版标记词" not in texts
        assert "新版标记词" in texts


class TestRetrieverIndexChapter:
    def test_retriever_has_index_chapter(self, tmp_path: Path) -> None:
        """P0-1：agentic_write 依赖 hasattr(retriever, 'index_chapter')"""
        d = make_project(tmp_path, n_chapters=1)
        assert hasattr(Retriever(d, embedder=FakeEmbedder()), "index_chapter")

    def test_retriever_index_chapter_writes_index(self, tmp_path: Path) -> None:
        d = make_project(tmp_path, n_chapters=2)
        Indexer(d, embedder=FakeEmbedder()).reindex()

        ch_file = d / "chapters" / "ch003.md"
        ch_file.write_text(
            "---\nchapter: 3\n---\n\n# 第 3 章\n\n林寻 破局 反杀，赤霄枪出。",
            encoding="utf-8",
        )
        Retriever(d, embedder=FakeEmbedder()).index_chapter(ch_file)

        assert 3 in _indexed_chapters(d)


class TestEnsureBootstrap:
    def test_ensure_builds_index_when_missing(self, tmp_path: Path) -> None:
        """P0-3：从未 reindex 过的项目，写章后应自动自举"""
        d = make_project(tmp_path, n_chapters=3)
        assert not (d / ".state" / "rag").exists()

        res = Indexer(d, embedder=FakeEmbedder()).ensure()
        assert res["bootstrapped"] is True
        assert res["indexed_chunks"] > 0
        assert _indexed_chapters(d) == {1, 2, 3}

    def test_ensure_is_noop_when_index_ready(self, tmp_path: Path) -> None:
        d = make_project(tmp_path, n_chapters=2)
        idx = Indexer(d, embedder=FakeEmbedder())
        idx.reindex()

        res = Indexer(d, embedder=FakeEmbedder()).ensure()
        assert res["bootstrapped"] is False

    def test_ensure_does_not_retry_after_failed_bootstrap(self, tmp_path: Path) -> None:
        """自举失败后不再每章重复全量 embed（落 marker 防抖）"""
        d = make_project(tmp_path, n_chapters=3)
        rag_dir = d / ".state" / "rag"
        rag_dir.mkdir(parents=True, exist_ok=True)
        (rag_dir / ".bootstrapped").write_text("2026-09-09T00:00:00", encoding="utf-8")

        res = Indexer(d, embedder=FakeEmbedder()).ensure()
        assert res["bootstrapped"] is False
        assert res.get("skipped") == "already_attempted"
        assert not (rag_dir / "index.json").exists()

    def test_ensure_respects_disabled_flag(self, tmp_path: Path) -> None:
        d = make_project(tmp_path, n_chapters=2)
        res = Indexer(d, embedder=FakeEmbedder()).ensure(auto_bootstrap=False)
        assert res["bootstrapped"] is False
        assert res.get("skipped") == "disabled"


class TestRollbackSyncsIndex:
    def test_rollback_drops_archived_chapters_from_index(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """P0-2：回滚后索引不得残留被归档章节的切片"""
        _patch_embedder(monkeypatch)
        d = make_project(tmp_path, n_chapters=3)
        Indexer(d, embedder=FakeEmbedder()).reindex()
        assert _indexed_chapters(d) == {1, 2, 3}

        res = M10RollbackWorkflow(d).rollback_to_chapter(2)
        assert res.success is True
        assert sorted(res.archived_chapters) == ["ch002.md", "ch003.md"]

        # 索引应只剩第 1 章；若不清则会召回已判废的旧版正文（幽灵召回）
        assert _indexed_chapters(d) == {1}

    def test_rollback_without_index_is_noop(self, tmp_path: Path, monkeypatch) -> None:
        """无索引项目的回滚不应报错、也不应凭空建索引"""
        _patch_embedder(monkeypatch)
        d = make_project(tmp_path, n_chapters=3)
        res = M10RollbackWorkflow(d).rollback_to_chapter(2)
        assert res.success is True
        assert not (d / ".state" / "rag" / "index.json").exists()

    def test_rollback_failure_does_not_break_rollback(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """索引同步异常必须被吞掉，不得阻断回滚本身"""

        def _boom(self, nums, *, emit=True):  # noqa: ANN001
            raise RuntimeError("索引服务不可用")

        monkeypatch.setattr(Indexer, "drop_chapters", _boom)
        d = make_project(tmp_path, n_chapters=3)
        rag_dir = d / ".state" / "rag"
        rag_dir.mkdir(parents=True, exist_ok=True)

        res = M10RollbackWorkflow(d).rollback_to_chapter(2)
        assert res.success is True
        assert sorted(res.archived_chapters) == ["ch002.md", "ch003.md"]


class TestIndexStatsAndDoctor:
    def test_index_json_has_updated_at(self, tmp_path: Path) -> None:
        d = make_project(tmp_path, n_chapters=2)
        Indexer(d, embedder=FakeEmbedder()).reindex()
        data = json.loads((d / ".state" / "rag" / "index.json").read_text(encoding="utf-8"))
        assert data.get("updated_at")

    def test_stats_reports_missing_chapters(self, tmp_path: Path) -> None:
        d = make_project(tmp_path, n_chapters=3)
        Indexer(d, embedder=FakeEmbedder()).reindex()
        # 模拟"磁盘新增了第 4 章但索引没跟上"
        (d / "chapters" / "ch004.md").write_text(
            "---\nchapter: 4\n---\n\n# 第 4 章\n\n新的剧情推进。", encoding="utf-8"
        )
        st = Indexer(d, embedder=FakeEmbedder()).stats()
        assert st["chapters_on_disk"] == 4
        assert st["chapters_indexed"] == 3
        assert st["missing_chapters"] == [4]

    def test_doctor_warns_on_stale_index(self, tmp_path: Path, monkeypatch) -> None:
        """P1-2：索引落后于正文时必须告警（此前只要文件存在就判 ok）"""
        _patch_embedder(monkeypatch)
        d = make_project(tmp_path, n_chapters=3)
        Indexer(d, embedder=FakeEmbedder()).reindex()
        (d / "chapters" / "ch004.md").write_text(
            "---\nchapter: 4\n---\n\n# 第 4 章\n\n新的剧情推进。", encoding="utf-8"
        )

        items = [i for i in Doctor(d)._check_rag() if i.module == "rag"]
        assert len(items) == 1
        assert items[0].status == "warn"
        assert "未索引" in items[0].detail
        assert "reindex" in items[0].fix_command

    def test_doctor_ok_when_index_fresh(self, tmp_path: Path, monkeypatch) -> None:
        _patch_embedder(monkeypatch)
        d = make_project(tmp_path, n_chapters=3)
        Indexer(d, embedder=FakeEmbedder()).reindex()

        items = [i for i in Doctor(d)._check_rag() if i.module == "rag"]
        assert len(items) == 1
        assert items[0].status == "ok"
        assert "覆盖 3 章" in items[0].detail

    def test_doctor_warns_on_stale_sources(self, tmp_path: Path, monkeypatch) -> None:
        """切片 source 指向已被回滚移走的文件 → 告警"""
        _patch_embedder(monkeypatch)
        d = make_project(tmp_path, n_chapters=3)
        Indexer(d, embedder=FakeEmbedder()).reindex()
        (d / "chapters" / "ch003.md").unlink()

        items = [i for i in Doctor(d)._check_rag() if i.module == "rag"]
        assert items[0].status == "warn"
        assert "失效" in items[0].detail
