# AGENTS.md - cli/commands/ 命令子模块

## 职责

每个文件对应一个 CLI 命令，使用 `@command` 装饰器自动注册。

## 自动发现

- `__init__.py` 使用 glob 自动扫描所有 `*.py` 文件并导入
- 导入触发 `@command` 装饰器完成 typer 注册 + COMMAND_REGISTRY 元数据登记
- 新增命令只需在此目录新建文件并使用 `@command(...)`，无需手动登记

## 注册顺序

由 `command_router.COMMAND_REGISTRY` 基线顺序保证，装饰器对已存在的命令名跳过重复登记。

## 命令文件

参见 `../AGENTS.md` 命令列表。