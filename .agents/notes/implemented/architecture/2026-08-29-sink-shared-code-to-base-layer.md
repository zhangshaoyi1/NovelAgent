# Agent Note: 下沉共享代码至 base 层并修复跨层反向依赖
Status: implemented

> 基线恢复点：git tag `pre-base-sink-20260829`（本次 base 层下沉重构前代码与文档均可从此恢复）。
> 关联提案：[统一命令接口方案](../../proposed/architecture/2026-08-29-unified-command-architecture.md)（web→cli 命令注册副作用的后续消化）。

## Problem

分层依赖原则（`entry → workflows → agents → core → client → base`）要求**下层绝不依赖上层**。
但依赖审计发现多处违反该方向的依赖，导致底层模块被迫间接引用第三方库（rich）或上层模块：

- **`client → core` 反向依赖（两处）**：`client` 需要 `LLMError`（在 `core/base/exceptions.py`）
  与结构化输出（`core/base/structured_output.py`），违反「client 仅依赖 base」。
- **`core/base → agent.utils`**：`core/base/structured_output` 需要 `parse_llm_json`，但
  `agent/utils.py` 混入 rich 依赖，使底层基础设施间接依赖第三方库。
- **`core → workflows`**：`core/tools/builtins.py` 直接 import
  `workflows/m11_export.export_chapters` 做导出工具注册，违反依赖方向。
- **`agents → workflows`**：`agents/evaluator.py` 直接 import 并实例化
  `workflows/m10_rollback.M10RollbackWorkflow`，违反依赖方向。
- **`web → cli`**：`web/state.py` 直接经 `cli.commands.merge_genres.pending_conflicts`
  读冲突数据。CLI 与 Web 是兄弟消费者，谁也不该依赖谁。

这些反向依赖使分层架构形同虚设：底层一改、上层连带回归；新消费者（如 MCP）接入成本升高。

## Decision

下沉共享协议与纯工具到 `base` 层，并用接口反转消除跨层依赖（2026-08-29 已交付）：

- **纯工具函数下沉**：`parse_llm_json` / `safe_remove` / `chunk_text` 自 `agent/utils.py`
  迁移至 `agent/base/utils.py`（仅标准库）。`make_quiet_console`（依赖 rich）保留在
  `agent/utils.py`，属 CLI 渲染层职责；`agent/utils.py` 对下沉函数做向后兼容再导出。
- **LLM 协议层下沉**：`LLMError` / `LLMConfig` / `LLMResponse` / `LLMProvider` /
  `register_provider` 收敛到 `agent/base/llm.py`。`embed()` 由抽象方法改为默认
  `NotImplementedError`（具体实现随 `client/embedding_router`，自 core/llm 归位到 client），
  保证 base 零外部依赖。`client/provider.py` 仅保留 OpenAI/Ollama 具体实现，自 base 导入协议类型。
- **结构化输出下沉**：`pydantic_to_json_schema` / `extract_json` / `StructuredOutputError`
  自 `core/base/structured_output.py` 迁移至 `agent/base/structured_output.py`；
  `core/base/structured_output.py` 保留为向后兼容再导出。
- **D-I（core→workflows）**：`core/tools/builtins.py` 移除对 `m11_export.export_chapters`
  的直接依赖。改为「core 定义工具契约、workflows 用 `@tool` 装饰器注册实现」，
  `export_chapters` 的注册落在 `workflows/m11_export.py`。
- **D-J（agents→workflows）**：`agents/evaluator.py` 不再直接 import
  `M10RollbackWorkflow`。定义 `RollbackProvider` 协议 + 构造注入
  （`rollback_provider` 参数）；未注入时 `_resolve_rollback()` 懒加载兜底。
- **D-K（web→cli）**：`web/state.py` 不再经 `cli.commands.merge_genres.pending_conflicts`
  读冲突，改为直接读 `core/registry/genre_merger.load_conflicts`。

下沉后依赖方向复核结论：`core` 仅依赖 `client`；`client` 仅依赖 `base`；
`agents` 不硬依赖 `workflows`（仅懒加载兜底）；`web` 残留
`import agent.cli.commands`（命令元数据登记副作用，归「统一命令接口」提案消化）。

## Alternatives considered

### 为什么不在 core 层复制一份 LLMError / parse_llm_json？
复制会形成两份事实来源，类型无法互通（`except LLMError` 抓到的是另一个类），长期必漂移。
下沉到 `base`（所有层的共同祖先）是唯一能同时消除 `client→core` 与
`core/base→agent.utils` 两个反向依赖的方案。

### 为什么 D-I 用「工具契约 + 上层注册」而不是把 export_chapters 移进 core？
`export_chapters` 需编排 workflow（导出依赖写章流水线产物），放进 `core/` 会制造
`core → workflows` 反向依赖（历史教训：`base/retry.py` 依赖 `event_sourcing/` 被判违规）。
因此契约留在 core、实现留在 workflows，经 `@tool` 装饰器注册。

### 为什么 D-J 用「构造注入 + 懒加载兜底」而不是彻底删除 workflows 引用？
`EvaluatorAgent` 的调用方（standalone 命令与 pipeline）均位于 workflows 层之上，
构造注入后上层的 `M10RollbackWorkflow` 经参数传入即可。懒加载兜底保留是为了
「降级不阻断」：万一调用方未注入，回滚能力退化为失败报告而非崩溃。

### 为什么不把 web → cli 的命令注册副作用一并移除？
`web/app.py` 的 `import agent.cli.commands` 依赖的是「命令元数据登记」副作用，
其根治需把命令契约迁出 cli（即「统一命令接口」方案）。该方案已有独立提案记录
（`.agents/notes/unified-command-architecture.md`），不在本次下沉范围内，避免两个大改叠在一次变更里。

## Consequences

- **收益**：分层依赖方向重新自洽；`client` 与 `core/base` 不再依赖上层或第三方渲染库；
  底层类型（LLMError 等）与解析工具三端（CLI/Web/未来 MCP）共用同一事实来源。
- **代价**：涉及多文件 import 迁移（client / core / agents / web 及对应测试的
  monkeypatch 目标从 `agent.utils` 改为 `agent.base.utils`）；`core/base/structured_output.py`
  与 `agent/utils.py` 保留为向后兼容薄再导出，需在后续清理。
- **遗留**：`web/app.py` 的 `import agent.cli.commands` 副作用耦合仍存在，由
  「统一命令接口」提案后续消化。
- **验证**：全量测试通过、无回归（含 test_safe_remove / structured_output / g4 / g9 系列）。
