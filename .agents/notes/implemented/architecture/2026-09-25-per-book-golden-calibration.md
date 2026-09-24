# Agent Note: 金三门禁的项目级标定接线（策略值必须真正驱动门禁）

Status: implemented

## Problem

``DEFAULT_QUALITY_POLICY["golden_three"]``（``gate`` / ``threshold`` / ``floor``）**已经存在**，
但只有 Web 质量面板读它做**显示**（``web/quality_admin.py`` 两处 ``.get("golden_three")``），
**没有任何一处用它驱动门禁**：

- ``cli/commands/autowrite.py`` 的 policy 消费面只有 ``cost.*`` 与 ``strict_review``；
  金三阈值来自 typer 选项默认值（且第三个位置参是 ``SIX_DIM_PASS_LINE`` 常量，
  **无法区分"用户显式传 60"与"没传"**）；
- ``evaluator`` / ``agentic_pipeline`` 的 ``golden_three_threshold`` / ``floor``
  只由 CLI→pipeline 透传。

后果是**假一致**：用户在 ``<project>/.state/quality_policy.json`` 写
``golden_three.threshold = 55``，质量面板会显示 55，而门禁仍按 60 判——
"改了阈值"这个动作本身不可靠。

**为什么现在需要它**（2026-09-25 灵荒工坊）：该书开篇是**刻意慢热**
（细纲对 ch001/ch002/ch003 三章均标注「爽点=无（压抑铺垫）」），六维判定真值
约 42/52/56、单维噪声 σ≈10–15 ⇒ 全局阈值 60/40 下判决接近抛硬币
（实测去偏后仍 58/61、爽点 33–36）。门禁阈值需要**按书标定**，而全局真源
（``core/quality/golden_policy.py`` 的唯一一处 60/40）**不应**被单本书改动——
它同时驱动批末金三、前三章写时门禁、迷爱看六维三条门禁。

## Decision

新增**唯一解析入口** ``core/quality/policy.py::golden_three_settings(policy, *, cli_threshold, cli_floor, cli_gate)``，
优先级：**CLI 显式 > 项目策略 > 全局真源**。

- 取值经 ``_policy_int`` 归一为 1–100；非法/越界一律回落真源
  （``.state/quality_policy.json`` 是**用户可编辑的系统边界**，坏值不得让整轮崩掉）；
- ``cli_gate=False``（``--no-golden-three-gate``）永远优先；``None`` 时由策略 ``gate`` 决定；
- ``autowrite`` 的两个选项默认值由 ``SIX_DIM_PASS_LINE`` / ``SIX_DIM_FLOOR`` 改为 ``None``
  （才能区分"没传"），并在 ``_qpolicy`` 加载**之后**解析（位置约束见下）；
- 灵荒工坊标定落盘 ``.state/quality_policy.json``：``{"golden_three": {"gate": true, "threshold": 55, "floor": 30}}``。

**位置约束（实测踩坑）**：解析块必须位于 ``_qpolicy = apply_profile(load_quality_policy(...))``
**之后**。首版写在 G10 预算解析之前 ⇒ ``UnboundLocalError: _qpolicy``，
被 ``tests/test_g6_cli.py::test_g6_cli_no_golden_three_gate`` 当场抓住。

## Alternatives considered

**Why not 直接改全局 ``SIX_DIM_FLOOR`` / ``SIX_DIM_PASS_LINE``？**
那是一处真源、同时驱动三条门禁与所有书（纪律 #19 + ``test_golden_threshold_ssot``）。
为单本书的慢热开篇下调全局触底线，会同时放宽迷爱看六维门禁 —— 影响面远超意图。

**Why not 只靠 CLI 参数（``--golden-three-threshold 55``）？**
Web 端「续写」按钮不会带这两个参数（本次续写就是 web 提交的），
等于"标定只在命令行生效"——又一种假一致。

**Why not 让策略直接覆盖 ``gate=True``（CLI 强开）？**
``--golden-three-gate`` 的 typer 默认值就是 ``True``，无法与"未传"区分。
故只支持「策略可关 + CLI 显式关优先」；要强开就写策略 ``gate: true``。

## Consequences

- 回归：``tests/test_g6_golden_three.py`` 新增 2 项（优先级/非法值回落；
  autowrite 必须经解析入口）；``-k "golden or appeal or evaluator or hard_dim"`` **99 passed**；
  ``test_golden_threshold_ssot`` 全绿（新代码未手写 40/60 字面量）。
- ``autowrite.py`` 保留 ``# noqa: F401`` 的真源导入：SSOT 红线 R4 要求每个消费者文件
  **导入真源**，即使本文件已不再直接消费 ``SIX_DIM_FLOOR``。
- 未解决：金三**内容真值压在标定线上**的问题——55/30 是"与慢热设计相容"的让步，
  该让步由作者在 ``quality_policy.json`` 显式声明（可随时回退 60/40）。
  判定方差本身（不可放宽硬指标由单次 LLM 判定）另见
  ``proposed/architecture/2026-09-25-hard-metric-judge-variance.md``。