"""两类「通过」的作用域标签（SSOT）——防同名混淆（H3 / B4）

背景
----
系统里有**两类判据**都会输出一个叫「通过」的结论，但判据强度与覆盖范围完全不同：

- **章级写时门禁**（``chapter_gate``）：规则扫描 + 单章 LLM 审稿，
  判「**这一章**能不能落盘」；
- **批级全书体检**（``book_health``）：跨章窗口的 LLM 严评（迷爱看六维/黄金三章/
  主线收敛…），判「**这一批 / 这本书**健不健康」。

同名 ``overall_pass`` ⇒ 消费方（Web / 审计 jsonl / 日志 / 复盘）会把
「章级规则通过」读成「全书体检通过」，把判据强度混为一谈
（同族实证：``evaluator_types`` 旧实现只看 ``overall_pass``，
降级维取 ``safe_default`` 达标时直接显示「✅ 通过」）。

本模块给每条 pass 记录发一个**作用域标签**，使二者**机器可区分**。
约定：新增任何输出「通过/不通过」的判据，必须先在 :data:`PASS_SCOPES` 登记
其作用域，并在其结论记录上调用 :func:`stamp`（红线 ``test_pass_scope_ssot.py``）。
"""

from __future__ import annotations

from typing import Any

#: 章级写时门禁（单章：规则 + 单章 LLM 审稿）
PASS_SCOPE_CHAPTER = "chapter_gate"
#: 批级 / 全书体检（跨章窗口 LLM 严评）
PASS_SCOPE_BOOK = "book_health"

#: 已登记的作用域（新增判据必须在此登记，红线锁成员资格）
PASS_SCOPES: tuple[str, ...] = (PASS_SCOPE_CHAPTER, PASS_SCOPE_BOOK)

#: 记录上的标签键
PASS_SCOPE_KEY = "pass_scope"


def stamp(record: Any, scope: str) -> Any:
    """给 pass 记录打作用域标签。

    - 只增不删：仅新增 ``pass_scope`` 键，不动既有字段；
    - **不覆盖**已有标签：已带标签的记录保持原标签（防上游标签被下游串味）；
    - 非 dict（如 None / 自定义对象）原样返回，绝不因此抛异常。
    """
    if not isinstance(record, dict):
        return record
    if PASS_SCOPE_KEY not in record:
        record[PASS_SCOPE_KEY] = scope
    return record
