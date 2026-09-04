# NovelAgent AGENTS.md - Standing Orders

> 本文件遵循 DeepSeek Harness 设计：**Context Router**，而非百科全书。
> 每个新 Coding Agent Session 首先阅读本文件，然后按指引读取进一步信息。

***

## 导航规则

### 理解架构（修改前必读）

任何涉及 `src/agent/` 包结构的修改：
→ **先读** [../项目文档/架构文档.md](../项目文档/架构文档.md)
→ **再读** [../项目文档/详细设计文档.md](../项目文档/详细设计文档.md)
→ **重点关注** "架构不变性"章节，确认你不违反依赖方向

### 包结构总览

```
agent/
├── src/agent/                    # 主业务代码（小说创作系统）
│   ├── base/                     # 基础抽象层（不依赖任何上层）
│   ├── client/                   # 统一 LLM 客户端层（只依赖 base）
│   ├── core/                     # 核心引擎层（依赖 base + client）
│   │   ├── base/                 # 基础基础设施（异常/注册表/重试/结构化输出）
│   │   ├── engine/               # 核心引擎（状态机/Agent循环/命令路由/工作流编排）
│   │   ├── story/                # 故事领域模型（设定/伏笔/章节/高潮曲线）
│   │   ├── quality/              # 质量保障层（护栏/一致性/评分/改写）
│   │   ├── llm/                  # LLM 基础设施（预算计划/Embedding路由）
│   │   ├── llmops/               # LLMOps（追踪/成本/评测）
│   │   ├── registry/             # 扩展机制注册表（Skill/题材包）
│   │   ├── infra/                # 基础设施（上下文工程/仪表盘/诊断/Compose）
│   │   ├── event_sourcing/       # 事件溯源（事件总线/存储/恢复）
│   │   ├── rag/                  # 检索增强生成（索引/检索/向量存储）
│   │   ├── anti_ai/              # AI 味检测与压制
│   │   ├── continuity/           # 连续性账本（G15）
│   │   ├── supervisor/           # 长小说监督体系
│   │   ├── auto_orchestrator/    # 一键自动编排
│   │   ├── tools/                # Tool 实现层（内置工具/ MCP 桥接）
│   │   └── failure/              # 统一失败处理
│   ├── agents/                   # 四个核心智能体（Planner/Writer/Editor/Evaluator）
│   ├── workflows/                # 工作流编排（@workflow 装饰器动态注册）
│   │   ├── planning/             # M1-M4：写作规划阶段（世界观/讨论/架构/大纲/角色）
│   │   ├── writing/              # M5-M6：章节写作阶段（AgenticWrite/写章/调整）
│   │   ├── evaluation/           # M10-M21：评测审计阶段
│   │   ├── pipeline/             # 流水线编排（全流程自主/主线/预算）
│   │   ├── market/               # M22-M23：市场分析
│   │   ├── m8_mode.py            # 模式切换（单独保留在根目录）
│   │   └── __init__.py           # 保持向后兼容的导入
│   ├── tasks/                    # 新式 TaskSpec + Executor 模式
│   ├── cli/                      # CLI 命令（@command 自动发现）
│   ├── service/                  # Service 层（AgentService 进程内服务接口）
│   ├── session/                  # 会话管理（原生 llmagent SessionManager 再导出）
│   ├── memory/                   # 统一记忆层（MemoryLayer + 三层记忆）
│   ├── skills/                   # Skill 插件目录
│   ├── prompts/                  # Prompt 配置文件
│   ├── templates/                # Jinja2 模板目录
│   ├── methods/                  # 写作方法目录
│   ├── state_schema/             # 状态 Schema 定义
│   └── web/                      # FastAPI Web UI
├── src/llmagent/                 # 编排内核（Gateway/Task/Catalog/Session/EventBus）
│   ├── gateway/                  # 模型调用网关（唯一LLM出口）
│   ├── kernel/                   # 核心运行时（Task/Session/Agent/Planner/Memory）
│   └── tasks/                    # 业务 Task 定义
├── tests/                        # 业务测试（pytest）
├── llmagent_tests/               # 编排内核测试
├── scripts/                      # 辅助脚本
├── projects/                     # 项目数据目录
└── tools/                        # 工具目录
```

