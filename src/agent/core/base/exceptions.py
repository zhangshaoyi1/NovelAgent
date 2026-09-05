"""集中自定义异常

所有领域异常统一在此定义，避免散落在各业务模块、并被 CLI 直接捕获而耦合。
此模块为叶子模块（不依赖 agent 包内其他模块），可安全被任意层导入。

下沉说明（2026-08-29）：``LLMError`` 已随 LLM 协议类型一并下沉至
``agent.base.llm``（消除 ``client→core`` 反向依赖），本模块仅保留领域异常。
"""

from __future__ import annotations

from typing import Any


class FrozenFieldError(PermissionError):
    """尝试修改未解冻的冻结字段"""


class PreValidationBlocked(Exception):
    """E3 高严重度冲突，生成被前置门禁中断，需用户仲裁

    args:
        report: 冲突报告对象（ConflictReport）
    """

    def __init__(self, report: Any) -> None:
        self.report = report
        summary = getattr(report, "summary", "") or ""
        super().__init__(f"前置冲突检测拦截生成：{summary}")


class FatalProviderError(RuntimeError):
    """LLM Provider 致命错误（配额耗尽/欠费/鉴权失败等），重试与故障转移均无意义

    继承 RuntimeError 以兼容既有 ``except RuntimeError`` 调用点；
    调用方应通过 :func:`is_fatal_provider_error` 识别并立即熔断，
    而不是按瞬时故障退避重试。
    """


# 配额/鉴权类错误的特征（大小写不敏感）。HTTP 4xx 计费类错误码 + 常见
# 中英文文案（openai SDK 报错形如 "Error code: 403 - Free quota exhausted..."）。
_FATAL_PROVIDER_PATTERNS: tuple[str, ...] = (
    "free quota",
    "quota exhausted",
    "quota exceeded",
    "insufficient quota",
    "quota",
    "exhausted",
    "insufficient_balance",
    "insufficient balance",
    "arrears",
    "billing",
    "欠费",
    "余额不足",
    "配额",
    "费用不足",
    "api key",
    "unauthorized",
    "forbidden",
    "permission denied",
    "invalid_api_key",
    "authentication",
    "错误码: 401",
    "错误码: 402",
    "错误码: 403",
)


def is_fatal_provider_error(exc: BaseException | str) -> bool:
    """判断异常（或错误文本）是否为配额/鉴权类致命错误

    识别依据：HTTP 401/402/403 状态码或配额/欠费/鉴权关键词。
    此类错误重试、退避、故障转移都不会成功，调用方应立即中止并上报人工。
    """
    text = str(exc)
    lowered = text.lower()
    return any(p in lowered for p in _FATAL_PROVIDER_PATTERNS)
