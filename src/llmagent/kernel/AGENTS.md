# AGENTS.md - llmagent/kernel/ 核心运行时

## 职责

提供 Task 运行时、状态机、七统一门面骨架、红线常量。

## 核心模块（按 M 阶段）

### M0 基础

| 文件 | 导出 | 作用 |
|------|------|------|
| `task.py` | `Executor`, `FailurePolicy`, `TaskKind`, `TaskRun`, `TaskSpec`, `TaskStatus`, `ValidationPolicy` | Task 基础定义 |

### M1 七统一

| 文件 | 导出 | 作用 |
|------|------|------|
| `artifact.py` | `ArtifactStore` | 制品存储 |
| `checkpoint.py` | `CheckpointManager` | 检查点管理 |
| `event_bus.py` | `EventBus` | 事件总线 |
| `metrics.py` | `Metrics` | 度量指标 |
| `monitor.py` | `Monitor` | 监控 |
| `redlines.py` | 各种常量 | 红线常量（BUDGET_HARD_STOP / MAX_AGENT_TURNS / MAX_RETRY_PER_TRACE 等） |

### M2 完整校验器

| 文件 | 导出 | 作用 |
|------|------|------|
| `validator.py` | `AllOfValidator`, `AnyOfValidator`, `ChainValidator`, `Composer`, `JsonSchemaValidator`, `ModelRunner`, `NoOpValidator`, `PolicyResolver`, `PureRunner`, `QualityScoreValidator`, `ResultLedger`, `ValidationResult`, `Validator`, `ValidatorRegistry`, `ValidatorRunner`, `WeightedValidator`, `WordCountValidator` | 校验器框架 |

### M2 完整失败处理

| 文件 | 导出 | 作用 |
|------|------|------|
| `failure.py` | `Catcher`, `CaughtError`, `Compensator`, `ErrorClassifier`, `Escalator`, `FailureAction`, `FailureContext`, `FailureHandler`, `FailurePolicy`, `Mutator`, `PolicyResolver`, `RedLineGuard` | 失败处理 |

### M2 统治理

| 文件 | 导出 | 作用 |
|------|------|------|
| `catalog.py` | `Catalog`, `LineageGraph`, `PolicyLoader`, `SchemaGate`, `Versioner` | Catalog（任务注册表） |

### M3.1 Session 聚合根

| 文件 | 导出 | 作用 |
|------|------|------|
| `session.py` | `ChatContext`, `ContextBuilder`, `DialogueInterpreter`, `DialogueTurn`, `InputQueue`, `Session`, `SessionContext`, `SessionGate`, `SessionManager`, `SessionState`, `TaskContext` | 会话管理 |

### M3.2 AGENT Task

| 文件 | 导出 | 作用 |
|------|------|------|
| `agent.py` | `AgentLoopExecutor`, `EchoTool`, `Scratchpad`, `StopDecision`, `StopPolicy`, `Tool`, `ToolCall`, `ToolsetPolicy`, `ToolSpec`, `TurnValidator`, `WriteTool` | Agent 主循环（ReAct） |

### M3.3 Planner

| 文件 | 导出 | 作用 |
|------|------|------|
| `planner.py` | `ExpansionPolicy`, `Plan`, `PlanNode`, `StaticDAG`, `TemplateRetrieval` | 计划编排 |

### M3.4 记忆写入

| 文件 | 导出 | 作用 |
|------|------|------|
| `memory.py` | `MemoryEntry`, `MemoryManager`, `MemoryStore`, `MemoryWritePolicy`, `SalienceFilter`, `WriteFailureCase`, `WriteHumanCorrection`, `WriteOnSuccess` | 记忆管理 |

### M3.5 人类介入

| 文件 | 导出 | 作用 |
|------|------|------|
| `human.py` | `HUMAN_TASK_SPEC`, `HumanTaskExecutor`, `HumanTicket`, `HumanTicketManager`, `SLAPolicy`, `TimeoutDefaultStrategy` | 人类介入 |

## 依赖规则

- `kernel/` 不依赖 `gateway/`、`tasks/` 等上层模块