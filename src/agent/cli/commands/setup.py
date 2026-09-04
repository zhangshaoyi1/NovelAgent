"""setup 命令 —— M22 项目脚手架（写作基础设施部署）

对目标项目目录部署写作基础设施文件：CLAUDE.md、rules/*.md、agents/*.md、
上下文.md.tmpl 与 .story-deployed 哨兵文件。纯文件部署，无 LLM。

与 /start 的职责边界：
- /start 负责「项目初始化」——收集标题/体量/题材/风格、调用 LLM 生成世界观、
  渲染并保存 world.md、驱动状态机 INIT → CONFIGURING。
- /setup 只做「写作基础设施部署」——把 story-setup skill 的模板文件合并/复制
  到目标目录，不生成任何故事内容，不驱动状态机。两者不重叠。

用法：
    # 部署到默认目录 novels/my-novel
    novel-agent setup
    # 部署到指定目录并填写占位符
    novel-agent setup -d <project-dir> --book 书名 --platform 起点 --author 作者
    # 已部署也重新部署（覆盖 agents，合并 CLAUDE.md/rules）
    novel-agent setup -d <project-dir> --force
    # JSON 输出（脚本 / Web UI 调用）
    novel-agent setup -d <project-dir> --json
"""
from __future__ import annotations

from pathlib import Path

from agent.cli._app import app, command, console, typer
from agent.cli._shared import enforce_gate, emit_result
from agent.workflows.market.m22_setup import AGENTS_VERSION, SETUP_SKILL_VERSION


@command(global_=True)
def setup(
    project_dir: str = typer.Option(
        "novels/my-novel", "--dir", "-d", help="目标项目目录（写作基础设施部署目标）"
    ),
    book: str = typer.Option(
        "", "--book", help="书名（子目录名；存在 {书名}/ 目录时部署上下文模板）"
    ),
    platform: str = typer.Option(
        "起点", "--platform", help="目标平台（占位符替换，默认 起点）"
    ),
    author: str = typer.Option(
        "作者", "--author", help="作者名（占位符替换，默认 作者）"
    ),
    force: bool = typer.Option(
        False, "--force", help="已部署（存在 .story-deployed）也重新部署"
    ),
    json_output: bool = typer.Option(
        False, "--json", help="以 JSON 形式输出结果到 stdout"
    ),
) -> None:
    """项目脚手架 —— 部署写作基础设施（CLAUDE.md/rules/agents/上下文模板/哨兵）

    M22 纯文件部署工作流（无 LLM）：
    - CLAUDE.md 按 ## section 合并（已有 section 保留，模板新增 section 追加）
    - rules/*.md 合并（存在保留，缺失复制）
    - agents/*.md 可覆盖
    - 上下文.md.tmpl 有 {书名}/ 目录时复制到 {书名}/追踪/
    - .story-deployed 哨兵记录 deployed_at / agents_version / setup_skill_version
    - 检测到已部署时默认跳过并提示；--force 重新部署
    """
    from agent.workflows.market.m22_setup import M22SetupInput, M22SetupWorkflow

    project_path = Path(project_dir)
    # 部署前门禁：/setup 为全局命令，任意状态放行；项目未初始化（无 state.json）
    # 时也放行（纯文件部署不依赖状态机）。
    enforce_gate(str(project_path), "setup", json_mode=json_output)

    workflow = M22SetupWorkflow(project_dir=project_path)
    result = workflow.run(
        M22SetupInput(
            project_dir=project_path,
            book=book,
            platform=platform,
            author=author,
            force=force,
        )
    )

    if json_output:
        emit_result(result.to_dict(), json_mode=True)
        return

    if result.skipped_existing:
        console.print(
            f"[yellow]检测到已部署（{result.sentinel.name} 存在），本次跳过。[/yellow]"
        )
        console.print("[dim]如需重新部署请加 --force。[/dim]")
        return

    _render_report(result)


def _render_report(result) -> None:
    """rich 渲染部署报告（已部署文件清单 + 保留清单 + 注意事项）。"""
    from rich.panel import Panel
    from rich.table import Table

    title = "M22 项目脚手架 · 已重新部署" if result.redeployed else "M22 项目脚手架 · 部署完成"
    console.print(Panel(f"[bold green]✓ 写作基础设施部署完成[/bold green]", title=title))

    if result.deployed_files:
        table = Table(title="已部署文件")
        table.add_column("#", justify="right", style="dim")
        table.add_column("文件", style="cyan")
        for i, f in enumerate(result.deployed_files, 1):
            table.add_row(str(i), f)
        console.print(table)

    if result.preserved_files:
        console.print(
            "[yellow]保留既有文件（未覆盖）：[/yellow]"
            + "、".join(f"[dim]{f}[/dim]" for f in result.preserved_files)
        )

    if result.notes:
        console.print("\n[bold]注意事项[/bold]")
        for n in result.notes:
            console.print(f"  [dim]·[/dim] {n}")

    console.print(
        f"\n[dim]哨兵文件：{result.sentinel}（agents v{AGENTS_VERSION} · "
        f"setup skill v{SETUP_SKILL_VERSION}）[/dim]"
    )
