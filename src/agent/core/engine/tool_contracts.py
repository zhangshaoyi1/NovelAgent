"""Tool 抽象与注册表（Phase 0）

设计目标：把 NovelAgent 的现有能力（RAG 检索、设定读写、字数、质检、导出、伏笔）
封装为**可被 LLM 调用的工具**，为上层的 Agentic Loop（缺口2）与 Function Calling（缺口1）铺路。

契约归属 engine/（agent_loop 是工具的执行方，接口与实现分离）：
- 本模块只含纯契约（Tool / ToolResult / ToolRegistry / @tool / registry 单例），零业务依赖
- 具体工具实现在 tools/builtins.py（依赖 story/rag/quality），经注册表装配
- engine 不反向依赖 tools/，避免 engine → tools → quality 的上行依赖链

形态对齐 MCP（Model Context Protocol）的 tool 描述：
    {"name", "description", "inputSchema"}
后续 ``genre_pack.mount_mcp``（v2 接口）可直接把本注册表暴露给外部 MCP server。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class ToolResult:
    """工具执行结果（统一结构，便于回灌给 LLM 或上层编排）"""

    success: bool
    data: Any = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"success": self.success, "data": self.data, "error": self.error}


class Tool:
    """单个工具

    Attributes:
        name: 工具名（LLM 调用时使用的标识）
        description: 自然语言描述（给 LLM 看，决定何时调用）
        parameters_schema: JSON Schema（inputSchema），描述入参
        execute: 实际执行函数（同步）；返回任意可 JSON 序列化对象或 ToolResult
        is_async: 是否为异步执行（预留，Phase 1 异步化使用）
    """

    def __init__(
        self,
        name: str,
        description: str,
        parameters_schema: dict[str, Any],
        execute: Callable[..., Any],
        is_async: bool = False,
    ) -> None:
        self.name = name
        self.description = description
        self.parameters_schema = parameters_schema
        self._execute = execute
        self.is_async = is_async

    def to_mcp_manifest(self) -> dict[str, Any]:
        """导出为 MCP 兼容的 tool 描述（v2 接入外部 MCP server 用）"""
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.parameters_schema,
        }

    def run(self, **kwargs: Any) -> ToolResult:
        """执行工具，异常自动包装为失败的 ToolResult（不向外抛）"""
        try:
            result = self._execute(**kwargs)
            if isinstance(result, ToolResult):
                return result
            return ToolResult(success=True, data=result)
        except Exception as e:  # noqa: BLE001 - 工具失败不应中断 Agent 循环
            return ToolResult(success=False, error=f"{type(e).__name__}: {e}")

    async def run_async(self, **kwargs: Any) -> ToolResult:
        """异步执行工具。

        - ``is_async`` 工具：直接 ``await``。
        - 同步工具：卸载到线程池（``run_in_executor``），**不阻塞事件循环**，
          从而允许同一循环中的其他协程（含其它工具）真正并发。
        """
        try:
            if self.is_async:
                result = await self._execute(**kwargs)
            else:
                loop = asyncio.get_running_loop()
                result = await loop.run_in_executor(
                    None, lambda: self._execute(**kwargs)
                )
            if isinstance(result, ToolResult):
                return result
            return ToolResult(success=True, data=result)
        except Exception as e:  # noqa: BLE001
            return ToolResult(success=False, error=f"{type(e).__name__}: {e}")


class ToolRegistry:
    """工具注册表（单例式全局注册）"""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> Tool:
        if tool.name in self._tools:
            # 允许重复注册（后注册的覆盖），便于测试/热替换
            pass
        self._tools[tool.name] = tool
        return tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def list(self) -> list[Tool]:
        return list(self._tools.values())

    def names(self) -> list[str]:
        return list(self._tools.keys())

    def call(self, name: str, **kwargs: Any) -> ToolResult:
        """按名调用工具；未知工具返回失败结果"""
        tool = self.get(name)
        if tool is None:
            return ToolResult(success=False, error=f"未知工具: {name}")
        return tool.run(**kwargs)

    async def call_async(self, name: str, **kwargs: Any) -> ToolResult:
        """异步按名调用工具（见 ``Tool.run_async``）。"""
        tool = self.get(name)
        if tool is None:
            return ToolResult(success=False, error=f"未知工具: {name}")
        return await tool.run_async(**kwargs)

    async def call_many_async(self, calls: list[dict[str, Any]]) -> list[ToolResult]:
        """并发执行多个**无依赖**的工具调用，真正并行（``asyncio.gather``）。

        ``calls`` 形如 ``[{"name": "rag_retrieve", "args": {...}}, ...]``。
        全部完成（无论成功失败）后按顺序返回结果列表，不中断整体。
        """
        tasks = [
            self.call_async(c["name"], **c.get("args", {})) for c in calls
        ]
        return await asyncio.gather(*tasks)

    def manifests(self) -> list[dict[str, Any]]:
        """全部工具的 MCP 描述列表（可直接喂给支持 tools= 的模型）"""
        return [t.to_mcp_manifest() for t in self._tools.values()]


# 全局注册表实例（内置工具在导入 builtins 模块时注册）
registry = ToolRegistry()


def tool(
    name: str | None = None,
    description: str | None = None,
    parameters_schema: dict[str, Any] | None = None,
    is_async: bool = False,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """工具装饰器：把一个函数注册为 Tool。

    用法::

        @tool(
            name="rag_retrieve",
            description="语义召回相关片段",
            parameters_schema={"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
        )
        def rag_retrieve(query: str) -> Any:
            ...
    """

    def deco(fn: Callable[..., Any]) -> Callable[..., Any]:
        tool_name = name or fn.__name__
        tool_desc = description or (fn.__doc__ or "").strip().split("\n")[0]
        schema = parameters_schema or {"type": "object", "properties": {}}
        registry.register(Tool(tool_name, tool_desc, schema, fn, is_async))
        return fn

    return deco
