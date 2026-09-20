"""章节字数下限的**唯一真源**（纪律 #19：跨模块共享常量必须机器交叉核对）

为什么单独成模块
----------------
``1500`` 这个下限此前在**两处各写一份**、代码互不相识：

- ``core/quality/scoring/quality_checker.py`` —— **写时门禁**的硬下限
  （``resolve_min_cjk_words`` 的兜底值，动态下限 ``max(1500, 目标×0.8)``）
- ``core/quality/book_checkup.py`` —— **全书体检**的「超短章」阈值
  （``DEFAULT_MIN_CHAPTER_CHARS``）

两处数值同源、来源不同 ⇒ 正是纪律 #19 的待爆形态：**一次改名即双向破裂**
——改了一边、另一边静默沿用旧值，体检与门禁口径分叉，而**没有任何测试会发现**
（因为既有红线只锁各自的存在，不锁两者的**派生关系**）。

本模块把下限收成一份，两个消费者**派生**而非重写。红线断言的是
**派生关系**（导入 + AST 禁写字面量），不是「数值相等」——后者会在
「两边都改回旧值」时巧合通过（同族：``test_pressure_stage_ssot`` 的 R8c）。

零依赖（仅标准库）：供 ``core/quality`` 与 ``core/quality/scoring`` 两侧安全导入，
不会因导入顺序造出环。
"""

from __future__ import annotations

#: 章节字数**恒硬下限**（去空白 CJK 字符数）。
#:
#: 语义：任何目标字数配置下都不会低于此值 —— 写时门禁用它兜底，
#: 全书体检用它判「超短章」。**改这个值等于同时改门禁与体检口径**，
#: 必须跑 ``tests/test_chapter_length_ssot.py``（R2 会拦截单边字面量）。
ABSOLUTE_MIN_CJK_WORDS = 1500

__all__ = ["ABSOLUTE_MIN_CJK_WORDS"]
