"""Multi-Agent Collaboration（Phase 5 · 多 Agent 协作）

把 Phase 2 的「线性 Pipeline（Planner→Writer→Editor→Evaluator）」升级为**基于消息总线
+ 子任务 DAG** 的协作框架：

- **Agent 节点** ``AgentNode``：每个 Agent 是一个节点（name / role / run），可注入（离线测试）。
- **子任务 DAG** ``SubtaskDAG``：声明依赖关系，拓扑排序执行；下游消费上游产出。
- **动态重规划** ``replan_on_failure``：子任务失败时可注入恢复子任务（如 Critic 修复），
  局部重跑，而非整条链路崩溃——对应「不崩」工程哲学的协作层落地。
- **消息总线** ``MessageBus``：Agent 间 / 编排与外部（CLI）通过发布-订阅解耦通信。

全部离线、零依赖；Agent 为可注入函数（同步 / 异步均支持）。
"""

from __future__ import annotations

import asyncio
import inspect
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable


class CollaborationError(Exception):
    """协作执行失败（存在未恢复的失败子任务）。"""


@dataclass
class Subtask:
    """DAG 中的一个子任务。"""

    id: str
    agent: str
    deps: list[str] = field(default_factory=list)
    payload: dict[str, Any] = field(default_factory=dict)
    status: str = "pending"   # pending | done | failed
    output: Any = None
    error: str | None = None


class SubtaskDAG:
    """子任务有向无环图：增删、环检测、拓扑排序。"""

    def __init__(self) -> None:
        self.nodes: dict[str, Subtask] = {}

    def add(self, task: Subtask) -> Subtask:
        if task.id in self.nodes:
            raise ValueError(f"子任务已存在：{task.id}")
        for d in task.deps:
            if d not in self.nodes:
                raise ValueError(f"依赖的子任务不存在：{d}")
        self.nodes[task.id] = task
        self._check_cycle()
        return task

    def _check_cycle(self) -> None:
        WHITE, GRAY, BLACK = 0, 1, 2
        color = defaultdict(int)

        def dfs(u: str) -> None:
            color[u] = GRAY
            for v in self.nodes[u].deps:
                if color[v] == GRAY:
                    raise ValueError(f"检测到环：{u} -> {v}")
                if color[v] == WHITE:
                    dfs(v)
            color[u] = BLACK

        for nid in self.nodes:
            if color[nid] == WHITE:
                dfs(nid)

    def topo_order(self) -> list[str]:
        """返回拓扑序（依赖在前）。"""
        self._check_cycle()
        visited: set[str] = set()
        order: list[str] = []

        def dfs(u: str) -> None:
            if u in visited:
                return
            visited.add(u)
            for v in self.nodes[u].deps:
                dfs(v)
            order.append(u)

        for nid in self.nodes:
            dfs(nid)
        return order


class MessageBus:
    """轻量发布-订阅消息总线（编排 / Agent / 外部观察者解耦）。"""

    def __init__(self) -> None:
        self._subs: dict[str, list[Callable[[Any], None]]] = defaultdict(list)
        self._log: list[tuple[str, Any]] = []

    def subscribe(self, topic: str, cb: Callable[[Any], None]) -> None:
        self._subs[topic].append(cb)

    def publish(self, topic: str, message: Any) -> None:
        self._log.append((topic, message))
        for cb in self._subs.get(topic, []):
            cb(message)

    def history(self, topic: str | None = None) -> list[tuple[str, Any]]:
        if topic is None:
            return list(self._log)
        return [(t, m) for t, m in self._log if t == topic]


class AgentNode:
    """一个 Agent 节点。``run`` 可为同步或异步函数。"""

    def __init__(self, name: str, role: str, run: Callable[[dict[str, Any]], Any]) -> None:
        self.name = name
        self.role = role
        self.run = run
        self.is_async = inspect.iscoroutinefunction(run)

    def invoke(self, inp: dict[str, Any]) -> Any:
        return self.run(inp)

    async def invoke_async(self, inp: dict[str, Any]) -> Any:
        if self.is_async:
            return await self.run(inp)
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, lambda: self.run(inp))


