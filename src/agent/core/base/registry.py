"""Registry 基类（DeepSeek Harness 风格）

提供通用注册表基础设施，所有可扩展模块（workflows/skills/providers/genres）
都遵循「动态发现 + 自动注册 + 按名获取」模式。

设计理念：
- **Registry** = 全局单例注册表，存储名称到对象/类的映射
- **Provider** = 每个模块自包含，通过装饰器自动注册，不需要手动修改中心字典
- **Consumer** = 从注册表按名获取，不硬编码导入路径
- **Open/Closed** = 对扩展开放，对修改关闭 —— 新增模块无需修改注册表代码
"""

from __future__ import annotations

from typing import Generic, TypeVar, Dict, Optional

T = TypeVar("T")


class BaseRegistry(Generic[T]):
    """通用注册表基类

    类型参数 T 是被注册对象的类型（如 Type[Workflow], Skill 等）。
    """

    def __init__(self) -> None:
        self._registry: Dict[str, T] = {}

    def register(self, name: str, item: T) -> T:
        """注册一个项目

        - 允许重复注册（后注册覆盖先注册），便于测试/热替换
        - 返回 item 自身，方便装饰器链式调用
        """
        self._registry[name] = item
        return item

    def get(self, name: str) -> Optional[T]:
        """按名获取，不存在返回 None"""
        return self._registry.get(name)

    def has(self, name: str) -> bool:
        """检查是否已注册"""
        return name in self._registry

    def list(self) -> list[str]:
        """列出所有已注册名称"""
        return list(self._registry.keys())

    def all(self) -> list[T]:
        """列出所有已注册项目"""
        return list(self._registry.values())

    def __len__(self) -> int:
        return len(self._registry)

    def __contains__(self, name: str) -> bool:
        return self.has(name)
