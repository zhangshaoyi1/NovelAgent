"""Phase 5 · 真异步并发 离线测试（agent/tests/phase5/test_async.py）"""

import asyncio
import time

from agent.core.agent_loop import AgentAction, AgentLoop
from agent.core.tools.base import Tool, ToolRegistry, ToolResult


def test_tool_run_async_sync_tool_offloaded():
    seen = {}

    def sync_tool(x):
        seen["ran"] = True
        return f"ok:{x}"

    t = Tool("t", "desc", {"type": "object"}, sync_tool, is_async=False)
    res = asyncio.run(t.run_async(x=7))
    assert res.success and res.data == "ok:7"
    assert seen.get("ran") is True


def test_tool_run_async_async_tool_awaited():
    async def a_tool(x):
        await asyncio.sleep(0.001)
        return f"a:{x}"

    t = Tool("a", "desc", {"type": "object"}, a_tool, is_async=True)
    res = asyncio.run(t.run_async(x=3))
    assert res.success and res.data == "a:3"


def test_registry_call_async_unknown_returns_failure():
    reg = ToolRegistry()
    res = asyncio.run(reg.call_async("nope"))
    assert not res.success and "未知工具" in (res.error or "")


def test_call_many_async_runs_concurrently_not_sequentially():
    log = []

    async def slow(delay):
        log.append(("start", delay))
        await asyncio.sleep(delay)
        log.append(("end", delay))
        return "done"

    reg = ToolRegistry()
    reg.register(Tool("s1", "", {}, slow, is_async=True))
    reg.register(Tool("s2", "", {}, slow, is_async=True))

    start = time.monotonic()
    results = asyncio.run(
        reg.call_many_async(
            [
                {"name": "s1", "args": {"delay": 0.08}},
                {"name": "s2", "args": {"delay": 0.08}},
            ]
        )
    )
    elapsed = time.monotonic() - start
    # 并发：总耗时 ≈ max(0.08, 0.08) 而非 0.08+0.08
    assert elapsed < 0.15
    assert all(r.success for r in results)
    # 两个任务确实重叠：start 全部先于任一个 end
    starts = [i for i, e in enumerate(log) if e[0] == "start"]
    ends = [i for i, e in enumerate(log) if e[0] == "end"]
    assert starts[0] < ends[0] and starts[1] < ends[0]


def test_agent_loop_run_async_end_to_end():
    reg = ToolRegistry()

    def double(n):
        return n * 2

    reg.register(Tool("double", "x2", {"type": "object", "properties": {"n": {"type": "integer"}}}, double))

    async def decide(messages):
        saw_tool = any(
            "工具 double 返回" in m.get("content", "") for m in messages
        )
        if not saw_tool:
            return AgentAction(think="call", action="tool_call", tool="double", args={"n": 21})
        return AgentAction(think="done", action="finish", draft="42")

    loop = AgentLoop(tools=reg, decide_async=decide, max_iterations=8)
    res = asyncio.run(loop.run_async("compute something"))
    assert res.finished is True
    assert res.draft == "42"
    assert res.iterations == 2


def test_agent_loop_run_async_unknown_tool_degrades():
    reg = ToolRegistry()

    async def decide(messages):
        return AgentAction(think="x", action="tool_call", tool="ghost", args={})

    loop = AgentLoop(tools=reg, decide_async=decide, max_iterations=3)
    res = asyncio.run(loop.run_async("task"))
    # 未知工具降级为观察，循环不死、最终超迭代返回未完成
    assert res.finished is False
    assert res.iterations == 3
    assert any(s.tool == "ghost" for s in res.steps)