class MultiAgentCoordinator:
    """多 Agent 协作编排器。

    Args:
        agents: ``AgentNode`` 列表或 ``dict[name, AgentNode]``。
        replan_on_failure: 失败恢复钩子 ``(subtask, outputs) -> list[Subtask]``；
            返回恢复子任务（可依赖失败 id），空列表表示放弃并抛 ``CollaborationError``。
        bus: 可选消息总线（子任务完成 / 失败时发布事件）。
    """

    def __init__(
        self,
        agents: list[AgentNode] | dict[str, AgentNode],
        replan_on_failure: Callable[[Subtask, dict[str, Any]], list[Subtask]] | None = None,
        bus: MessageBus | None = None,
    ) -> None:
        if isinstance(agents, dict):
            self.agents = agents
        else:
            self.agents = {a.name: a for a in agents}
        self.replan = replan_on_failure or (lambda st, out: [])
        self.bus = bus or MessageBus()
        self.dag = SubtaskDAG()

    # ------------------------------------------------------------------
    def add_subtask(
        self,
        id: str,
        agent: str,
        deps: list[str] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> Subtask:
        if agent not in self.agents:
            raise ValueError(f"未注册的 Agent：{agent}")
        return self.dag.add(Subtask(id=id, agent=agent, deps=deps or [], payload=payload or {}))

    @staticmethod
    def _build_input(st: Subtask, outputs: dict[str, Any]) -> dict[str, Any]:
        return {
            "payload": st.payload,
            "deps": {d: outputs[d] for d in st.deps if d in outputs},
        }

    def _run_node(self, name: str, inp: dict[str, Any]) -> Any:
        return self.agents[name].invoke(inp)

    async def _run_node_async(self, name: str, inp: dict[str, Any]) -> Any:
        return await self.agents[name].invoke_async(inp)

    # ------------------------------------------------------------------
    # 同步执行
    # ------------------------------------------------------------------
    def run(self) -> dict[str, Any]:
        outputs: dict[str, Any] = {}
        failed: list[str] = []
        pending = list(self.dag.topo_order())

        while pending:
            progressed = False
            for st_id in list(pending):
                st = self.dag.nodes[st_id]
                if not all(d in outputs for d in st.deps):
                    continue
                inp = self._build_input(st, outputs)
                try:
                    out = self._run_node(st.agent, inp)
                    st.output = out
                    st.status = "done"
                    outputs[st.id] = out
                    self.bus.publish("subtask_done", {"id": st.id, "agent": st.agent})
                    pending.remove(st.id)
                    progressed = True
                except Exception as e:  # noqa: BLE001
                    st.error = str(e)
                    recoveries = self.replan(st, outputs)
                    if recoveries:
                        pending.remove(st.id)
                        for r in recoveries:
                            # 恢复子任务不应依赖已失败的节点（其无产出）；自动剔除该依赖，
                            # 恢复逻辑应从失败节点的 payload 重建输入。
                            r.deps = [d for d in r.deps if d != st.id and d in self.dag.nodes]
                            self.dag.add(r)
                            pending.append(r.id)
                        progressed = True
                    else:
                        st.status = "failed"
                        failed.append(st.id)
                        self.bus.publish("subtask_failed", {"id": st.id, "error": str(e)})
                        pending.remove(st.id)
                        progressed = True
            if not progressed:
                break

        if failed:
            raise CollaborationError(f"未恢复的失败子任务：{failed}")
        return outputs

    # ------------------------------------------------------------------
    # 异步执行
    # ------------------------------------------------------------------
    async def run_async(self) -> dict[str, Any]:
        outputs: dict[str, Any] = {}
        failed: list[str] = []
        pending = list(self.dag.topo_order())

        while pending:
            progressed = False
            for st_id in list(pending):
                st = self.dag.nodes[st_id]
                if not all(d in outputs for d in st.deps):
                    continue
                inp = self._build_input(st, outputs)
                try:
                    out = await self._run_node_async(st.agent, inp)
                    st.output = out
                    st.status = "done"
                    outputs[st.id] = out
                    self.bus.publish("subtask_done", {"id": st.id, "agent": st.agent})
                    pending.remove(st.id)
                    progressed = True
                except Exception as e:  # noqa: BLE001
                    st.error = str(e)
                    recoveries = self.replan(st, outputs)
                    if recoveries:
                        pending.remove(st.id)
                        for r in recoveries:
                            # 恢复子任务不应依赖已失败的节点（其无产出）；自动剔除该依赖，
                            # 恢复逻辑应从失败节点的 payload 重建输入。
                            r.deps = [d for d in r.deps if d != st.id and d in self.dag.nodes]
                            self.dag.add(r)
                            pending.append(r.id)
                        progressed = True
                    else:
                        st.status = "failed"
                        failed.append(st.id)
                        self.bus.publish("subtask_failed", {"id": st.id, "error": str(e)})
                        pending.remove(st.id)
                        progressed = True
            if not progressed:
                break

        if failed:
            raise CollaborationError(f"未恢复的失败子任务：{failed}")
        return outputs