### 依赖方向（严格单向）

`base → client → core → agents → workflows → tasks`

- **`base/`** 不依赖任何上层（client/core/agents/workflows）

- **`client/`** 只依赖 `base/`，不依赖 `core/` 或任何上层

- **`core/`** 依赖 `base/` + `client/`

- **`agents/`** 依赖 `base/` + `client/` + `core/`

- **`workflows/`** 依赖所有下层

- **`tasks/`** 与 `workflows/` 并列，均为入口层，依赖所有下层

### 修改 `base/` 层：

→ `base/` **不依赖任何上层**（client/core/agents/workflows）
→ `base/` 只提供基础抽象（Agent 基类、配置、消息、类型、LLM 协议层）
→ 打破这条约束会导致循环导入，必须立即回滚

### 修改 `client/` 层：

→ `client/` **只依赖** **`base/`**，不依赖 `core/` 或任何上层
→ `client/` 提供统一 LLM 客户端入口（内部使用原生 llmagent Gateway）
→ `client/gateway_adapter.py` 是唯一 LLM 出口，提供 `create_gateway()` / `chat_creative()` / `chat_utility()` / `chat_structured()`
→ 所有 LLM 调用必须走 `gateway_adapter` 的辅助函数，**禁止直接调用 `LLMClient`（已废弃）**

### 修改 `core/` 层：

→ `core/` 依赖 `base/` + `client/`
→ `core/` 是领域核心引擎（状态机、护栏、一致性、结构化输出等）
→ `core/` 下子包职责单一，禁止循环依赖

### 修改 `agents/` 层：

→ `agents/` 依赖 `base/` + `client/` + `core/`
→ 四个核心智能体：`planner.py` / `writer_agent.py` / `editor.py` / `evaluator.py`
→ 旧 `*_agent.py` 已删除，请勿再引用

### 修改 `workflows/` 层：

→ `workflows/` 依赖所有下层
→ 编排主写作流程：`m1_config` → ... → `m23_short`
→ 通过 `@workflow` 装饰器自动注册到 `WorkflowRegistry`

### 修改 `tasks/` 层：

→ `tasks/` 与 `workflows/` 并列，均为入口层
→ 使用 `TaskSpec` + `Executor` 模式注册到 `TaskRegistry`
→ 每个任务文件定义一个 TaskSpec 和对应 Executor

### 新增命令：

→ 在 `src/agent/cli/commands/` 新建文件
→ 用 `@command(name=..., allowed_states=(...))` 装饰器自动注册
→ 无需手动修改 CLI 装配代码

### 非平凡修改（超过 3 个文件 / 影响架构）：

→ **必须** 创建或更新 **Agent Note**（决策记录，模板见 `.agents/notes/TEMPLATE.md`）
→ 路径 `.agents/notes/{lifecycle}/{class}/yyyy-mm-dd-topic-title.md`
→ `lifecycle`：`proposed/`（实施前评审）· `implemented/`（已交付）· `rejected/`（否决）
→ `class`：`architecture` / `feature` / `bug-fix` / `simplification` / `process` / `testing`
→ 头部两行严格为 `# Agent Note: <title>` + `Status: <status>`
→ 正文骨架：`## Problem` → `## Decision`（implemented）/ `## Proposal`（proposed）
→ → `## Alternatives considered` → `## Consequences`（implemented）
→ **架构变更（base/client/core 包结构、依赖方向、跨包契约）必须补充 Agent Note**

### 测试验证：

→ 修改后统一运行验证：`python -m pytest tests/ -q --tb=short`
→ 全量 1200+ 测试应全部通过（零失败需验证）

### 提交前检查：

