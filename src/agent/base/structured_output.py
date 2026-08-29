"""结构化输出（base 层，仅依赖标准库 + pydantic）

目标：把 LLM 的"自由文本输出"升级为"强类型、可校验的结构化输出"，
替代散落在各处的启发式 `parse_llm_json` 调用（参见 技术现状与Agent化差距分析.md 缺口3）。

提供：
- ``pydantic_to_json_schema``：pydantic v2 模型 → OpenAI 兼容 ``json_schema``
- ``extract_json``：稳健 JSON 提取（复用 ``agent.base.utils.parse_llm_json`` 作为回退）
- ``StructuredOutputError``：结构化输出失败统一异常

下沉说明（2026-08-29）：本模块原位于 ``core/base/structured_output.py``。因
``client/LLMClient`` 需复用其能力、而 client 低于 core（依赖链 ``core → client → base``），
直接引用会造成 ``client → core`` 反向依赖。现下沉到 base 层，core/client 均自 base 引用；
``core/base/structured_output.py`` 保留为向后兼容再导出。

调用方（如 LLMClient.chat_structured）负责把 json_schema 塞进 ``response_format``；
provider 不支持时回退到"无 response_format + 文本解析"。
"""

from __future__ import annotations

from typing import Any, Type

from pydantic import BaseModel

from agent.base.utils import parse_llm_json


class StructuredOutputError(Exception):
    """结构化输出失败（含 response_format 不被支持、解析失败等）"""


def pydantic_to_json_schema(model: Type[BaseModel] | dict[str, Any]) -> dict[str, Any]:
    """把 pydantic v2 模型（或既有的 dict schema）转成 OpenAI 兼容 json_schema。

    strict 模式要求：所有属性 required + ``additionalProperties=false``。
    这里统一补齐，调用方再决定是否开启 ``strict``（部分 OpenAI 兼容端点不支持）。

    Args:
        model: pydantic 模型类，或已存在的 dict schema。

    Returns:
        可放入 ``response_format.json_schema.schema`` 的 dict。
    """
    if isinstance(model, dict):
        schema: dict[str, Any] = dict(model)
    else:
        schema = dict(model.model_json_schema())

    props = schema.get("properties")
    if props:
        schema["properties"] = props
        # strict 友好：补齐 required
        if "required" not in schema:
            schema["required"] = list(props.keys())
    # strict 友好：禁止额外字段
    schema["additionalProperties"] = False
    # 去掉 pydantic 自带的 $defs 引用复杂度对部分端点的不友好（保留但不再深究）
    schema.pop("title", None)
    return schema


def extract_json(text: str) -> dict[str, Any]:
    """从模型原始输出稳健提取 JSON dict。

    优先复用 ``agent.base.utils.parse_llm_json``（处理 ```json 围栏、首尾括号配对等）。
    失败抛出 ``StructuredOutputError``。

    Args:
        text: 模型原始输出文本。

    Returns:
        解析后的 dict。

    Raises:
        StructuredOutputError: 无法解析为 JSON。
    """
    try:
        return parse_llm_json(text)
    except Exception as e:  # noqa: BLE001 - 统一包装为结构化输出异常
        raise StructuredOutputError(f"无法从模型输出解析 JSON: {e}") from e


__all__ = ["StructuredOutputError", "pydantic_to_json_schema", "extract_json"]
