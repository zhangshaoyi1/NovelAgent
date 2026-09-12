# AGENTS.md - src/agent/ 主业务代码包

## 职责

小说创作 Agent 系统的主业务代码，采用分层架构。

## 包结构

| 子包 | 职责 | 依赖 |
|------|------|------|
| `base/` | 基础抽象层（Agent 基类/消息/类型/LLM协议） | 无 agent 依赖 |
| `client/` | 统一 LLM 客户端 | base |
| `core/` | 核心引擎层 | base + client |
| `agents/` | 四个核心智能体 | base + client + core |
| `workflows/` | 工作流编排 | 所有下层 |
| `tasks/` | TaskSpec + Executor 模式 | 所有下层 |
| `cli/` | CLI 命令系统 | 所有下层 |
| `service/` | Service 层 | 所有下层 |
| `session/` | 会话管理（llmagent 再导出） | llmagent |
| `memory/` | 统一记忆层 | llmagent |
| `skills/` | Skill 插件目录 | 独立 |
| `prompts/` | Prompt 配置 | 独立 |
| `templates/` | Jinja2 模板 | 独立 |
| `methods/` | 写作方法目录 | 独立 |
| `state_schema/` | 状态 Schema 定义 | 独立 |
| `daemon/` | 后台写作守护进程（任务队列/进程管理/跟随） | 所有下层 |
| `web/` | FastAPI Web UI | 所有下层（web→cli 仅命令注册副作用，cli 禁止 import web） |

## 依赖方向（严格单向）

`base → client → core → agents → workflows/tasks`

- 下层不依赖上层
- 禁止循环依赖（cli↔web 循环依赖已于 2026-09-12 拆除，方向为 web→cli 单向）
- `core/` 内部子包也遵循单向依赖
- `core/llm/` 已收口为预算计划（embedding 路由在 client/，兼容层已拆除）

## 导出

`__init__.py` 按分层顺序导出所有公共类型，延迟导入避免循环依赖。