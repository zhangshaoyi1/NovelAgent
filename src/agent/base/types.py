"""公共类型定义

提供常用类型别名，避免各个模块重复定义。
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Awaitable

# 常用类型别名
JsonDict = Dict[str, Any]
"""JSON 字典类型"""

ModelName = str
"""模型名称类型"""

TokenCount = int
"""Token 计数类型"""

# 异步回调类型
AsyncCallback = Callable[[Any], Awaitable[None]]
"""异步回调函数类型"""
