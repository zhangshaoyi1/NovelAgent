"""六维评分门禁阈值的**唯一真源**（纪律 #19：跨模块共享常量须机器交叉核对）

为什么单独成模块
----------------
``60``（综合合格线）与 ``40``（单维触底线）此前在**至少八处各写一份**、
代码互不相识：

1. ``core/quality/policy.py`` —— ``DEFAULT_QUALITY_POLICY["golden_three"]``
2. ``core/quality/scoring/reader_appeal.py`` —— ``APPEAL_PASS_LINE`` / ``APPEAL_DIM_FLOOR``
   （六维评分器自身的判定线）
3. ``agents/evaluator.py`` —— 构造参数 ``appeal_threshold`` 默认值
4. ``agents/evaluator.py`` —— 构造参数 ``golden_three_threshold`` / ``floor`` 默认值
5. ``workflows/writing/m5_quality_gate.py`` —— ``GOLDEN_WRITE_GATE_TOTAL`` / ``FLOOR``
   （前三章**写时**门禁）
6. ``workflows/pipeline/agentic_pipeline.py`` —— 构造参数默认值（appeal + golden 两组）
7. ``cli/commands/autowrite.py`` —— typer 选项默认值
8. ``cli/commands/autowrite.py`` —— ``_cli_value(..., 60)`` 回落值

其中 ``m5_quality_gate.py`` 的原注释是「与 B4 golden_three_threshold 默认一致」
—— **用注释担保一致性**，正是纪律 #19 点名的反模式：注释不参与编译、不参与断言，
一次单边改名即**双向破裂**且无任何测试会失败。

★ 为什么这两个值特别值得收口
------------------------------
它们是**provider 敏感判据**：分数由 LLM（``ReaderAppealScorer`` 六维）产生，
换 provider 后分值分布可能整体位移，绝对阈值 ``60`` 会随之系统性**假失败或假通过**。
敏感面必须**只有一处可改**，否则"改阈值"这个动作本身就不可靠
（详见 ``core/quality/provider_sensitivity.py`` 的登记表与抽检结论）。

改动注意
--------
本对值同时驱动「批末金三门禁」「前三章写时门禁」「迷爱看六维门禁」三处，
**改值等于同时改三条门禁**，必须跑 ``tests/test_golden_threshold_ssot.py``。
"""

from __future__ import annotations

#: 六维评分器的**综合合格线**（0–100）。低于此判不达标。
SIX_DIM_PASS_LINE = 60
#: 六维评分器的**单维触底线**（任一维低于此即触底，独立于综合线）。
SIX_DIM_FLOOR = 40

# ---- 语义别名（保留旧名供既有调用点/导出表使用，值同源，不是第二份真源）----
#: 黄金三章综合合格线（= 六维综合线）
GOLDEN_THREE_TOTAL = SIX_DIM_PASS_LINE
#: 黄金三章单维触底线（= 六维触底线）
GOLDEN_THREE_FLOOR = SIX_DIM_FLOOR

__all__ = [
    "SIX_DIM_PASS_LINE",
    "SIX_DIM_FLOOR",
    "GOLDEN_THREE_TOTAL",
    "GOLDEN_THREE_FLOOR",
]
