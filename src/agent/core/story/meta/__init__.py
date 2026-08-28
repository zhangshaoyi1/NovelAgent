"""元数据/元设计子包

职责：元层面的设计资料 — 产品哲学文案、世界观结构定义、写作方法论。
- philosophy.py：产品设计理念文案
- worldbuilding_schema.py：冰山设定结构定义

依赖：仅 base，不依赖其他子包。
"""

from agent.core.story.meta.philosophy import (
    TAGLINE,
    OPENING,
    POSITIONING,
    PILLARS,
    CLOSING,
    Pillar,
    render_text,
    render_markdown,
    get_philosophy,
)
from agent.core.story.meta.worldbuilding_schema import (
    IcebergField,
    IcebergDimension,
    IcebergGroup,
    get_iceberg,
    total_fields,
    summary,
)

__all__ = [
    "TAGLINE",
    "OPENING",
    "POSITIONING",
    "Pillar",
    "PILLARS",
    "CLOSING",
    "render_text",
    "render_markdown",
    "get_philosophy",
    "IcebergField",
    "IcebergDimension",
    "IcebergGroup",
    "get_iceberg",
    "total_fields",
    "summary",
]