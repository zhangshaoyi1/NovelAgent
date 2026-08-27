"""消息协议定义

统一 LLM 对话消息的结构，避免各个模块各自定义。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class Role(str, Enum):
    """对话角色"""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


@dataclass
class Message:
    """单条对话消息

    统一消息结构，兼容所有 LLM 提供商。
    """

    role: Role
    """消息角色"""

    content: str
    """消息内容"""

    name: Optional[str] = None
    """工具调用时的工具名称（可选）"""

    def to_dict(self) -> dict:
        """转换为字典（适配 OpenAI 格式）"""
        data = {
            "role": self.role.value,
            "content": self.content,
        }
        if self.name is not None:
            data["name"] = self.name
        return data

    @classmethod
    def system(cls, content: str) -> Message:
        """创建 system 消息"""
        return cls(Role.SYSTEM, content)

    @classmethod
    def user(cls, content: str) -> Message:
        """创建 user 消息"""
        return cls(Role.USER, content)

    @classmethod
    def assistant(cls, content: str) -> Message:
        """创建 assistant 消息"""
        return cls(Role.ASSISTANT, content)
