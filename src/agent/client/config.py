"""LLM 配置模型

定义 LLM 客户端所需的全部配置，支持从 .env 文件加载。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


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