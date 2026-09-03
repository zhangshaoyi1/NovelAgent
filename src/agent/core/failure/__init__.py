"""失败处理层：统一失败处理流水线

Phase 4 重构：从 llmagent 重新导出核心失败处理类型，
同时提供适配层（failure_adapter）兼容 agent 业务接口。

核心类型来自 llmagent.kernel.failure：
- FailureHandler: 完整失败处理门面
- FailureAction / FailureContext: 失败动作/上下文
- PolicyResolver: 策略解析器
- Catcher / ErrorClassifier: 异常捕获/分类
- Mutator / Compensator / Escalator: 修复/补偿/升级
- RedLineGuard: 红线守卫

依赖规则：依赖 llmagent，不依赖上层业务。
"""

from llmagent.kernel.failure import (
    Catcher,
    CaughtError,
    Compensator,
    ErrorClassifier,
    Escalator,
    FailureAction,
    FailureContext,
    FailureHandler,
    FailurePolicy,
    Mutator,
    PolicyResolver,
    RedLineGuard,
)

__all__ = [
    "Catcher",
    "CaughtError",
    "Compensator",
    "ErrorClassifier",
    "Escalator",
    "FailureAction",
    "FailureContext",
    "FailureHandler",
    "FailurePolicy",
    "Mutator",
    "PolicyResolver",
    "RedLineGuard",
]