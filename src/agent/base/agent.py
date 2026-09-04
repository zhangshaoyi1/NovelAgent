"""Agent 抽象基类

所有领域 Agent（Planner/Writer/Editor/Evaluator 等）都继承此类，
提供统一的同步/异步运行接口。

设计要点：
- 每个 Agent 有唯一名称和角色描述
- 支持同步 run() 和异步 run_async()
- 输入输出类型明确
- 可注入依赖（便于测试）
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Generic, TypeVar

InputT = TypeVar("InputT")
OutputT = TypeVar("OutputT")


class Agent(ABC, Generic[InputT, OutputT]):
    """Agent 抽象基类

    所有领域智能体都应该继承此类，实现统一接口。

    类型参数：
        InputT: 输入类型（运行时输入数据）
        OutputT: 输出类型（运行后结果类型）
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Agent 名称（唯一标识）"""
        ...

    @property
    @abstractmethod
    def role(self) -> str:
        """Agent 角色描述（说明职责）"""
        ...

    @abstractmethod
    def run(self, input_data: InputT) -> OutputT:
        """同步执行 Agent

        Args:
            input_data: 输入数据

        Returns:
            执行结果
        """
        ...

    @abstractmethod
    async def run_async(self, input_data: InputT) -> OutputT:
        """异步执行 Agent

        Args:
            input_data: 输入数据

        Returns:
            执行结果
        """
        ...
