# AGENTS.md - core/llm/ LLM 基础设施层

## 职责

提供 LLM 调用的上层抽象与辅助能力。

## 核心模块

| 文件 | 导出 | 作用 |
|------|------|------|
| `budget_plan.py` | `load_budget_plan` | 预算计划（成本预算配置加载） |

## 下沉说明（2026-08-29）

- embedding 实现与路由已下沉至 `agent/client/`（`embeddings.py` / `embedding_router.py`）
- 本包仅做再导出以保持 `core.llm` 公共 API 不变
- 核心 LLM 客户端实现位于 `agent/client/`