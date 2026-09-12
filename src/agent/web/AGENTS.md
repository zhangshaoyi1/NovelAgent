# AGENTS.md - web/ Web UI 包

## 职责

FastAPI + Jinja2 + SSE 的 Web 用户界面。

## 技术栈

- FastAPI: Web 框架
- Jinja2: 模板引擎
- SSE: 服务端事件推送

## 依赖规则

- 依赖所有下层服务（service/client/base 等）
- 对外提供 HTTP接口
- **依赖只能向下**：`web/app.py` 向 `agent.cli.commands` import 仅为命令注册副作用；**`cli/` 禁止 import `web/`**（R4 方向矩阵，cli↔web 循环依赖已于 2026-09-12 拆除，见 `tests/architecture/`）

## 运行机制

- 长任务通过 `daemon`（`agent.daemon.task_queue`）执行：`runner.py` 从 task_queue re-export `NO_DIR_COMMANDS`（单一真相源），经 daemon 的 process_manager 拉起命令进程
- 注意：已有 daemon 存活时，"重启 Web ≠ 换代码"——任务由既有 daemon 加载的代码执行