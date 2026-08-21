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

    from agent.web.app import app as fastapi_app

    console.print(
        f"[bold green]NovelAgent Web UI[/bold green] 启动中 → "
        f"http://{host}:{port}"
    )
    uvicorn.run(fastapi_app, host=host, port=port)
