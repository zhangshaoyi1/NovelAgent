"""Phase 5 · 多 Agent 协作 离线测试（agent/tests/phase5/test_collab.py）"""

import asyncio

import pytest

from agent.core.engine.collab import (
    AgentNode,
    CollaborationError,
    MessageBus,
    MultiAgentCoordinator,
    Subtask,
    SubtaskDAG,
)


def test_dag_topo_order_respects_deps():
    dag = SubtaskDAG()
    dag.add(Subtask(id="a", agent="x"))
    dag.add(Subtask(id="b", agent="x", deps=["a"]))
    dag.add(Subtask(id="c", agent="x", deps=["b"]))
    order = dag.topo_order()
    assert order.index("a") < order.index("b") < order.index("c")


def test_dag_cycle_detection():
    dag = SubtaskDAG()
    dag.add(Subtask(id="a", agent="x"))
    dag.add(Subtask(id="b", agent="x", deps=["a"]))
    # 手动构造环：让 a 反向依赖 b（不经 add 校验）
    dag.nodes["a"].deps = ["b"]
    with pytest.raises(ValueError):
        dag._check_cycle()


def test_linear_collab_feeds_upstream_outputs():
    planner = AgentNode("planner", "架构", lambda inp: {"outline": "三幕结构"})
    writer = AgentNode("writer", "执笔", lambda inp: f"章节基于{inp['deps']['plan']['outline']}")
    editor = AgentNode("editor", "主编", lambda inp: f"审定：{inp['deps']['write']}")

    coord = MultiAgentCoordinator([planner, writer, editor])
    coord.add_subtask("plan", "planner")
    coord.add_subtask("write", "writer", deps=["plan"])
    coord.add_subtask("edit", "editor", deps=["write"])
    out = coord.run()

    assert "三幕结构" in out["write"]
    assert "审定" in out["edit"]


def test_unregistered_agent_raises():
    coord = MultiAgentCoordinator([AgentNode("a", "x", lambda i: i)])
    with pytest.raises(ValueError):
        coord.add_subtask("t", "ghost")


def test_failure_triggers_replan_recovery_and_succeeds():
    def flaky(inp):
        raise RuntimeError("执笔失败")

    def recover(inp):
        return "修复后的章节"

    coord = MultiAgentCoordinator(
        [AgentNode("writer", "执笔", flaky), AgentNode("critic", "修复", recover)],
        replan_on_failure=lambda st, outputs: [
            Subtask(id=f"{st.id}_fix", agent="critic", deps=[st.id])
        ],
    )
    coord.add_subtask("write", "writer")
    out = coord.run()
    assert out["write_fix"] == "修复后的章节"


def test_failure_without_recovery_raises_collaboration_error():
    coord = MultiAgentCoordinator(
        [AgentNode("writer", "执笔", lambda i: (_ for _ in ()).throw(RuntimeError("x")))],
        replan_on_failure=lambda st, outputs: [],
    )
    coord.add_subtask("write", "writer")
    with pytest.raises(CollaborationError):
        coord.run()


def test_message_bus_publish_subscribe():
    bus = MessageBus()
    got = []
    bus.subscribe("done", lambda m: got.append(m))
    bus.publish("done", {"id": "a"})
    assert got == [{"id": "a"}]
    assert bus.history("done") == [("done", {"id": "a"})]


def test_bus_receives_subtask_events():
    coord = MultiAgentCoordinator(
        [AgentNode("p", "x", lambda i: "ok")]
    )
    coord.add_subtask("plan", "p")
    out = coord.run()
    assert out["plan"] == "ok"
    assert any(t == "subtask_done" for t, _ in coord.bus.history())


def test_async_collab_with_async_agents():
    async def a_writer(inp):
        await asyncio.sleep(0.001)
        return f"async:{inp['payload']['n']}"

    async def a_editor(inp):
        return f"edit:{inp['deps']['w']}"

    coord = MultiAgentCoordinator(
        [AgentNode("w", "执笔", a_writer), AgentNode("e", "主编", a_editor)]
    )
    coord.add_subtask("w", "w", payload={"n": 5})
    coord.add_subtask("e", "e", deps=["w"])
    out = asyncio.run(coord.run_async())
    assert out["w"] == "async:5"
    assert out["e"] == "edit:async:5"
