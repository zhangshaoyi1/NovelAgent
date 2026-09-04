# AGENTS.md - cli/ CLI 命令系统

## 职责

novel-agent CLI 包，提供所有命令行交互入口。

## 核心模块

| 文件/目录 | 作用 |
|-----------|------|
| `_app.py` | CLI 应用主入口（typer app） |
| `commands/` | 命令子模块集合（@command 自动注册） |

## 命令列表

| 命令 | 作用 |
|------|------|
| `version` | 版本信息 |
| `start` | 启动项目 |
| `discuss` | 讨论 |
| `architecture` | 架构 |
| `confirm_architecture` | 确认架构 |
| `outline` | 大纲 |
| `design_characters` | 角色设计 |
| `write` | 写章 |
| `adjust_route` | 调整路线 |
| `adjust_relation` | 调整关系 |
| `mode` | 模式切换 |
| `foreshadow_report` | 伏笔报告 |
| `foreshadow_check` | 伏笔检查 |
| `snapshot` | 快照 |
| `list_snapshots` | 列出快照 |
| `rollback_setting` | 回滚设定 |
| `frozen_fields` | 冻结字段 |
| `unfreeze` | 解冻 |
| `status` | 状态 |
| `load_skill` | 加载 Skill |
| `bookworm_review` | 书虫评测 |
| `help_` | 帮助 |
| `reset_state` | 重置状态 |
| `draft_status` | 草稿状态 |
| `draft_discard` | 丢弃草稿 |
| `rollback` | 回滚 |
| `resume` | 续写 |
| `export` | 导出 |
| `import_draft` | 导入草稿 |
| `completion_extras` | 完成附加 |
| `audit_setting` | 审计设定 |
| `audit_chapter` | 审计章节 |
| `summarize_chapter` | 章节摘要 |
| `summarize_range` | 范围摘要 |
| `context` | 上下文 |
| `list_genres` | 题材列表 |
| `genre_info` | 题材信息 |
| `load_genre` | 加载题材 |
| `inject_genre` | 注入题材 |

## 自动发现机制

- `commands/__init__.py` 使用 glob 自动扫描 `*.py` 并导入
- 每个命令文件使用 `@command(name=..., allowed_states=(...))` 装饰器自动注册
- 新增命令只需在 `commands/` 新建文件并使用 `@command`，无需手动登记

## 对外暴露

- `app`（供 `python -m agent.cli` 与测试使用）
- 各命令函数通过 `from agent.cli import <command>` 向后兼容访问