"""五条红线常量（唯一真源）

红线含义（§5.3.6）：
1. 全局预算熔断（超支必停）
2. 单 Trace 内最大总重试次数（防指数爆炸）
3. Replan 连续次数上限
4. 补偿失败必须转人工（不做无限自动补偿）
5. FailurePolicy 自身抛异常 → 降级为 NeverRetry

这些常量是框架强制约束，TaskSpec 不可覆盖。
业务模块禁止 import 本模块（由 RedLineGuard 引用）。
"""

# 单 Trace 总重试上限（防指数爆炸）
MAX_RETRY_PER_TRACE = 8

# 连续 Replan 上限
MAX_REPLAN_DEPTH = 3

# 补偿失败 → 强制人工
COMPENSATION_FAIL_ACTION = "escalate_human"

# 全局预算熔断开关
BUDGET_HARD_STOP = True

# 策略自身异常降级策略名
POLICY_ERROR_FALLBACK = "NeverRetry"

# 最大轮次上限（AGENT Task 用）
MAX_AGENT_TURNS = 25