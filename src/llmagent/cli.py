"""llmagent CLI 入口"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="LLM 自主编排系统 (llmagent)")
    sub = parser.add_subparsers(dest="command", help="子命令")

    # write-chapter 命令
    wc = sub.add_parser("write-chapter", help="写单章")
    wc.add_argument("--project", "-p", type=str, default=".", help="项目目录")
    wc.add_argument("--title", "-t", type=str, default="测试章节", help="章节标题")
    wc.add_argument("--outline", "-o", type=str, default="", help="章节大纲")
    wc.add_argument("--words", "-w", type=int, default=3000, help="目标字数")

    # list-providers 命令
    sub.add_parser("list-providers", help="列出已注册的 Provider")

    args = parser.parse_args()

    if args.command == "write-chapter":
        _run_write_chapter(args)
    elif args.command == "list-providers":
        _list_providers()
    else:
        parser.print_help()


def _run_write_chapter(args: argparse.Namespace) -> None:
    from llmagent.app import LLMApp
    from llmagent.kernel.task import TaskRun, TaskSpec, TaskKind
    from llmagent.tasks.write_chapter import WRITE_CHAPTER_SPEC, WriteChapterExecutor

    project_dir = Path(args.project)
    app = LLMApp(data_dir=project_dir / ".llmagent")

    try:
        # 注册写章 Task
        app.catalog.register(WRITE_CHAPTER_SPEC, WriteChapterExecutor)

        # 创建 TaskRun
        run = TaskRun(
            run_id=f"wc-{sys.argv[1] if len(sys.argv) > 1 else 'test'}",
            spec=WRITE_CHAPTER_SPEC,
            output={
                "chapter_title": args.title,
                "outline": args.outline,
                "previous_chapter": "",
                "word_target": args.words,
            },
        )

        # 执行
        executor = WriteChapterExecutor(app.gateway, app.artifact_store)
        import asyncio

        result = asyncio.run(executor.execute(run))

        if result.status.value == "SUCCEEDED":
            print(f"✅ 章节 [{result.output.get('title', '')}] 生成完成")
            print(f"   字数: {result.output.get('word_count', 0)}")
            print(f"   内容预览: {result.output.get('content', '')[:200]}...")
        else:
            print(f"❌ 生成失败: {result.error}")

    finally:
        app.close()


def _list_providers() -> None:
    from llmagent.app import LLMApp

    app = LLMApp()
    try:
        cards = app.gateway.registry.available()
        if not cards:
            print("没有已注册的 Provider")
        else:
            print("已注册的 Provider:")
            for card in cards:
                print(f"  - {card.provider}/{card.model} (窗口: {card.context_window})")
    finally:
        app.close()


if __name__ == "__main__":
    main()