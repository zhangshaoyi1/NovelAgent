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
) -> int:
    """执行一次「开新书/续写 → 写至完本」的流程。

    Args:
        checkup: 完本后是否自动跑体检（evaluate + foreshadow-report）。

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
    auto_cmd = cli + [
        "autowrite",
        "-d", str(project_dir),
        "--mode", mode,
        "--brief", story_core,
        "--chapters", str(chapters),
    ]
    if env:
        auto_cmd += ["--env", env]
    rc = subprocess.run(auto_cmd, cwd=str(AGENT_ROOT)).returncode
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
        rc_eval = subprocess.run(eval_cmd, cwd=str(AGENT_ROOT)).returncode
        if rc_eval != 0:
            print("⚠ evaluate 体检异常（非致命），请稍后手动重跑："
                  f" python -m agent.cli evaluate -d {project_dir}")

        fs_cmd = cli + ["foreshadow-report", "-d", str(project_dir)]
        if env:
            fs_cmd += ["--env", env]
        rc_fs = subprocess.run(fs_cmd, cwd=str(AGENT_ROOT)).returncode
        if rc_fs != 0:
            print("⚠ foreshadow-report 异常（非致命），请稍后手动重跑："
                  f" python -m agent.cli foreshadow-report -d {project_dir}")

        # G14：全量段落去重扫描（完本关卡，检测跨章重复内容）
        try:
            from agent.core.quality.guardrails import Guardrails, save_fingerprints

            gr = Guardrails(check_junk=False, check_title=False, check_dup=True, check_meta_leak=True)
            db: dict[str, list] = {}
            dup_report: list[str] = []
            chapters_dir = project_dir / "chapters"
            if chapters_dir.exists():
                for f in sorted(chapters_dir.glob("ch*.md")):
                    try:
                        text = f.read_text(encoding="utf-8")
                    except Exception:  # noqa: BLE001
                        continue
                    ch_num = f.stem
                    hits = gr._check_dup(text)
                    if hits:
                        dup_report.append(f"### {ch_num}\n" + "\n".join(f"- {h}" for h in hits))
                    gr.register_fingerprints(ch_num, text)
                    db.update(gr.fingerprint_db)
                if dup_report:
                    report_path = project_dir / ".state" / "dup_scan_report.md"
                    report_path.parent.mkdir(parents=True, exist_ok=True)
                    report_path.write_text(
                        "# 完本全量段落去重扫描报告\n\n"
                        + "\n\n".join(dup_report)
                        + "\n",
                        encoding="utf-8",
                    )
                    print(f"⚠ 检测到跨章重复内容，报告见：{report_path}")
                else:
                    print("✅ 全量段落去重扫描：未检测到跨章重复内容。")
                # 同步刷新指纹库（决策③：全书指纹库持久化）
                save_fingerprints(db, project_dir / ".state" / "chapter_fingerprints.json")
        except Exception as e:  # noqa: BLE001 - 扫描异常非致命
            print(f"⚠ 全量段落去重扫描异常（非致命）：{e}")

        print("✅ 体检完成，报告见项目目录（evaluate / foreshadow_report.md / dup_scan_report.md）。")

    return 0
