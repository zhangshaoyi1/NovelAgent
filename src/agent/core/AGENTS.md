# AGENTS.md - core/ 核心服务层

## 职责

提供小说创作系统的核心引擎和领域模型，采用高类聚低耦合的分层架构。

## 子包结构

| 子包 | 职责 | 依赖 |
|------|------|------|
| `base/` | 基础基础设施（异常/注册表/重试/结构化输出） | 标准库 |
| `engine/` | 核心引擎（状态机/Agent循环/命令路由/工作流编排/事件流） | base + client |
| `story/` | 故事领域模型（设定/伏笔/关系网/章节/高潮曲线/爽点剧本） | base + client |
| `quality/` | 质量保障层（护栏/一致性/评分/改写） | base + client + story |
| `llm/` | LLM 基础设施（预算计划/Embedding路由） | base + client |
| `llmops/` | LLMOps（追踪/成本/评测） | base + client |
| `registry/` | 扩展机制注册表（Skill/题材包） | base |
| `infra/` | 基础设施（上下文工程/仪表盘/诊断/Compose） | base + client + story |
| `event_sourcing/` | 事件溯源（事件总线/存储/恢复） | base + client |
| `rag/` | 检索增强生成（索引/检索/向量存储） | base + client |
| `anti_ai/` | AI 味检测与压制 | base + client + story |
| `continuity/` | 连续性账本（G15） | base |
| `supervisor/` | 长小说监督体系 | base + client + story |
| `auto_orchestrator/` | 一键自动编排 | base + client + story |
| `tools/` | Tool 实现层（内置工具/MCP桥接） | engine + llm |
| `failure/` | 统一失败处理（llmagent 再导出） | llmagent |

## 依赖规则

- `base → engine → story/quality/llm/registry/infra → 上层业务`
- 下层不依赖上层，禁止循环依赖
- `event_sourcing`/`rag`/`llmops`/`anti_ai`/`continuity`/`supervisor`/`auto_orchestrator` 通过延迟导入避免循环依赖