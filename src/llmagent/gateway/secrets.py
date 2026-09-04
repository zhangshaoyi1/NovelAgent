"""★ 唯一读取 API key 的模块

非本模块的代码禁止读取 LLMAGENT_GATEWAY_* 环境变量。
ProviderRegistry 启动时注入凭据到适配器，适配器实例持有凭据、不回传。
"""

from __future__ import annotations

import os

# 允许读取的环境变量白名单（仅 LLMAGENT_GATEWAY_* 前缀）
PROVIDER_ENV_WHITELIST: list[str] = [
    "LLMAGENT_GATEWAY_OPENAI_API_KEY",
    "LLMAGENT_GATEWAY_OPENAI_BASE_URL",
    "LLMAGENT_GATEWAY_QWEN_API_KEY",
    "LLMAGENT_GATEWAY_QWEN_BASE_URL",
    "LLMAGENT_GATEWAY_OLLAMA_BASE_URL",
]


def load_credentials() -> dict[str, str]:
    """从环境变量读取凭据，仅返回白名单内的变量。"""
    result: dict[str, str] = {}
    for name in PROVIDER_ENV_WHITELIST:
        value = os.environ.get(name)
        if value:
            result[name] = value
    return result