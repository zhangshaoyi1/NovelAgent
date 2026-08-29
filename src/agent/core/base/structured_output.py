"""结构化输出（向后兼容再导出）

下沉说明（2026-08-29）：实现已下沉至 ``agent.base.structured_output``（消除
``client → core`` 反向依赖）。本模块保留为向后兼容再导出，新代码应直接自
``agent.base.structured_output`` 引用。
"""

from __future__ import annotations

from agent.base.structured_output import (
    StructuredOutputError,
    extract_json,
    pydantic_to_json_schema,
)

__all__ = ["StructuredOutputError", "pydantic_to_json_schema", "extract_json"]
