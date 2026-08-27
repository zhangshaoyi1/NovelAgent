from __future__ import annotations

from agent.cli._app import app, console, typer, command
from agent.cli._shared import *

from agent.core.state_machine import State

@command(allowed_states=(State.WRITING,))
def bookworm_review(
    book_name: str = typer.Option(..., "--book", "-b", help="小说名称"),
    title: str = typer.Option(..., "--title", "-t", help="章节标题或副标题"),
    opening_text: str = typer.Option(
        "", "--text", "-x",
        help="开头正文。留空则从 --file 读取",
    ),
    text_file: str = typer.Option(
        "", "--file", "-f",
        help="开头正文文件路径（UTF-8），与 --text 二选一",
    ),
    genre: str = typer.Option(
        "", "--genre", "-g", help="题材（如 xiuxian），可选",
    ),
    version: str = typer.Option(
        "1", "--version", "-v", help="版本标签（用于多次测评对比）",
    ),
    compare_with: str = typer.Option(
        "", "--compare", "-c",
        help="与已保存的某版本对比（如 1），需同时指定 --save-dir",
    ),
    save_dir: str = typer.Option(
        "", "--save-dir", "-s",
        help="保存测评结果的目录（含 JSON + MD）",
    ),
    project_dir: str = typer.Option(
        "projects/my-novel", "--dir", "-d", help="小说项目目录（用于默认 save_dir）",
    ),
) -> None:
    """M15 书虫测评 - 评估小说标题/开头的吸引力

    评估 7 维度：标题吸引力/开篇钩子/节奏/人物辨识度/题材契合/同质化/章末钩子
    输出：总评分（0-100）+ 各维度分 + 问题清单 + 改进建议 + 参考对照

    使用示例：
      # 基础测评
      novel-agent bookworm-review -b "凡人修仙" -t "第一章 血色试炼" -x "正文..."

      # 从文件读取正文
      novel-agent bookworm-review -b "凡人修仙" -t "第一章" -f opening.txt -g xiuxian

      # 测评 v2 并与 v1 对比
      novel-agent bookworm-review -b "凡人修仙" -t "第一章" -x "改后正文" -v 2 -c 1 -s reviews/
    """
    from pathlib import Path

    # 统一门禁：当前阶段是否允许 /bookworm-review（来自 command_router / StateMachine）
    enforce_gate(str(Path(project_dir)), "bookworm_review")

    from agent.workflows.m15_bookworm import BookwormInput, BookwormSkill

    # 解析正文
    if opening_text:
        text = opening_text
    elif text_file:
        text = Path(text_file).read_text(encoding="utf-8")
    else:
        console.print("[bold red]✗[/bold red] 必须提供 --text 或 --file 之一")
        raise typer.Exit(code=1)

    # 保存目录：未指定则用 project_dir/reviews/
    save_path = Path(save_dir) if save_dir else Path(project_dir) / "reviews"

    registry = _get_registry()
    skill = registry.get_skill("bookworm")
    if skill is None:
        # 自动加载
        from agent.client import LLMClient

        skill = registry.load_builtin("bookworm", llm=LLMClient(), console=console)

    inp = BookwormInput(
        title=title,
        book_name=book_name,
        opening_text=text,
        genre=genre or None,
    )

    try:
        review = skill.review(inp, version=version, save_dir=save_path)
    except Exception as e:
        console.print(f"[bold red]✗ 测评失败[/bold red] {e}")
        raise typer.Exit(code=1) from e

    skill.show_review(review)
    console.print(
        f"\n[dim]已保存到 {save_path / f'bookworm_review_{version}.json'}[/dim]"
    )

    # 版本对比
    if compare_with:
        old_file = save_path / f"bookworm_review_{compare_with}.json"
        if not old_file.exists():
            console.print(
                f"[yellow]⚠ 对比版本 {compare_with} 不存在（{old_file}），跳过对比[/yellow]"
            )
        else:
            import json as _json

            old_data = _json.loads(old_file.read_text(encoding="utf-8"))
            from agent.workflows.m15_bookworm import BookwormIssue, BookwormReview

            old_review = BookwormReview(
                total_score=old_data.get("total_score", 0),
                dimensions=old_data.get("dimensions", {}),
                one_liner_feeling=old_data.get("one_liner_feeling", ""),
                issues=[
                    BookwormIssue(
                        severity=i.get("severity", "warn"),
                        description=i.get("description", ""),
                        location=i.get("location", ""),
                    )
                    for i in old_data.get("issues", [])
                ],
                suggestions=old_data.get("suggestions", []),
                reference=old_data.get("reference", ""),
                version=compare_with,
            )
            comp = skill.compare(old_review, review)
            console.print()
            skill.show_comparison(comp)
