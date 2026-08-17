"""命令子模块集合：import 即触发 @command 注册（副作用，自动发现）

T-1 起改为 glob 自动扫描 ``*.py`` 并导入，触发各模块的 ``@command`` 装饰器
完成「typer 注册 + COMMAND_REGISTRY 元数据登记」单一注册点。新增命令只需在
本目录新增一个文件并在其中使用 ``@command(...)``，无需在此处手动登记。

注：注册顺序由 ``command_router.COMMAND_REGISTRY`` 基线顺序保证（装饰器对已
存在的命令名跳过重复登记），故此处使用排序后的 glob 即可，不会破坏既有顺序依赖。
"""

from __future__ import annotations

import importlib
from pathlib import Path

_HERE = Path(__file__).parent

for _file in sorted(_HERE.glob("*.py")):
    if _file.name == "__init__.py":
        continue
    importlib.import_module(f"agent.cli.commands.{_file.stem}")