→ 所有测试通过
→ 不破坏架构不变性
→ 非平凡修改有决策记录
→ 不提交 `.env.accel`、`outputs/`、`.agents/notes/`、`_debug_*.py`

***

## 全局不变性（必须遵守）

1. **单向依赖原则**：依赖方向永远是 `base → client → core → agents → workflows`，绝不反向
2. **降级不阻断**：任何 LLM 不可用 / API 失败都应该优雅降级，不阻断写作流程
3. **向后兼容**：旧导入路径保留废弃警告，不要直接删除旧入口
4. **Agentic 设计**：任何失败都返回错误信息给用户，而非静默崩溃
5. **LLM 统一出口**：所有 LLM 调用必须通过 `agent.client.gateway_adapter` 的辅助函数（`chat_creative()` / `chat_utility()` / `chat_structured()`），**禁止直接使用旧 `LLMClient`**

***

## 快速索引

| 文件/目录                                     | 作用                                      |
| ----------------------------------------- | --------------------------------------- |
| `src/agent/base/`                         | 基础抽象层（Agent 基类/消息/类型/LLM协议）             |
| `src/agent/client/`                       | 统一 LLM 客户端（Gateway 原生，gateway_adapter 为唯一出口）                    |
| `src/agent/agents/`                       | Planner/Writer/Editor/Evaluator 四个核心智能体 |
| `src/agent/workflows/agentic_pipeline.py` | 自主写作主编排                                 |
| `src/agent/workflows/agentic_write.py`    | 唯一写章入口                                  |
| `src/agent/workflows/mainline.py`         | 主流程编排                                   |
| `src/agent/core/engine/`                  | 核心引擎（状态机/Agent循环/工作流编排）                 |
| `src/agent/core/story/`                   | 故事领域模型（设定/伏笔/章节/高潮曲线）                   |
| `src/agent/core/quality/`                 | 质量保障体系（护栏/一致性/评分/改写）                    |
| `src/agent/core/llmops/`                  | LLMOps（追踪/成本/评测）                        |
| `src/agent/core/continuity/`              | 连续性账本（G15）                              |
| `src/agent/core/anti_ai/`                 | AI 味检测与压制                               |
| `src/agent/core/event_sourcing/`          | 事件溯源（事件总线/存储/恢复）                        |
| `src/agent/core/supervisor/`              | 长小说监督体系                                 |
| `src/agent/core/auto_orchestrator/`       | 一键自动编排                                  |
| `src/agent/core/failure/`                 | 统一失败处理（原生 llmagent FailureHandler）      |
| `src/agent/core/tools/`                   | Tool 实现层（内置工具/ MCP 桥接）                  |
| `src/agent/tasks/`                        | 新式 TaskSpec + Executor 注册               |
| `src/agent/cli/`                          | CLI 命令系统（@command 自动发现）                 |
| `src/agent/service/`                      | Service 层（AgentService）                 |
| `src/agent/session/`                      | 会话管理（原生 llmagent SessionManager）        |
| `src/agent/memory/`                       | 统一记忆层（MemoryLayer）                      |
| `src/agent/web/`                          | FastAPI Web UI                          |
| `src/llmagent/`                           | 编排内核（Gateway/Task/Catalog/EventBus）     |
| `src/llmagent/gateway/`                   | 模型调用网关（唯一LLM出口）                         |
| `src/llmagent/kernel/`                    | 核心运行时（Task/Session/Agent/Planner）       |
| `tests/`                                  | 业务测试（1200+ 用例）                          |
| `llmagent_tests/`                         | 编排内核测试                                  |
| `../项目文档/架构文档.md`                         | 完整架构文档（含模块职责）                           |
| `../项目文档/详细设计文档.md`                       | 组件级详细设计（智能体/工作流/Skill/引擎核心类）            |
| `.agents/skills/debugging.md`             | 调试方法论                                   |
| `.agents/skills/refactoring.md`           | 重构检查清单                                  |
| `.agents/skills/code-review.md`           | 代码审查清单                                  |

<br />
