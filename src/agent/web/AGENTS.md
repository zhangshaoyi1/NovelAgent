# AGENTS.md - web/ Web UI 包

## 职责

FastAPI + Jinja2 + SSE 的 Web 用户界面。

## 技术栈

- FastAPI: Web 框架
- Jinja2: 模板引擎
- SSE: 服务端事件推送

## 依赖规则

- 依赖所有下层服务（service/client/base 等）
- 对外提供 HTTP 接口