"""事件化重试包装器

将 base/retry 的重试事件通过 EventBus 发出。
依赖方向：event_sourcing → base （正确：上层依赖下层）
"""

from __future__ import annotations

from typing import Any, Callable, Optional, Tuple, Type, Union

from agent.core.base.retry import RetryCallback, retry
from agent.core.event_sourcing.event_bus import EventBus
from agent.core.event_sourcing.event_model import EventType

RetryableExceptionType = Union[
    Type[BaseException],
    Tuple[Type[BaseException], ...],
]


def _build_on_retry(func_name: str, max_attempts: int) -> RetryCallback:
    """构建发送事件的重试回调"""

    def _on_retry(attempt: int, exception: BaseException, wait: float) -> None:
        try:
            bus = EventBus.get_instance()
            if attempt < max_attempts:
                bus.emit_event(
                    EventType.RETRY_ATTEMPTED,
                    payload={
                        "attempt": attempt,
                        "max_attempts": max_attempts,
                        "exception": repr(exception),
                        "wait": wait,
                        "func": func_name,
                    },
                )
            else:
                bus.emit_event(
                    EventType.RETRY_EXHAUSTED,
                    payload={
                        "attempts": max_attempts,
                        "exception": repr(exception),
                        "func": func_name,
                    },
                )
        except Exception:
            pass

    return _on_retry


def retry_with_events(
    max_attempts: int = 3,
    backoff: float = 2.0,
    jitter: float = 0.1,
    max_wait: float = 60.0,
    retryable_exceptions: RetryableExceptionType = (
        TimeoutError,
        ConnectionError,
        ConnectionResetError,
    ),
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """带事件发送的重试装饰器

    用法同 ``retry``，但自动将重试事件发到 EventBus。
    """

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        on_retry = _build_on_retry(func.__name__, max_attempts)
        return retry(
            max_attempts=max_attempts,
            backoff=backoff,
            jitter=jitter,
            max_wait=max_wait,
            retryable_exceptions=retryable_exceptions,
            on_retry=on_retry,
        )(func)

    return decorator


__all__ = [
    "retry_with_events",
]