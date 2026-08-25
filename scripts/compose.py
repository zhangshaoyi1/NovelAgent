#!/usr/bin/env python3
"""compose.py — 一键自动写书入口（CLI 包装）。

复用 ``agent.core.compose_runner.run_compose``，保持单一事实来源。
不依赖 WorkBuddy。一条命令：生成约束文档 → 多角色（Planner/Writer/Editor/Evaluator）
推进 → 直至完本。

用法：
  # 新书：给标题/体量/题材/核心，自动生成约束文档并开始写
  python scripts/compose.py --name "我在长安开殡仪馆那些年" \
      --scope long --genre "xuanhuan" \
      --story-core "在长安开殡仪馆的年轻人卷入皇室长生阴谋" \
      --chapters 120

  # 续写已有项目（已有 world.md，直接进入多角色推进）
  python scripts/compose.py --dir D:/project/NovelAgent/novels/changan-binyiguan

特点：
- 单命令触发，跑完即止（不会像定时任务那样反复调度）。
- 数据默认落在 agent 源码之外的独立目录（NOVEL_DATA_ROOT，默认 ../novels），保持 agent 仓库纯代码。
- 全程通过 subprocess 复用 NovelAgent CLI，行为与原生 ``autowrite`` 一致。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from agent.core.compose_runner import DEFAULT_NOVEL_ROOT, resolve_project_dir, run_compose

CONSTRAINT_DOCS = [
    "world.md",
    "architecture.md",
    "outline.md",
    "foreshadows.md",
    "relations/graph.md",
    "method.md",
    "style.md",
    ".state/payoff_script.json",
]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="一键自动写书：生成约束文档 → 多角色推进 → 完本"
    )
    parser.add_argument("--name", default="", help="新书名（非空则开新书）")
    parser.add_argument(
        "--dir",
        default="",
        help="已有项目目录（续写）。与 --name 二选一；都不给则交互要求",
    )
    parser.add_argument("--scope", default="long", help="体量: short|medium|long")
    parser.add_argument("--genre", default="", help="题材，如 xuanhuan/wuxia/xiuxian")
    parser.add_argument("--story-core", default="", help="一句话故事核心")
    parser.add_argument("--chapters", type=int, default= 0, help="目标章节数(0=默认)")
    parser.add_argument("--mode", default="auto", help="写章引擎: auto|heavy|light")
    parser.add_argument(
        "--env", default="", help="指定 .env（否则用 agent 默认 .env）"
    )
    parser.add_argument(
        "--no-checkup", action="store_true",
        help="写完后不做自动体检（evaluate + foreshadow-report）",
    )
    args = parser.parse_args()

    rc = run_compose(
        name=args.name,
        directory=args.dir,
        scope=args.scope,
        genre=args.genre,
        story_core=args.story_core,
        chapters=args.chapters,
        mode=args.mode,
        env=args.env,
        checkup=not args.no_checkup,
    )
    if rc != 0:
        return rc

    # 汇报约束文档（仅新增：run_compose 已打印章节总数）
    try:
        project_dir = resolve_project_dir(args.name, args.dir)
        found = [rel for rel in CONSTRAINT_DOCS if (project_dir / rel).exists()]
        print(f"  约束文档: {', '.join(found) if found else '（无，请检查）'}")
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
