"""Provider 适配器注册 + 调用 + 通道层故障转移

★ 唯一允许 import provider SDK 的模块。
"""

from __future__ import annotations

from .registry import ProviderRegistry, ModelProvider

__all__ = ["ProviderRegistry", "ModelProvider"]