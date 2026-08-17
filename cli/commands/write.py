from __future__ import annotations

import os
from pathlib import Path

from agent.cli._app import app, console, typer, command
from agent.cli._shared import *

from agent.core.state_machine import State

@command(allowed_states=(State.CHARACTER_DESIGN, State.WRITING,))
def write(
    project_dir: str = typer.Option(
        "projects/my-novel", "--dir", "-d", help="小说项目目录"
    ),
    json_output: bool = typer.Option(
        False, "--json",
        help="以 JSON 形式输出结果到 stdout（工作流内部 rich 输出转 stderr）",
    ),
    env_file: str = typer.Option(
        None, "--env",
        help="指定 .env 文件（仅本次命令生效，透传给下游 LLMClient）",
    ),
    strict_review: bool = typer.Option(
        True, "--strict-review", "--no-strict-review",
        help="开启 D 多维 LLM 质量审查（爽点/OOC/连贯性/追读力），"
             "维度 blocking 会触发重写；默认开启（每章默认审查）。"
             "如需跳过审查可加 --no-strict-review",
    ),
) -> None:
    """M5 章节创作 - 生成下一章正文

    基于已确认架构 + 大纲 + 角色设计，按压力曲线生成下一章：
      1. 7 步上下文加载（world→subline→route→relations→characters→foreshadows→题材规则）
      2. LLM 生成章节正文
      3. LLM 质量校验（9 项通用层规则），未通过自动修订（≤2 次）
      4. 持久化 chapters/ch<NNN>.md + 更新进度指针

    状态转换：CHARACTER_DESIGN → WRITING（首次）/ WRITING → WRITING（后续）

    Args:
        project_dir: 小说项目目录
    """
    from agent.core.conflict_service import ConflictArbiter
    from agent.core.exceptions import PreValidationBlocked
    from agent.core.genre_pack import GenrePackRegistry
    from agent.workflows.m5_write_chapter import (
        M5WriteChapterWorkflow,
    )

    # D：--env 透传（命令级设置环境变量，下游所有 LLMClient() 自动读取）
    if env_file:
        os.environ["NOVEL_AGENT_DOTENV"] = env_file

    project_path = Path(project_dir)
    if not (project_path / "world.md").exists():
        if json_output:
            emit_result(
                {
                    "success": False,
                    "error": {
                        "code": "no_world",
                        "message": f"{project_path / 'world.md'} 不存在，请先运行 start",
                    },
                },
                json_mode=True,
            )
        else:
            console.print(
                f"[bold red]✗[/bold red] {project_path / 'world.md'} 不存在，请先运行 start"
            )
        raise typer.Exit(code=1)

    # 统一门禁：当前阶段是否允许 /write（来自 command_router / StateMachine）
    enforce_gate(str(project_path), "write", json_mode=json_output)

    # E3 前置式冲突检测门禁：注入冲突仲裁器（生成前拦截高严重度冲突）
    # --json 时把仲裁器与 M5 的 rich 输出导向 stderr，避免污染 stdout 的 JSON
    workflow_console = make_quiet_console() if json_output else console
    conflict_arbiter = ConflictArbiter(project_dir=project_path, console=workflow_console)
    # E2 题材动态注入：注入题材包注册表（运行时加载套路到 M5）
    genre_registry = GenrePackRegistry()
    workflow = M5WriteChapterWorkflow(
        project_dir=project_path,
        conflict_arbiter=conflict_arbiter,
        genre_registry=genre_registry,
        console=workflow_console,
        strict_review=strict_review,
    )
    try:
        result = workflow.run()
        if json_output:
            # subline / route_node 取自章节 frontmatter（_save_chapter 必写这两个字段）
            import frontmatter

            post = frontmatter.load(result.chapter_file)
            emit_result(
                {
                    "success": True,
                    "chapter": result.chapter_num,
                    "title": result.chapter_title,
                    "word_count": result.word_count,
                    "quality_passed": result.quality_passed,
                    "revision_attempts": result.revision_attempts,
                    "rag_context_len": result.rag_context_len,
                    "d_issues": result.d_issues,
                    "subline": post.metadata.get("subline", ""),
                    "route_node": post.metadata.get("route_node", ""),
                },
                json_mode=True,
            )
            return
        status = "通过" if result.quality_passed else "未完全通过"
        console.print(
            f"\n[bold green]✓ M5 完成[/bold green] "
            f"第 {result.chapter_num} 章 · {result.word_count} 字 · "
            f"质量{status} · 修订 {result.revision_attempts} 次"
        )
        # E4 证据链小结
        chain = result.evidence_chain
        if chain.total() > 0:
            console.print(
                f"[dim]证据链：角色 {len(chain.characters)} · "
                f"伏笔 {len(chain.foreshadows)} · 设定 {len(chain.settings)} "
                f"（缺失源 {len(chain.missing_sources)}）[/dim]"
            )
    except PreValidationBlocked as blocked:
        # E3：高严重度冲突，生成被中断，需用户仲裁
        if json_output:
            emit_result(
                {
                    "success": False,
                    "error": {
                        "code": "pre_validation_blocked",
                        "message": blocked.report.summary,
                    },
                },
                json_mode=True,
            )
        else:
            console.print(
                f"\n[bold red]⚠ 前置冲突检测未通过，已暂停生成[/bold red]"
            )
            conflict_arbiter.show_report(blocked.report)
            console.print(
                "\n[yellow]请先解决上述高严重度冲突（修改 world.md / "
                "subline / 角色档案，或使用 /adjust-route 调整路线），"
                "再重新运行 write。[/yellow]"
            )
        raise typer.Exit(code=2) from blocked
    except Exception as e:
        if json_output:
            emit_result(
                {
                    "success": False,
                    "error": {"code": "write_failed", "message": str(e)},
                },
                json_mode=True,
            )
        else:
            console.print(f"\n[bold red]✗ M5 失败[/bold red] {e}")
        raise typer.Exit(code=1) from e
