"""逃生舱（ESCAPE_HATCH）：显式标记的原生逻辑，受监控。

对应 m0-guardrails.md §8：允许极少数场景绕过七统一门禁，但必须——
1. **显式声明**：文件必须在 `declare()` 登记并写明理由；
2. **运行期留痕**：每次经 `escape_hatch()` 上下文绕过门禁都记录一条使用日志并
   向 stderr 打印红色告警；
3. **CI 卡口**：全局豁免数不得超过 `ESCAPE_HATCH_LIMIT`（默认 5），
   `tests/architecture/test_escape_hatch.py` 会校验声明数与使用日志。

用法::

    from llmagent.escape_hatch import declare, escape_hatch

    declare(__name__, "理由：xxx 确实无法经门禁表达")   # 模块级登记一次
    with escape_hatch(__name__, "本次绕过的原因"):
        ...  # 原生逻辑
"""

from __future__ import annotations

import sys
import threading
from contextlib import contextmanager
from typing import Iterator

# 全局豁免上限（m0-guardrails T3：阈值 5）
ESCAPE_HATCH_LIMIT = 5

_ANSI_RED = "\033[31m"
_ANSI_RESET = "\033[0m"

_lock = threading.Lock()
# 已登记豁免：module/file -> 理由
_declared: dict[str, str] = {}
# 运行期使用日志：每次进入 escape_hatch() 追加一条
_usage: list[dict[str, str]] = []


def declare(module: str, reason: str) -> None:
    """登记一个豁免文件（模块级调用一次）。

    登记数超过 `ESCAPE_HATCH_LIMIT` 时立即抛错（fail-fast，等价于 CI 卡口）。
    """
    with _lock:
        if module in _declared:
            _declared[module] = reason
            return
        if len(_declared) >= ESCAPE_HATCH_LIMIT:
            raise RuntimeError(
                f"[escape_hatch] 豁免登记已达上限 {ESCAPE_HATCH_LIMIT}，"
                f"禁止新增豁免：{module}（{reason}）。请先消除存量豁免。"
            )
        _declared[module] = reason


def declared() -> dict[str, str]:
    """当前全部豁免登记（module -> reason），供 CI 测试与控制台统计。"""
    with _lock:
        return dict(_declared)


def usage_log() -> list[dict[str, str]]:
    """运行期绕过日志（只读副本）。"""
    with _lock:
        return [dict(u) for u in _usage]


def within_limit() -> bool:
    return len(_declared) <= ESCAPE_HATCH_LIMIT


def reset() -> None:
    """清空登记与日志（仅供测试）。"""
    with _lock:
        _declared.clear()
        _usage.clear()


@contextmanager
def escape_hatch(module: str, reason: str) -> Iterator[None]:
    """绕过七统一门禁的作用域。必须先 declare() 过本 module。

    每次进入：记录使用日志 + stderr 红色告警（控制台标红）。
    未登记的 module 直接抛 RuntimeError，防止静默绕过。
    """
    with _lock:
        if module not in _declared:
            raise RuntimeError(
                f"[escape_hatch] {module} 未登记豁免，禁止绕过门禁。"
                f"请先在模块级调用 declare() 并写明理由。"
            )
        _usage.append({"module": module, "reason": reason})
    print(
        f"{_ANSI_RED}[escape_hatch] ⚠ {module} 绕过门禁：{reason}"
        f"{_ANSI_RESET}",
        file=sys.stderr,
        flush=True,
    )
    try:
        yield
    finally:
        pass
