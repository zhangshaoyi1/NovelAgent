"""P0-3 原子文件写入（core/infra/atomic.py + m5_persist 接线）。

覆盖：
- atomic_write_text / atomic_write_bytes：正常写入生效、覆盖写、无临时残留；
- atomic_write_set：成功路径全部生效；
- atomic_write_set：模拟第 2 个文件预写失败 → 目标零污染（全成或全不成）；
- atomic_write_set：空写入集合报 ValueError；
- m5 _save_chapter：落盘走原子写（无 .tmp-atomic 残留）。
"""

from __future__ import annotations

import unittest.mock
from pathlib import Path

import pytest

from agent.core.infra.atomic import (
    atomic_write_bytes,
    atomic_write_set,
    atomic_write_text,
)


def test_atomic_write_text_roundtrip(tmp_path: Path) -> None:
    f = tmp_path / "a" / "b.txt"
    atomic_write_text(f, "内容一")
    assert f.read_text(encoding="utf-8") == "内容一"
    atomic_write_text(f, "内容二")  # 覆盖写
    assert f.read_text(encoding="utf-8") == "内容二"
    assert not list(tmp_path.rglob("*.tmp-atomic"))


def test_atomic_write_bytes_roundtrip(tmp_path: Path) -> None:
    f = tmp_path / "bin.dat"
    atomic_write_bytes(f, b"\x00\x01")
    assert f.read_bytes() == b"\x00\x01"


def test_write_set_success_all_committed(tmp_path: Path) -> None:
    f1 = tmp_path / "chapters" / "ch001.md"
    f2 = tmp_path / ".state" / "state.json"
    out = atomic_write_set({f1: "正文", f2: '{"progress": {}}'})
    assert [p.name for p in out] == ["ch001.md", "state.json"]
    assert f1.read_text(encoding="utf-8") == "正文"
    assert f2.read_text(encoding="utf-8") == '{"progress": {}}'
    assert not [p for p in tmp_path.rglob(".atomic-*")]  # 无临时目录残留


def test_write_set_failure_leaves_no_partial_files(tmp_path: Path) -> None:
    """模拟第 2 个文件预写失败：目标零污染（全成或全不成）。"""
    f1 = tmp_path / "chapters" / "ch001.md"
    f2 = tmp_path / ".state" / "state.json"
    f1.parent.mkdir()
    f1.write_text("旧正文", encoding="utf-8")  # 已有旧内容，必须保持原样

    original_write_bytes = Path.write_bytes
    calls = {"n": 0}

    def flaky_write_bytes(self: Path, data: bytes) -> None:
        calls["n"] += 1
        if calls["n"] == 2:  # 第 2 个文件（staging 阶段）抛磁盘故障
            raise OSError("模拟磁盘故障")
        original_write_bytes(self, data)

    with unittest.mock.patch.object(Path, "write_bytes", flaky_write_bytes):
        with pytest.raises(OSError, match="模拟磁盘故障"):
            atomic_write_set({f1: "新正文", f2: '{"p": 1}'})

    assert f1.read_text(encoding="utf-8") == "旧正文"  # 原文件未动
    assert not f2.exists()  # 未产生半成品
    assert not [p for p in tmp_path.rglob(".atomic-*")]  # 临时目录已清理


def test_write_set_empty_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        atomic_write_set({})


def test_save_chapter_uses_atomic_write(tmp_path: Path) -> None:
    """m5 _save_chapter 落盘后无 .tmp-atomic 残留（原子写生效证据）。"""
    from agent.core.story.evidence_chain import EvidenceChain
    from agent.workflows.writing.m5_write_chapter import M5WriteChapterWorkflow

    wf = M5WriteChapterWorkflow(tmp_path, llm_client=object(), pre_validate=False)
    ctx = {
        "chapter_num": 1,
        "subline_id": "S01",
        "route_node_id": "N01",
        "pressure_stage": "铺垫",
    }
    file = wf._save_chapter(ctx, "第一段。\n\n第二段。", "初现", 8, True, 0, EvidenceChain())
    assert file.exists()
    assert "第一段" in file.read_text(encoding="utf-8")
    assert not list((tmp_path / "chapters").glob("*.tmp-atomic"))
