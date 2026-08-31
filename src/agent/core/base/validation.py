"""边界数据不变式校验（G15 P0-5）

设计：对标 DeepWrite `zod + superRefine` 的「入口拒绝脏数据」思想，在结构化输出
进入领域层之前统一做 pydantic 边界校验（模型内 validator + 本模块的聚合 helper）。

- 模型内在不变式（唯一性、引用一致性、状态机合法性）在各 schema 的
  ``@field_validator / @model_validator`` 中声明（见 core/continuity 与 core/story）。
- ``validate_model`` 是本模块的对外统一入口：校验通过返回 ``(True, "", model)``，
  不合法返回 ``(False, message, None)``。调用方（LLMClient 结构化入口 / 领域层读取）
  依据返回值决定「触发 Parse Retry」还是「降级」，而**绝不把脏数据透传下去**。

依赖规则：仅依赖标准库 + pydantic（与 core/base 其它模块一致）。
"""

from __future__ import annotations

from typing import Any, Type, TypeVar

from pydantic import BaseModel, ValidationError

M = TypeVar("M", bound=BaseModel)


def validate_model(
    model_cls: Type[M],
    data: dict[str, Any] | Any,
) -> tuple[bool, str, M | None]:
    """入口统一校验：把任意结构化数据解析校验为一个领域模型。

    Args:
        model_cls: 目标 pydantic 模型类。
        data: 待校验的数据（dict 或模型实例）。

    Returns:
        ``(ok, message, model)``：
        - ok=True：校验通过，model 为解析后的实例；
        - ok=False：message 为校验错误摘要，model 为 None。
        调用方据此决定触发 Parse Retry 或降级，不把脏数据透传。
    """
    if isinstance(data, model_cls):
        return True, "", data
    try:
        return True, "", model_cls.model_validate(data)
    except ValidationError as e:
        return False, _format_error(e), None


def validate_many(
    model_cls: Type[M],
    items: list[Any],
) -> tuple[bool, str, list[M]]:
    """批量校验（返回逐条失败信息，任一条失败整体返回 False）。"""
    out: list[M] = []
    for idx, item in enumerate(items):
        ok, msg, model = validate_model(model_cls, item)
        if not ok or model is None:
            return False, f"[{idx}] {msg}", out
        out.append(model)
    return True, "", out


def _format_error(e: ValidationError) -> str:
    """把 ValidationError 折叠成单行摘要（首条错误足以定位）。"""
    errors = e.errors()
    if not errors:
        return "校验失败"
    first = errors[0]
    loc = ".".join(str(p) for p in first.get("loc", ()))
    return f"{loc}: {first.get('msg', '')}"


__all__ = ["validate_model", "validate_many"]