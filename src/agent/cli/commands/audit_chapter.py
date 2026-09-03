from __future__ import annotations

from agent.cli._app import app, console, typer, command
from agent.cli._shared import *

@command(global_=True)
def audit_chapter(
    project_dir: str = typer.Option(
        "projects/my-novel", "--dir", "-d", help="小说项目目录"
    ),
    chapter: int = typer.Option(
        ..., "--chapter", "-c", help="待审核章节号（1-based）"
    ),
    policy: str = typer.Option(
        "standard", "--policy", "-p", help="杀戮边界策略: lenient|standard|strict"
    ),
) -> None:
    """M12 内容审核 - 审核章节是否含违禁内容

    F12.2：涉黄/涉政/极端暴力拦截。修仙杀戮边界可配置（lenient/standard/strict）。
    存在高严重度违规时章节会被拦截（exit code 2）。

    使用示例：
      novel-agent audit-chapter -d projects/my-novel -c 5
      novel-agent audit-chapter -d projects/my-novel -c 5 --policy strict
    """
    from pathlib import Path

    import frontmatter

    from agent.client.gateway_adapter import create_gateway_adapter
    from agent.core.story.setting_manager import SettingManager
    from agent.workflows.m12_audit import ContentAuditor

    project_path = Path(project_dir)
    enforce_gate(str(project_path), "audit_chapter")
    chapter_file = project_path / "chapters" / f"ch{chapter:03d}.md"
    if not chapter_file.exists():
        console.print(f"[bold red]✗[/bold red] 章节文件不存在: {chapter_file.name}")
        raise typer.Exit(code=1)

    post = frontmatter.load(chapter_file)
    # 读取题材（兼容多题材：优先 genre_label，否则取首个题材 id）
    sm = SettingManager(project_path)
    world_data = sm.load_world()
    _meta = world_data.get("metadata", {}) or {}
    _genres = _meta.get("genres") or []
    genre = str(_meta.get("genre_label") or (_genres[0] if _genres else "xiuxian"))

    # 接线：LLM 调用事件 → <project>/.events/events.jsonl（复用公共接线，避免复制）
    from agent.core.event_sourcing.llm_wiring import wire_llm_event_hook

    wire_llm_event_hook(project_path)

    auditor = ContentAuditor(
        project_path, llm=create_gateway_adapter(), console=console, violence_policy=policy
    )
    try:
        result = auditor.audit_chapter(post.content, genre=genre, violence_policy=policy)
    except Exception as e:
        console.print(f"[bold red]✗ 审核失败[/bold red] {e}")
        raise typer.Exit(code=1) from e

    auditor.show_result(result)
    if result.needs_block:
        raise typer.Exit(code=2)
