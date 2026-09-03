"""AgentLoop → AgentLoopExecutor 双向桥接（Phase 3）

将现有 AgentLoop 封装为 llmagent AgentLoopExecutor 兼容的接口，
同时保留旧 AgentLoop 的完整功能。

设计原则：
- 双向兼容：旧 AgentLoop 继续工作，新 AgentLoopExecutor 也能发现并执行
- 适配器模式：不对现有 AgentLoop 做任何侵入性修改
- 渐进迁移：上层业务代码可选择使用旧接口或新接口
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable, Optional

from agent.core.engine.agent_loop import (
    AgentAction,
    AgentLoop,
    DecideFn,
    DecideAsyncFn,
    LoopResult,
    LoopStep,
)


class AgentLoopBridge:
    """AgentLoop 双向桥接

    封装旧 AgentLoop，提供：
    - 旧接口：run() / run_async()（直接委托）
    - 新接口：execute()（兼容 llmagent AgentLoopExecutor 协议）
    - 工厂方法：from_agent_loop() / from_agent_loop_executor()
    """

    def __init__(
        self,
        agent_loop: AgentLoop,
    ) -> None:
        self._loop = agent_loop

    @property
    def loop(self) -> AgentLoop:
        """获取底层 AgentLoop 实例"""
        return self._loop

    # ---- 旧接口（直接委托） ----

    def run(self, task: str, initial_observation: str | None = None) -> LoopResult:
        """同步执行（旧接口）"""
        return self._loop.run(task, initial_observation)

    async def run_async(self, task: str, initial_observation: str | None = None) -> LoopResult:
        """异步执行（旧接口）"""
        return await self._loop.run_async(task, initial_observation)

    # ---- 新接口（兼容 llmagent AgentLoopExecutor） ----

    async def execute(self, run: Any) -> Any:
        """执行 ReAct 循环（兼容 llmagent Executor 协议）

        Args:
            run: llmagent TaskRun 实例，input 从 run.output 中获取

        Returns:
            修改后的 TaskRun
        """
        try:
            from llmagent.kernel.task import TaskStatus
        except ImportError:
            TaskStatus = None  # type: ignore[assignment]

        input_data = run.output or {}
        task = input_data.get("input", "")

        # 使用旧 AgentLoop.run_async 执行
        result = await self._loop.run_async(task)

        # 回填 TaskRun
        if TaskStatus is not None:
            run.status = TaskStatus.SUCCEEDED if result.finished else TaskStatus.FAILED
        run.error = "" if result.finished else "Agent 未在最大轮次内完成"
        run.output = {
            "finished": result.finished,
            "draft": result.draft,
            "iterations": result.iterations,
            "steps": [s.__dict__ if hasattr(s, "__dict__") else s for s in result.steps],
        }
        return run

    # ---- 工厂方法 ----

    @classmethod
    def from_components(
        cls,
        tools: list[Any] | Any,
        decide: DecideFn | None = None,
        decide_async: DecideAsyncFn | None = None,
        max_iterations: int = 12,
        system_prompt: str = "",
        **kwargs: Any,
    ) -> AgentLoopBridge:
        """从组件创建 AgentLoopBridge

        Args:
            tools: 工具列表或 ToolRegistry
            decide: 同步决策函数
            decide_async: 异步决策函数
            max_iterations: 最大轮次
            system_prompt: 系统提示
            **kwargs: 传递给 AgentLoop 的回调参数

        Returns:
            AgentLoopBridge 实例
        """
        loop = AgentLoop(
            tools=tools,
            decide=decide,
            decide_async=decide_async,
            max_iterations=max_iterations,
            system_prompt=system_prompt,
            **kwargs,
        )
        return cls(loop)

    @classmethod
    def from_agent_loop_executor(
        cls,
        executor: Any,
        decide: DecideFn | None = None,
        decide_async: DecideAsyncFn | None = None,
    ) -> AgentLoopBridge:
        """从 llmagent AgentLoopExecutor 创建桥接

        将新 Executor 包装为旧 AgentLoop 兼容的接口。
        注意：此方法需要 executor 提供 toolset 和 llm_think 等属性。

        Args:
            executor: llmagent AgentLoopExecutor 实例
            decide: 同步决策函数（可选，覆盖 executor 的 llm_think）
            decide_async: 异步决策函数（可选，覆盖 executor 的 llm_think）

        Returns:
            AgentLoopBridge 实例
        """
        # 从 executor 提取工具列表
        tools: list[Any] = []
        try:
            tool_names = executor.toolset.list_available()
            for name in tool_names:
                tool = executor.toolset.get(name)
                if tool:
                    from agent.core.engine.tool_contracts import Tool as OldTool

                    # 包装为旧 Tool 协议
                    wrapped = OldTool(
                        name=tool.name,
                        description=tool.spec.description,
                        parameters_schema=tool.spec.input_schema,
                    )
                    tools.append(wrapped)
        except Exception:
            pass

        # 构建旧 AgentLoop
        loop = AgentLoop(
            tools=tools,
            decide=decide,
            decide_async=decide_async,
            max_iterations=12,
        )
        return cls(loop)