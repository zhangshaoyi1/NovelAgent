"""基础基础设施层

职责：提供不依赖 Core 业务语义的基础设施，被所有上层包依赖。
- 自定义异常定义
- 注册表基类
- 统一重试机制
- 结构化输出（JSON Schema 生成）

依赖规则：仅依赖标准库，不依赖任何 agent 包内模块。
"""

from agent.base.llm import LLMError
from agent.core.base.exceptions import (
    FrozenFieldError,
    PreValidationBlocked,
)
from agent.core.base.registry import BaseRegistry
from agent.core.base.retry import retry, RetryError, retry_transport, retry_parse
from agent.core.base.structured_output import (
    pydantic_to_json_schema,
    extract_json,
    StructuredOutputError,
)

__all__ = [
    # exceptions
    "LLMError",
    "FrozenFieldError",
    "PreValidationBlocked",
    # retry
    "retry",
    "RetryError",
    "retry_transport",
    "retry_parse",
    # registry
    "BaseRegistry",
    # structured output
    "pydantic_to_json_schema",
    "extract_json",
    "StructuredOutputError",
]