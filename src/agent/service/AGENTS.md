# AGENTS.md - service/ Service 层

## 职责

对外暴露进程内自主写作服务接口，供 CLI / 未来 FastAPI 共用。

## 核心模块

| 文件 | 导出 | 作用 |
|------|------|------|
| `agent_service.py` | `AgentService` | 进程内自主写作服务接口 |

## 依赖规则

- `service/` 是入口层，依赖所有下层
- 对外只暴露 `AgentService`

## 设计说明

Phase 3 接口预留，后续可扩展为 FastAPI 或 gRPC 服务。