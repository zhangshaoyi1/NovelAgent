"""G9 流式逐段渲染测试（T7 验收，纯离线）

覆盖（对齐设计 §6.2 / §9 T7）：
- RenderStreamer.stream_text 批次顺序与内容拼接 == 原文；
- --no-stream 退化整块（不调 stream_text）且子阶段事件保留；
- 子阶段顺序 generate → quality_check（→ revise）；
- 渲染异常（console 抛错）不崩、仍返回 block 模式；
- JSON 模式不渲染正文（stdout 无正文；事件 JSONL 走 stderr）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from rich.console import Console

from agent.cli._render import RenderStreamer
from agent.cli.commands.autowrite import _make_on_event
from agent.core.engine.events import ProgressEventBus
from agent.core.engine.state_machine import State
from agent.workflows.writing.m5_write_chapter import M5WriteChapterWorkflow
from tests.conftest import _build_minimal_project, _build_mock_llm, QUALITY_FAIL


# ============================================================
# 1. stream_text 批次顺序与拼接 == 原文
# ============================================================
def test_stream_text_concatenation_equals_original() -> None:
    captured: list[str] = []
    p1 = "第一段正文。" * 60   # 360 字
    p2 = "第二段正文。" * 60   # 360 字
    text = p1 + "\n\n" + p2
    streamer = RenderStreamer(Console(quiet=True), min_batch_chars=200, throttle_s=1.0)
    meta = streamer.stream_text(
        text, min_interval_s=0.0, sink=lambda t, end="\n": captured.append(t)
    )
    assert meta["mode"] == "stream"
    assert meta["chars"] == len(text)
    assert meta["batches"] >= 2, "长文应分批输出"
    assert "".join(captured) == text, "批次拼接应等于原文"


def test_stream_text_short_text_single_batch() -> None:
    captured: list[str] = []
    text = "短文本段落一。\n\n短文本段落二。"
    streamer = RenderStreamer(Console(quiet=True), min_batch_chars=200)
    meta = streamer.stream_text(text, min_interval_s=0.0,
                                sink=lambda t, end="\n": captured.append(t))
    assert meta["mode"] == "stream"
    assert "".join(captured) == text


# ============================================================
# 2. --no-stream 退化整块（不调 stream_text）
# ============================================================
def _write_chapter_file(d: Path, chapter: int = 1) -> Path:
    ch = d / "chapters"
    ch.mkdir(parents=True, exist_ok=True)
    f = ch / f"ch{chapter:03d}.md"
    f.write_text(
        "---\nchapter: 1\npressure_stage: 铺垫\n---\n"
        f"# 第 {chapter} 章\n\n第一段正文。\n\n第二段正文。",
        encoding="utf-8",
    )
    return f


def test_no_stream_does_not_stream_text(tmp_path: Path) -> None:
    d = tmp_path / "p"
    d.mkdir(parents=True)
    _write_chapter_file(d)
    on_event, stream_meta = _make_on_event(
        json_output=False, no_progress=False, no_stream=True, project_dir=d
    )
    on_event({
        "type": "chapter_done", "chapter": 1, "words": 10,
        "quality_passed": True, "chapter_elapsed_s": 5, "eta_s": None,
    })
    assert stream_meta == [], "--no-stream 不应调用 stream_text（不产生渲染元信息）"


def test_stream_wires_chapter_text(tmp_path: Path) -> None:
    d = tmp_path / "p"
    d.mkdir(parents=True)
    _write_chapter_file(d)
    on_event, stream_meta = _make_on_event(
        json_output=False, no_progress=False, no_stream=False, project_dir=d
    )
    on_event({
        "type": "chapter_done", "chapter": 1, "words": 10,
        "quality_passed": True, "chapter_elapsed_s": 5, "eta_s": None,
    })
    assert stream_meta, "默认（未 --no-stream）应在 chapter_done 后流式渲染正文"
    assert stream_meta[-1]["mode"] == "stream"
    assert stream_meta[-1]["chars"] > 0


# ============================================================
# 3. 子阶段顺序 generate → quality_check → revise
# ============================================================
# 2026-09-16：原三项「M5 精确边界」子阶段断言（经 `M5WriteChapterWorkflow.run()`）
# 已随废弃写章入口删除（登记单 20260916_闸门信号可达性普查 §三.C2）。
# ⚠ 能力缺口（已登记待办）：章内子阶段事件（chapter_substage）现由
# `AgenticWriteWorkflow._emit_substage` 生产，但**尚无覆盖其阶段顺序的测试**。


# ============================================================
# 4. 渲染异常 → 退化整块（不崩）
# ============================================================
def test_stream_render_exception_degrades_to_block() -> None:
    streamer = RenderStreamer(Console(quiet=True))

    def boom(text, markup=False, end="\n"):
        raise RuntimeError("console 抛错")

    streamer.console.print = boom  # type: ignore[method-assign]
    text = "x" * 500
    meta = streamer.stream_text(text, min_interval_s=0.0)
    assert meta["mode"] == "block", "渲染异常应退化整块模式"
    assert meta["chars"] == len(text)


# ============================================================
# 5. JSON 模式不渲染正文（事件 JSONL 走 stderr，不污染 stdout）
# ============================================================
def test_json_mode_no_story_text(tmp_path: Path, capsys) -> None:
    d = tmp_path / "p"
    d.mkdir(parents=True)
    _write_chapter_file(d)
    on_event, stream_meta = _make_on_event(
        json_output=True, no_progress=False, no_stream=False, project_dir=d
    )
    on_event({
        "type": "chapter_done", "chapter": 1, "words": 10,
        "quality_passed": True, "chapter_elapsed_s": 5, "eta_s": None,
    })
    captured = capsys.readouterr()
    assert stream_meta == [], "JSON 模式不渲染正文（无 stream_meta）"
    assert "第一段正文" not in captured.out, "JSON 模式 stdout 不应出现正文"
    err = captured.err
    ev_line = [l for l in err.splitlines() if l.strip() and l.strip().startswith("{")]
    assert ev_line, "JSON 模式事件应以 JSONL 走 stderr"
    parsed = json.loads(ev_line[-1])
    assert parsed["type"] == "chapter_done"
    assert parsed["chapter"] == 1
