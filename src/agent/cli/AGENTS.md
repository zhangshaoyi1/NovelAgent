# AGENTS.md - cli/ CLI 命令系统

## 职责

novel-agent CLI 包，提供所有命令行交互入口。

## 核心模块

| 文件/目录 | 作用 |
|-----------|------|
| `_app.py` | CLI 应用主入口（typer app） |
| `commands/` | 命令子模块集合（@command 自动注册） |

## 命令列表

`commands/` 有 80+ 命令文件，通过 `@command` 自动发现，**不在此逐一列举**——完整清单看 `commands/` 目录或 `python -m agent.cli --help`。关键命令：

| 命令 | 作用 |
|------|------|
| `start` / `write` / `resume` / `rollback` | 主流程（启动/写章/续写/回滚） |
| `autowrite` | 自动批量写作（用 `--batch` 相对批次目标，勿传 `--chapters` 绝对目标） |
| `daemon` | 守护进程管理（抗异常不死；停止执行者是 daemon，Web 只写 stop_requested） |
| `unlock` | 清理写锁（冻结 state 直写解锁也在此；**勿手删 writer.lock**） |
| `doctor` | 诊断（含 RAG 索引陈旧度体检） |
| `export_skill` | 导出 Skill |
| `status` / `snapshot` / `list_snapshots` / `reset_state` | 状态与快照 |
| `mode` | 模式切换 |
| `web` | 启动 Web UI（仅注册命令元数据，禁止反向 import web/） |

其余：`discuss`/`architecture`/`outline`/`design_characters`/`foreshadow_*`/`audit_*`/`summarize_*`/`context`/`genre_*`/`rollback_setting`/`frozen_fields`/`unfreeze`/`draft_*`/`export`/`import_draft`/`completion_extras`/`load_skill`/`bookworm_review`/`dashboard`/`evaluate`/`repair`/`rewrite`/`deslop`/`cost`/`task`/`mainline`/`graph`/`roster` 等。

## 自动发现机制

- `commands/__init__.py` 使用 glob 自动扫描 `*.py` 并导入
- 每个命令文件使用 `@command(name=..., allowed_states=(...))` 装饰器自动注册
- 新增命令只需在 `commands/` 新建文件并使用 `@command`，无需手动登记

## 对外暴露

- `app`（供 `python -m agent.cli` 与测试使用）
- 各命令函数通过 `from agent.cli import <command>` 向后兼容访问