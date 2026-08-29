"""LLM 协议层（base 层，仅依赖标准库）

职责：集中定义 LLM 相关的**协议类型**与 **Provider 注册表**，供 client（具体
Provider / LLMClient 实现）与 core（模型路由、预算等上层设施）共同复用，避免
``client→core`` 反向依赖（此前 ``client`` 需从 ``core.base.exceptions`` 取
``LLMError``，违反「client 仅依赖 base」的依赖方向）。

下沉说明（2026-08-29）：
- ``LLMError`` 原位于 ``core/base/exceptions.py``。
- ``LLMConfig`` 原位于 ``client/config.py``。
- ``LLMResponse`` 原位于 ``client/types.py``。
- ``LLMProvider`` 抽象基类 + ``register_provider`` 原位于 ``client/provider.py``；
  其 ``embed()`` 由抽象方法改为在 client 层具体 Provider 中实现（经由
  ``client/embedding_router``），保证本模块零外部依赖、零上层引用。

包含：
- LLMError：LLM 调用统一异常
- LLMConfig：LLM 配置模型
- LLMResponse：LLM 响应数据
- LLMProvider：抽象 Provider 接口（含注册表 + 工厂 create()）
- register_provider：Provider 注册装饰器

依赖规则：仅标准库，不依赖任何 agent 包内模块。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, ClassVar


class LLMError(Exception):
    """LLM 调用错误（配置缺失、重试后仍失败、双 Provider 均不可用等）"""


@dataclass
class LLMConfig:
    """LLM 配置

    支持多模型分工（创作/校验）、多 Provider 回退、嵌入端独立。

    配置来源（.env）：
        LLM_PROVIDER           - 提供商（openai | ollama，默认 openai）
        LLM_API_KEY            - API 密钥（ollama 不需要）
        LLM_BASE_URL           - 服务地址
        LLM_MODEL_ID           - 主模型（创作用）
        LLM_MODEL_UTILITY      - 轻量模型（校验用，可选）
        LLM_TIMEOUT            - 超时秒数（默认 120）
        LLM_MAX_RETRIES        - 重试次数（默认 3）
        LLM_FALLBACK_PROVIDER  - 备用 Provider（回退链）
        LLM_ENABLE_THINKING    - 思考开关
    """

    provider: str = "openai"
    api_key: str = ""
    base_url: str = ""
    model: str = "glm-5.2"
    model_utility: str = ""
    embedding_model: str = ""
    embedding_base_url: str = ""
    embedding_api_key: str = ""
    embedding_provider: str = ""  # ""=同 LLM_PROVIDER, "qwen_local"=本地 Qwen transformers 推理
    timeout: int = 120
    max_retries: int = 3
    retry_base_delay: float = 1.0
    # T-7：回退 Provider 列表（多候选回退链）
    fallback_provider: str = ""
    fallback_model: str = ""
    fallback_providers: list[str] = field(default_factory=list)
    # 思考开关：None=不干预，False=强制关闭，True=强制开启
    enable_thinking: bool | None = None

    def __post_init__(self) -> None:
        """归一化 fallback_providers"""
        if isinstance(self.fallback_providers, str):
            self.fallback_providers = [
                p.strip() for p in self.fallback_providers.split(",") if p.strip()
            ]
        if self.fallback_provider and not self.fallback_providers:
            self.fallback_providers = [
                p.strip() for p in self.fallback_provider.split(",") if p.strip()
            ]


@dataclass
class LLMResponse:
    """LLM 响应"""

    text: str
    usage: dict[str, int] = field(default_factory=dict)
    model: str = ""
    raw: Any = None


class LLMProvider(ABC):
    """LLM 提供商抽象接口

    Provider 注册表模式（DeepSeek Harness 风格）：
    - 新增 provider 只需继承 LLMProvider 并使用 @register_provider 装饰器
    - 装饰器自动注册到 PROVIDERS 注册表，无需手动修改中心字典
    - 对扩展开放，对修改关闭

    本类只承载协议与注册机制；具体 Provider（OpenAI/Ollama）位于 client 层。
    """

    PROVIDERS: ClassVar[dict[str, type["LLMProvider"]]] = {}

    def __init__(self, config: LLMConfig) -> None:
        self.config = config

    @abstractmethod
    def chat(
        self,
        messages: list[dict[str, str]],
        model: str,
        temperature: float,
        max_tokens: int | None,
        enable_thinking: bool | None,
        timeout: int,
        **kwargs: Any,
    ) -> LLMResponse:
        """对话补全。具体协议差异由子类实现。"""
        ...

    def embed(self, texts: list[str]) -> list[list[float]]:
        """生成文本嵌入向量。

        基类提供默认实现：抛 ``NotImplementedError``（避免 base 依赖上层 embedding
        路由）。具体 Provider（OpenAI/Ollama）在 client 层经 ``client/embedding_router``
        覆盖实现；调用方通常经 ``LLMClient.embed()`` 使用，其已带异常兜底降级。
        """
        raise NotImplementedError(
            f"{type(self).__name__} 未实现 embed()；请覆盖为经 embedding_router 的实现"
        )

    @staticmethod
    def create(config: LLMConfig) -> "LLMProvider":
        """根据配置创建 Provider（经 PROVIDERS 注册表，无 if/else）"""
        provider_name = config.provider.lower() or "openai"
        try:
            provider_cls = LLMProvider.PROVIDERS[provider_name]
        except KeyError:
            available = ", ".join(sorted(LLMProvider.PROVIDERS)) or "(空)"
            raise LLMError(
                f"未知 LLM provider: {provider_name!r}。可用 provider: {available}"
            ) from None
        return provider_cls(config)


def register_provider(
    provider_name: str | None = None,
) -> Callable[[type[LLMProvider]], type[LLMProvider]]:
    """LLM Provider 注册装饰器

    用法::

        from agent.base.llm import register_provider

        @register_provider("openai")
        class OpenAIProvider(LLMProvider):
            ...

    通过装饰器自动注册到 LLMProvider.PROVIDERS 注册表，
    新增 Provider 无需修改现有代码。
    """

    def decorator(cls: type[LLMProvider]) -> type[LLMProvider]:
        name = provider_name or cls.__name__.removesuffix("Provider").lower()
        LLMProvider.PROVIDERS[name] = cls
        return cls

    return decorator


__all__ = [
    "LLMError",
    "LLMConfig",
    "LLMResponse",
    "LLMProvider",
    "register_provider",
]
