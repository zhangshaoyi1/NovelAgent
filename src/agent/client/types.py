"""LLM 响应类型定义"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class LLMResponse:
    """LLM 响应"""

    text: str
    usage: dict[str, int] = field(default_factory=dict)
    model: str = ""
    raw: Any = None