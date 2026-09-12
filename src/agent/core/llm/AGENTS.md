# AGENTS.md - core/llm/ 预算计划

## 职责

仅提供 LLM 成本预算计划配置加载。

## 核心模块

| 文件 | 导出 | 作用 |
|------|------|------|
| `budget_plan.py` | `load_budget_plan` | 预算计划（成本预算配置加载） |

## 历史说明（2026-09-12 收口）

- embedding 实现与路由在 `agent/client/`（`embeddings.py` / `embedding_router.py`）
- 本包旧兼容再导出层已拆除（零调用方），勿再恢复旧导入路径
- LLM 客户端唯一出口仍是 `agent/client/gateway_adapter.py`
