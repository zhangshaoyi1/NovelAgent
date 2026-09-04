"""模型调用网关层

★ 唯一允许 import provider SDK 的目录。
★ 唯一读取 API key 的模块（gateway/secrets.py）。

Gateway.chat() 是全系统唯一的 LLM 出口。
"""

from .chat import Gateway, GatewayError

__all__ = [
    "Gateway",
    "GatewayError",
]