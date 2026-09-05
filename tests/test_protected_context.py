"""P0-2 受保护上下文不静默压缩（core/infra/context.py）。

覆盖：
- compress：protected 条目永不并入压缩摘要（即便 kind 属可压缩类）；
- fit：protected 无条件保留，其余按预算填充；
- fit：protected 总量超预算时抛 ProtectedContextOverflowError，
  且异常信息携带逐条 token 统计（拒绝静默截断）；
- prepare：正常路径下 protected 条目完整进入最终块。
"""

from __future__ import annotations

import pytest

from agent.core.infra.context import (
    ContextEngine,
    ContextItem,
    ProtectedContextOverflowError,
)


def test_compress_never_merges_protected_items() -> None:
    eng = ContextEngine(token_budget=10_000)
    items = [
        ContextItem(key="old1", text="旧事件一", kind="event", ts=1),
        ContextItem(key="old2", text="旧事件二", kind="event", ts=2),
        ContextItem(
            key="ledger_fact", text="账本事实：主角已断臂", kind="event", ts=3, protected=True
        ),
        ContextItem(key="recent1", text="近期事件", kind="event", ts=4),
    ]
    out = eng.compress(items, keep_recent=1)
    keys = [it.key for it in out]
    # 受保护条目保持独立整条，绝不进入 __compressed__ 摘要
    assert "ledger_fact" in keys
    assert "__compressed__" in keys
    compressed = next(it for it in out if it.key == "__compressed__")
    assert "账本事实" not in compressed.text
    assert "旧事件一" in compressed.text


def test_compress_without_protected_unchanged_behavior() -> None:
    eng = ContextEngine(token_budget=10_000)
    items = [
        ContextItem(key="e1", text="事件一", kind="event", ts=1),
        ContextItem(key="e2", text="事件二", kind="event", ts=2),
        ContextItem(key="e3", text="事件三", kind="event", ts=3),
    ]
    out = eng.compress(items, keep_recent=1)
    keys = [it.key for it in out]
    assert keys.count("__compressed__") == 1
    assert "e3" in keys


def test_fit_always_keeps_protected_within_budget() -> None:
    eng = ContextEngine(token_budget=100)  # est: len//4
    items = [
        ContextItem(key="big1", text="x" * 200, kind="event", ts=1),  # 50 tokens
        ContextItem(key="big2", text="y" * 200, kind="event", ts=2),  # 50 tokens
        ContextItem(key="fact", text="z" * 40, kind="fact", ts=3, protected=True),  # 10
    ]
    out = eng.fit(items, budget=60)
    keys = [it.key for it in out]
    assert "fact" in keys  # 受保护必保留
    assert "big1" in keys or "big2" in keys  # 剩余预算仍填充普通条目
    # 预算未被突破
    assert sum(eng.est_fn(it.text) for it in out) <= 60


def test_fit_raises_when_protected_exceeds_budget() -> None:
    eng = ContextEngine(token_budget=50)
    items = [
        ContextItem(key="p1", text="a" * 120, kind="fact", ts=1, protected=True),  # 30
        ContextItem(key="p2", text="b" * 120, kind="fact", ts=2, protected=True),  # 30
    ]
    with pytest.raises(ProtectedContextOverflowError) as exc_info:
        eng.fit(items)
    err = exc_info.value
    assert err.protected_tokens == 60
    assert err.budget == 50
    assert dict(err.stats) == {"p1": 30, "p2": 30}
    # 错误信息可读（含总量与逐条统计提示）
    msg = str(exc_info.value)
    assert "protected=60" in msg and "budget=50" in msg
    assert "p1=30" in msg


def test_prepare_keeps_protected_in_final_blocks() -> None:
    eng = ContextEngine(token_budget=10_000)
    items = [
        ContextItem(key="e", text="普通事件", kind="event", ts=1),
        ContextItem(key="b", text="金手指边界", kind="bible", ts=2, protected=True),
    ]
    blocks = eng.prepare(items)
    texts = [b["content"] for b in blocks]
    assert "金手指边界" in texts
    assert "普通事件" in texts
