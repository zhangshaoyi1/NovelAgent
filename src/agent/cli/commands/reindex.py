from __future__ import annotations

import os
from pathlib import Path

from agent.cli._app import app, console, typer, command
from agent.cli._shared import emit_result


@command(global_=True)
def reindex(
    project_dir: str = typer.Option(
        "projects/my-novel", "--dir", "-d", help="小说项目目录"
    ),
    json_output: bool = typer.Option(
        False, "--json",
        help="以 JSON 形式输出重建统计到 stdout",
    ),
    env_file: str = typer.Option(
        None, "--env",
        help="指定 .env 文件（仅本次命令生效，透传给下游 GatewayAdapter）",
    ),
) -> None:
    """重建 RAG 语义索引（增量 A）

    遍历 world / architecture / outline / sublines / characters / relations /
    foreshadows / 已写章节，切片 → embedding → 写入 ``.state/rag/``。
    embedding 不可达时自动降级 BM25-only（统计 embedding_failed），不阻断。

    --json 输出字段：success / indexed_chunks / embedding_failed / chapters
    """
    if env_file:
        os.environ["NOVEL_AGENT_DOTENV"] = env_file

    from agent.core.rag.indexer import Indexer

    # 接线：LLM 调用事件（embed → api.call）→ <project>/.events/events.jsonl（复用公共接线，避免复制）
    from agent.core.event_sourcing.llm_wiring import wire_llm_event_hook

    wire_llm_event_hook(project_dir)

    try:
        stats = Indexer(Path(project_dir)).reindex()
    except Exception as e:  # noqa: BLE001
        if json_output:
            emit_result(
                {
                    "success": False,
                    "error": {
                        "code": "reindex_failed",
                        "message": f"RAG 索引重建失败：{e}",
                    },
                },
                json_mode=True,
            )
        else:
            console.print(f"[bold red]✗ RAG 索引重建失败[/bold red] {e}")
        raise typer.Exit(code=1) from e

    if json_output:
        emit_result(
            {
                "success": True,
                "indexed_chunks": stats["indexed_chunks"],
                "embedding_failed": stats["embedding_failed"],
                "chapters": stats["chapters"],
            },
            json_mode=True,
        )
        return

    console.print(
        f"[bold green]✓ RAG 索引重建完成[/bold green]："
        f"{stats['indexed_chunks']} 切片 / {stats['chapters']} 章"
    )
    if stats["embedding_failed"]:
        console.print(
            f"[yellow]⚠ {stats['embedding_failed']} 切片 embed 失败"
            f"（已降级 BM25-only 召回）[/yellow]"
        )
