"""Web UI 启动命令（懒加载 FastAPI / uvicorn，避免拖慢 CLI 启动）。"""

from __future__ import annotations

from agent.cli._app import app, console, typer, command


@command(global_=True, help="启动 Web UI（FastAPI + SSE 实时界面），浏览器访问 http://<host>:<port>")
def web(
    host: str = typer.Option("127.0.0.1", "--host", help="监听地址"),
    port: int = typer.Option(8000, "--port", "-p", help="监听端口"),
) -> None:
    """启动 Web UI 服务（FastAPI + SSE 实时界面）。"""
    import uvicorn

    console.print(
        f"[bold green]NovelAgent Web UI[/bold green] 启动中 → "
        f"http://{host}:{port}"
    )
    # 架构不变性 §3.7 / R4：cli/ 不得 import web/（依赖只能向下）。
    # 因此这里不 import agent.web.app，改由 uvicorn 在运行时按 import string 解析，
    # 反向依赖（web → cli 读命令元数据）保持不变，环被拆成单向。
    uvicorn.run("agent.web.app:app", host=host, port=port)
