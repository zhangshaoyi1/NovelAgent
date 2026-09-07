"""一键自动写书编排逻辑（共享模块）。

被 CLI 子命令 ``compose`` 与独立脚本 ``scripts/compose.py`` 共用，避免重复实现。

流程：
1. 解析项目目录（--dir 优先，--name 落到 novels/<书名>）
2. 直接驱动 ``autowrite`` 走多角色流水线，缺 world.md 时 autowrite 自主规划生成

数据默认落在 ``NOVEL_DATA_ROOT``（默认 ``<仓库>/../novels``），agent 仓库保持纯代码。
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

# compose_runner.py 位于 <repo>/src/agent/core/，向上 3 级即仓库根（含 src/ 与 scripts/）
AGENT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_NOVEL_ROOT = Path(
    os.environ.get("NOVEL_DATA_ROOT", str(AGENT_ROOT.parent / "novels"))
)


def resolve_project_dir(name: str = "", directory: str = "") -> Path:
    """解析目标项目目录。

    - 给了 --dir：直接用（续写）
    - 给了 --name：落到 NOVEL_DATA_ROOT/<书名>
    """
    if directory:
        return Path(directory).resolve()
    if name:
        return Path(DEFAULT_NOVEL_ROOT) / name
    raise ValueError("必须提供 directory（续写）或 name（新书）")


def run_compose(
    name: str = "",
    directory: str = "",
    scope: str = "long",
    genre: str = "",
    story_core: str = "",
    chapters: int = 0,
    mode: str = "auto",
    env: str = "",
    checkup: bool = True,
    fullbook_scan: Callable[[Path], None] | None = None,
) -> int:
    """执行一次「开新书/续写 → 写至完本」的流程。

    Args:
        checkup: 完本后是否自动跑体检（evaluate + foreshadow-report）。
        fullbook_scan: G14 全量去重扫描回调（由调用方注入
            ``quality.guardrails.fullbook_dup_scan``，避免 infra 反向依赖 quality）。

    Returns:
        子进程退出码（0 表示成功）。

    注：通过 subprocess 复用 NovelAgent CLI，行为与原生 ``autowrite`` 一致；
    全程不经过定时任务，跑完即止。start/autowrite 均只写本地文件，不触碰 git。
    """
    project_dir = resolve_project_dir(name, directory)
    project_dir.mkdir(parents=True, exist_ok=True)

    py = sys.executable
    cli = [py, "-m", "agent.cli"]

    # 缺 world.md 时不再手动 start，由 autowrite 自主规划生成（autowrite 已支持）
    if not (project_dir / "world.md").exists():
        if not name:
            print(
                "✗ 目标目录无 world.md，且未提供 --name。\n"
                "  请先提供 --name 开新书（autowrite 将自主规划生成约束文档），\n"
                "  或先用 --dir 指定已有项目目录续写。"
            )
            return 2
        print("[compose] 新书模式，autowrite 将自主规划生成设定集/架构/大纲/角色...")

    print("[compose] 启动多角色自主写作...")
    # 锁继承：compose 进程已被派发层加了 writer.lock，spawn 的子进程若不声明
    # 血缘会被父锁挡死（父子 PID 不同）。注入 NOVEL_AGENT_INHERIT_LOCK_PID
    # 让子进程共享父锁；外部进程（CLI/Web 另起的写命令）不受影响、照常被拒。
    from agent.core.project_lock import INHERIT_ENV

    child_env = {**os.environ, INHERIT_ENV: str(os.getpid())}
    # 无窗口：compose 经 daemon 队列执行时父链无控制台，控制台型子进程
    # （python.exe）会被系统分配可见窗口（Web 自动写作弹 python 黑窗），
    # 显式压制。前台 CLI 直跑时该标志同样无害。
    run_kwargs: dict = {}
    if os.name == "nt":
        run_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    auto_cmd = cli + [
        "autowrite",
        "-d", str(project_dir),
        "--mode", mode,
        "--brief", story_core,
        "--chapters", str(chapters),
    ]
    if env:
        auto_cmd += ["--env", env]
    rc = subprocess.run(auto_cmd, cwd=str(AGENT_ROOT), env=child_env, **run_kwargs).returncode
    if rc != 0:
        print("✗ autowrite 未成功完成（可能熔断/阻塞），详见上方输出")
        print("  可复用同一命令加 --dir 接力续写。")
        return rc

    total = (
        len(list((project_dir / "chapters").glob("ch*.md")))
        if (project_dir / "chapters").exists()
        else 0
    )
    print(f"\n✅ 写作完成。项目目录: {project_dir} · 章节总数: {total}")

    # 第三步（可选）：完本自动体检
    if checkup:
        print("\n[compose] 自动体检：evaluate + foreshadow-report ...")
        eval_cmd = cli + ["evaluate", "-d", str(project_dir), "--no-rollback"]
        if env:
            eval_cmd += ["--env", env]
        rc_eval = subprocess.run(eval_cmd, cwd=str(AGENT_ROOT), env=child_env, **run_kwargs).returncode
        if rc_eval != 0:
            print("⚠ evaluate 体检异常（非致命），请稍后手动重跑："
                  f" python -m agent.cli evaluate -d {project_dir}")

        fs_cmd = cli + ["foreshadow-report", "-d", str(project_dir)]
        if env:
            fs_cmd += ["--env", env]
        rc_fs = subprocess.run(fs_cmd, cwd=str(AGENT_ROOT), env=child_env, **run_kwargs).returncode
        if rc_fs != 0:
            print("⚠ foreshadow-report 异常（非致命），请稍后手动重跑："
                  f" python -m agent.cli foreshadow-report -d {project_dir}")

        # G14：全量段落去重扫描（完本关卡，检测跨章重复内容）
        # 扫描实现归属 quality/guardrails，由调用方注入回调，infra 不依赖 quality
        if fullbook_scan is not None:
            try:
                fullbook_scan(project_dir)
            except Exception as e:  # noqa: BLE001 - 扫描异常非致命
                print(f"⚠ 全量段落去重扫描异常（非致命）：{e}")  # noqa: SILENT_DEGRADE

        print("✅ 体检完成，报告见项目目录（evaluate / foreshadow_report.md / dup_scan_report.md）。")

    return 0
