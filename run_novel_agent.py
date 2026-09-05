# PyInstaller 入口脚本：pyproject 的 console script 指向 Typer app 对象，
# 冻结构建需要一个可执行的脚本文件。
from agent.cli import app

if __name__ == "__main__":
    app()
