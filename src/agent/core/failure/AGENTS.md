# AGENTS.md - core/failure/ 统一失败处理

## 职责

提供统一失败处理流水线（Phase 4 重构），从 llmagent 重新导出核心失败处理类型。

## 核心模块

| 导出 | 来源 | 作用 |
|------|------|------|
| `FailureHandler` | `llmagent.kernel.failure` | 完整失败处理门面 |
| `FailureAction` / `FailureContext` | `llmagent.kernel.failure` | 失败动作/上下文 |
| `PolicyResolver` | `llmagent.kernel.failure` | 策略解析器 |
| `Catcher` / `ErrorClassifier` | `llmagent.kernel.failure` | 异常捕获/分类 |
| `Mutator` / `Compensator` / `Escalator` | `llmagent.kernel.failure` | 修复/补偿/升级 |
| `RedLineGuard` | `llmagent.kernel.failure` | 红线守卫 |

## 依赖规则

- 依赖 llmagent
- 不依赖上层业务