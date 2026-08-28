"""统一重试机制——三层重试策略：Transport/Parse/Business"""

from __future__ import annotations

import asyncio
import functools
import logging
import random
import time
from dataclasses import dataclass, field
from typing import (
    Any,
    Callable,
    Optional,
    Protocol,
    Tuple,
    Type,
    Union,
)

logger = logging.getLogger(__name__)

# 可重试异常类型
RetryableExceptionType = Union[
    Type[BaseException],
    Tuple[Type[BaseException], ...],
]


class RetryCallback(Protocol):
    """重试回调协议"""
    def __call__(self, attempt: int, exception: BaseException, wait: float) -> None:
        ...


@dataclass
class RetryConfig:
    """重试配置"""
    max_attempts: int = 3
    backoff: float = 2.0
    jitter: float = 0.1
    max_wait: float = 60.0
    retryable_exceptions: RetryableExceptionType = (
        TimeoutError,
        ConnectionError,
        ConnectionResetError,
    )
    on_retry: Optional[RetryCallback] = None


class RetryError(Exception):
    """重试耗尽异常"""
    def __init__(
        self,
        message: str,
        attempts: int,
        last_exception: Optional[BaseException] = None,
    ) -> None:
        super().__init__(message)
        self.attempts = attempts
        self.last_exception = last_exception


def _compute_wait(attempt: int, config: RetryConfig) -> float:
    """计算等待时间（指数退避 + jitter）"""
    base = config.backoff ** (attempt - 1)
    jitter = random.uniform(0, config.jitter * base)
    wait = min(base + jitter, config.max_wait)
    return wait


def retry(
    max_attempts: int = 3,
    backoff: float = 2.0,
    jitter: float = 0.1,
    max_wait: float = 60.0,
    retryable_exceptions: RetryableExceptionType = (
        TimeoutError,
        ConnectionError,
        ConnectionResetError,
    ),
    on_retry: Optional[RetryCallback] = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """统一重试装饰器

    用法:
        @retry(max_attempts=3, backoff=2.0, jitter=0.1)
        def chat_parse_json(prompt: str) -> dict:
            ...
    """
    config = RetryConfig(
        max_attempts=max_attempts,
        backoff=backoff,
        jitter=jitter,
        max_wait=max_wait,
        retryable_exceptions=retryable_exceptions,
        on_retry=on_retry,
    )

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            last_exception: Optional[BaseException] = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except retryable_exceptions as e:
                    last_exception = e
                    if attempt < max_attempts:
                        wait = _compute_wait(attempt, config)
                        if on_retry:
                            try:
                                on_retry(attempt, e, wait)
                            except Exception:
                                pass
                        logger.warning(
                            "重试 %s/%s: %s, 等待 %.1fs",
                            attempt, max_attempts, repr(e), wait,
                        )
                        time.sleep(wait)
                    else:
                        raise RetryError(
                            f"重试耗尽: {func.__name__} 在 {max_attempts} 次后失败",
                            attempts=max_attempts,
                            last_exception=e,
                        ) from e
            # 不应该到达这里
            raise RetryError(
                f"重试异常: {func.__name__}",
                attempts=max_attempts,
                last_exception=last_exception,
            )

        @functools.wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            last_exception: Optional[BaseException] = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return await func(*args, **kwargs)
                except retryable_exceptions as e:
                    last_exception = e
                    if attempt < max_attempts:
                        wait = _compute_wait(attempt, config)
                        if on_retry:
                            try:
                                on_retry(attempt, e, wait)
                            except Exception:
                                pass
                        logger.warning(
                            "重试 %s/%s: %s, 等待 %.1fs",
                            attempt, max_attempts, repr(e), wait,
                        )
                        await asyncio.sleep(wait)
                    else:
                        raise RetryError(
                            f"重试耗尽: {func.__name__} 在 {max_attempts} 次后失败",
                            attempts=max_attempts,
                            last_exception=e,
                        ) from e
            raise RetryError(
                f"重试异常: {func.__name__}",
                attempts=max_attempts,
                last_exception=last_exception,
            )

        # 自动选择同步/异步包装
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        return wrapper

    return decorator


# 预定义常用重试配置
def retry_transport() -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Transport Retry：网络超时/连接重置/429/5xx"""
    return retry(
        max_attempts=3,
        backoff=2.0,
        jitter=0.1,
        max_wait=30.0,
        retryable_exceptions=(
            TimeoutError,
            ConnectionError,
            ConnectionResetError,
            OSError,
        ),
    )


def retry_parse() -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Parse Retry：JSON 解析失败/缺少字段/格式错误"""
    return retry(
        max_attempts=2,
        backoff=1.0,
        jitter=0.05,
        max_wait=5.0,
        retryable_exceptions=(ValueError, KeyError, TypeError, json.JSONDecodeError),
    )


def retry_business() -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Business Retry：质量评估不通过/一致性校验失败"""
    return retry(
        max_attempts=1,  # 业务层不自动重试，降级或人工介入
        backoff=1.0,
        jitter=0.0,
        max_wait=1.0,
        retryable_exceptions=(),
    )


import json