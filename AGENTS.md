# NovelAgent AGENTS.md - Standing Orders

> 本文件遵循 DeepSeek Harness 设计：**Context Router**，而非百科全书。
> 每个新 Coding Agent Session 首先阅读本文件，然后按指引读取进一步信息。

***

## 导航规则

### 理解架构（修改前必读）

任何涉及 `src/agent/` 包结构的修改：
→ **先读** [../项目文档/架构文档.md](../项目文档/架构文档.md)
→ **再读** [../项目文档/开发指南.md](../项目文档/开发指南.md)
→ **重点关注** "架构不变性"章节，确认你不违反依赖方向

### 修改 `base/` 层：

→ `base/` **不依赖任何上层**（client/core/agents/workflows）
→ `base/` 只提供基础抽象（Agent 基类、配置、消息、类型）
→ 打破这条约束会导致循环导入，必须立即回滚

### 修改 `client/` 层：

→ `client/` **只依赖** **`base/`**，不依赖 `core/` 或任何上层
→ `client/` 提供统一 LLM 客户端入口
→ 所有 LLM 调用必须走 `from agent.client import LLMClient`

### 修改 `core/` 层：

→ `core/` 依赖 `base/` + `client/`
→ `core/` 是领域核心引擎（状态机、护栏、一致性、结构化输出等）

### 修改 `agents/` 层：

→ `agents/` 依赖 `base/` + `client/` + `core/`
→ 三个核心智能体：`planner.py` / `editor.py` / `evaluator.py`
→ 旧 `*_agent.py` 已删除，请勿再引用

### 修改 `workflows/` 层：

→ `workflows/` 依赖所有下层
→ 编排主写作流程：`m1_config` → ... → `m18_recovery`

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
→ 全量 1200 测试应全部通过（零失败需验证）

### 提交前检查：

→ 所有测试通过
→ 不破坏架构不变性
→ 非平凡修改有决策记录

***

## 全局不变性（必须遵守）

1. **单向依赖原则**：依赖方向永远是 `base → client → core → agents → workflows`，绝不反向
2. **降级不阻断**：任何 LLM 不可用 / API 失败都应该优雅降级，不阻断写作流程
3. **向后兼容**：旧导入路径保留废弃警告，不要直接删除旧入口
4. **Agentic 设计**：任何失败都返回错误信息给用户，而非静默崩溃

***

## 快速索引

| 文件                                        | 作用                               |
| ----------------------------------------- | -------------------------------- |
| `src/agent/base/__init__.py`              | 基础抽象导出                           |
| `src/agent/client/__init__.py`            | 统一 LLM 客户端导出                     |
| `src/agent/agents/`                       | Planner/Editor/Evaluator 三个核心智能体 |
| `src/agent/workflows/agentic_pipeline.py` | 自主写作主编排                          |
| `src/agent/core/llmops/`                  | LLMOps（追踪 / 成本 / 评测）             |
| `../项目文档/架构文档.md`                         | 完整架构文档（含模块职责）                    |
| `../项目文档/开发指南.md`                         | 开发者指南（环境搭建、扩展）                   |
| `.agents/skills/debugging.md`             | 调试方法论                            |
| `.agents/skills/refactoring.md`           | 重构检查清单                           |
| `.agents/skills/code-review.md`           | 代码审查清单                           |

