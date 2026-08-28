"""Phase 5 · 上下文工程 离线测试（agent/tests/phase5/test_context.py）"""

import pytest

from agent.core.infra.context import ContextEngine, ContextItem


def _items() -> list[ContextItem]:
    return [
        ContextItem(key="sys", text="系统提示与动作协议", kind="system", ts=0),
        ContextItem(key="bible", text="全书设定：世界观与力量体系", kind="bible", ts=1),
        ContextItem(key="char", text="主角林惊羽的人物档案", kind="character", ts=2),
        ContextItem(key="fs", text="伏笔：断剑中封印的古老记忆", kind="foreshadow", ts=3),
        ContextItem(key="ev1", text="第1章 入门测试小事", kind="event", ts=4),
        ContextItem(key="ev2", text="第2章 日常切磋", kind="event", ts=5),
        ContextItem(key="ev3", text="第3章 集市偶遇", kind="event", ts=6),
        ContextItem(key="plot", text="第4章 宗门大比转折", kind="plot", ts=7),
    ]


def test_importance_weighting_favours_foreshadow_over_event():
    eng = ContextEngine()
    fs = ContextItem(key="fs", text="x", kind="foreshadow", ts=0)
    ev = ContextItem(key="ev", text="x", kind="event", ts=0)
    assert eng.importance(fs) > eng.importance(ev)
    assert eng.score(fs, 10) > eng.score(ev, 10)


def test_fit_keeps_high_value_within_budget_drops_events():
    eng = ContextEngine(token_budget=60, est_fn=len)  # 每条约 8~16 字
    items = _items()
    fitted = eng.fit(items)
    keys = {it.key for it in fitted}
    # 高价值条目必留
    assert {"sys", "bible", "char", "fs", "plot"} <= keys
    # 预算不足时低价值 event 被丢弃
    assert "ev1" not in keys or len(fitted) < len(items)
    # 输出按时间顺序
    assert [it.ts for it in fitted] == sorted(it.ts for it in fitted)


def test_fit_respects_budget_total():
    eng = ContextEngine(token_budget=50, est_fn=len)
    items = _items()
    fitted = eng.fit(items, budget=50)
    total = sum(len(it.text) for it in fitted)
    assert total <= 50


def test_compress_merges_old_events_into_summary():
    eng = ContextEngine()
    items = _items()
    compressed = eng.compress(items, keep_recent=2)
    summaries = [it for it in compressed if it.key == "__compressed__"]
    assert len(summaries) == 1
    assert "已压缩" in summaries[0].text
    # 近期 2 条 + 稳定前缀 + 摘要
    assert len(compressed) < len(items)


def test_compress_keeps_recent_events():
    eng = ContextEngine()
    items = _items()
    compressed = eng.compress(items, keep_recent=2)
    keys = {it.key for it in compressed}
    assert "ev3" in keys  # 最近事件保留
    assert "plot" in keys


def test_cache_breakpoint_marks_stable_kinds():
    eng = ContextEngine()
    items = _items()
    blocks = eng.build_prompt_blocks(items)
    by_content = {b["content"]: b for b in blocks}
    assert by_content["系统提示与动作协议"].get("cache_control") == {"type": "ephemeral"}
    assert by_content["全书设定：世界观与力量体系"].get("cache_control") == {"type": "ephemeral"}
    assert by_content["主角林惊羽的人物档案"].get("cache_control") == {"type": "ephemeral"}
    # 事件类不标断点
    assert "cache_control" not in by_content["第1章 入门测试小事"]


def test_prepare_pipeline_compress_fit_blocks():
    eng = ContextEngine(token_budget=200, est_fn=len)
    blocks = eng.prepare(_items())
    assert isinstance(blocks, list)
    assert all("content" in b for b in blocks)
    # 稳定前缀有断点
    assert any(b.get("cache_control") for b in blocks)


def test_injectable_summarizer_used():
    calls = []

    def my_sum(items: list[ContextItem]) -> str:
        calls.append(len(items))
        return f"SUMMARY({len(items)})"

    eng = ContextEngine(summarizer=my_sum)
    eng.compress(_items(), keep_recent=2)
    assert calls and calls[0] > 0
