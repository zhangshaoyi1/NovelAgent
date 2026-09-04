"""ProviderRegistry：适配器注册 + 调用 + 通道层故障转移"""

from __future__ import annotations

import time
from typing import Protocol

from ..models import ModelCard, PackedRequest, RawResponse, RouteDecision


class ModelProvider(Protocol):
    """模型适配器协议"""

    name: str

    def complete(self, packed: PackedRequest) -> RawResponse:
        ...

    def count_tokens(self, text: str) -> int:
        ...

    def model_card(self) -> ModelCard:
        ...


class ProviderRegistry:
    """Provider 注册中心"""

    def __init__(self) -> None:
        self._providers: dict[str, ModelProvider] = {}

    def register(self, name: str, provider: ModelProvider) -> None:
        """注册适配器"""
        self._providers[name] = provider

    def available(self) -> list[ModelCard]:
        """返回所有可用模型卡"""
        return [p.model_card() for p in self._providers.values()]

    def invoke(self, route: RouteDecision, packed: PackedRequest) -> RawResponse:
        """调用 + 通道层故障转移

        首次调用失败（5xx/429/超时）且非内容错误 → 选备用 provider 重试 1 次，
        标记 provider_failover；仍失败 → 抛异常交 ErrorClassifier。
        """
        # ① 找主 provider
        primary = self._get_provider(route.provider)
        if primary is None:
            raise RuntimeError(f"Provider '{route.provider}' 未注册")

        # ② 首次调用
        try:
            return primary.complete(packed)
        except Exception as exc:
            # 内容错误 / 确定性错误 → 不重试
            if self._is_deterministic(exc):
                raise

            # ③ 故障转移：找备用 provider
            fallback = self._find_fallback(route.provider)
            if fallback is None:
                raise

            # ④ 重试一次
            try:
                return fallback.complete(packed)
            except Exception as exc2:
                raise RuntimeError(
                    f"主 provider({route.provider}) 和备用 provider 均失败: {exc2}"
                ) from exc2

    def _get_provider(self, name: str) -> ModelProvider | None:
        return self._providers.get(name)

    def _find_fallback(self, exclude: str) -> ModelProvider | None:
        """找第一个不是 exclude 的可用的 provider"""
        for name, provider in self._providers.items():
            if name != exclude:
                return provider
        return None

    @staticmethod
    def _is_deterministic(exc: Exception) -> bool:
        """判断是否为确定性错误（参数错/内容违规等，不重试）"""
        name = type(exc).__name__.lower()
        return any(kw in name for kw in ["value", "invalid", "badrequest", "content"])