"""ecosystem 命令 —— MCP 生态 / 模型路由看板（Phase 4）

展示本项目的 MCP 服务器连接状态、动态模型路由表与本地内置工具清单。
纯只读看板，不修改书稿；用于发布前确认「外部工具接入」与「模型分工」是否就绪。
"""

from __future__ import annotations

import os
from pathlib import Path

from agent.cli._app import app, command, console, typer
from agent.cli._shared import *  # emit_result / make_quiet_console


@command(global_=True)
def ecosystem(
    project_dir: str = typer.Option(
        "projects/my-novel", "--dir", "-d", help="小说项目目录"
    ),
    json_output: bool = typer.Option(
        False, "--json", help="以 JSON 形式输出到 stdout"
    ),
    env_file: str = typer.Option(None, "--env", help="指定 .env 文件（透传）"),
) -> None:
    """生态看板 - MCP 服务器状态 / 模型路由表 / 本地工具清单

    读取本项目 ``.state/mcp.json``（若存在）探测外部 MCP 服务器可用性，列出动态模型
    路由候选与本地内置工具（经 Phase 0 MCP manifest 暴露）。
    """
    if env_file:
        os.environ["NOVEL_AGENT_DOTENV"] = env_file

    workflow_console = make_quiet_console() if json_output else console

    from agent.client import ModelRouter
    from agent.core.tools.mcp_bridge import MCPBridge

    proj = Path(project_dir)
    bridge = MCPBridge(config_path=proj / ".state" / "mcp.json")
    bridge.discover()
    router = ModelRouter()

    # 路由决策示例
    creative_decision = router.route("creative").to_dict()
    utility_decision = router.route("utility").to_dict()

    summary = {
        "mcp_servers": bridge.servers,
        "local_tools": bridge.local_manifest(),
        "remote_tools": bridge.remote_manifest(),
        "model_routing": router.report(),
        "route_example": {
            "creative": creative_decision,
            "utility": utility_decision,
        },
    }

    if json_output:
        emit_result({"success": True, "ecosystem": summary}, json_mode=True)
        return

    workflow_console.print("[bold cyan]生态看板（Phase 4）[/bold cyan]")

    workflow_console.print("\n[bold]MCP 服务器[/bold]")
    if bridge.servers:
        for name, st in bridge.servers.items():
            ok = st.get("status") == "available"
            tag = "[green]可用[/green]" if ok else "[yellow]不可用[/yellow]"
            workflow_console.print(f"  - {name}: {tag}（{st.get('reason','')}）")
    else:
        workflow_console.print("  （未配置外部 MCP 服务器；本地工具恒可用）")

    workflow_console.print(
        f"\n[bold]本地工具（{len(bridge.local_manifest())}）[/bold]："
        + ", ".join(t["name"] for t in bridge.local_manifest())
    )
    workflow_console.print(
        f"[bold]远程工具（{len(bridge.remote_manifest())}）[/bold]："
        + (", ".join(t["name"] for t in bridge.remote_manifest()) or "无")
    )

    workflow_console.print("\n[bold]模型路由表[/bold]")
    for c in router.report()["candidates"]:
        workflow_console.print(
            f"  - {c['name']}（{c['provider']}/{c['model_id']}，"
            f"用途={c['use']}，priority={c['priority']}，"
            f"${c['cost_per_1k']}/1k）"
        )
    workflow_console.print(
        f"  创作路由 → {creative_decision['model_id']}（{creative_decision['source']}）"
    )
    workflow_console.print(
        f"  校验路由 → {utility_decision['model_id']}（{utility_decision['source']}）"
    )
