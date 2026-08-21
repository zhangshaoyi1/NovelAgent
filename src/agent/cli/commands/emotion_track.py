"""emotion_track 命令 —— 全书情绪轨迹展示（G12 P0-2，拍板 6：独立命令，evaluate 零改动）。

读取 `.state/payoff_script.json`（目标张力曲线）+ 已写章节进度（state/chapters），
ASCII 渲染目标 vs 实际推进对比图；``--json`` 输出结构化轨迹。

用法：
    novel-agent emotion-track -d projects/my-novel          # ASCII 曲线
    novel-agent emotion-track -d projects/my-novel --json   # JSON 轨迹
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from agent.cli._app import app, command, console, typer
from agent.cli._shared import *  # noqa: F401,F403 - emit_result / make_quiet_console


def _cli_value(v: Any, default: Any) -> Any:
    """归一化 CLI 参数：经 typer 真实调用时值为标量；直接函数调用时还原 OptionInfo。"""
    if hasattr(v, "default"):
        return v.default
    return v


# 张力 → ASCII 字符（1=平 ▁ / 2=缓 ▂ / 3=中 ▃ / 4=高 ▅ / 5=顶 ▇）
_TENSION_GLYPH = {1: "▁", 2: "▂", 3: "▃", 4: "▅", 5: "▇"}


def build_track(project_dir: str | Path) -> dict[str, Any]:
    """构建情绪轨迹（目标 + 实际推进）。

    Returns:
        {"chapters": [{"chapter", "target_tension", "written", "emotion"}],
         "written": 已写章数, "total": 目标章数, "empty": bool}
    """
    from agent.core.payoff_script import load_payoff_script

    script = load_payoff_script(project_dir, enabled=True)
    chapters = script.get("chapters") or []
    if not chapters:
        return {"chapters": [], "written": 0, "total": 0, "empty": True}

    # 已写章数：state.progress.total_written → chapters/ 文件数
    written = 0
    try:
        from agent.core.state_machine import StateMachine

        sm = StateMachine(project_dir)
        sm.load()
        written = int((sm.progress or {}).get("total_written", 0) or 0)
    except Exception:  # noqa: BLE001
        pass
    if written <= 0:
        try:
            from agent.core.chapters import list_chapter_files

            written = len(list_chapter_files(project_dir))
        except Exception:  # noqa: BLE001
            written = 0

    items = []
    for c in chapters:
        ch = int(c.get("chapter", 0))
        items.append(
            {
                "chapter": ch,
                "target_tension": int(c.get("tension", 3) or 3),
                "written": ch <= written,
                "emotion": c.get("emotion", ""),
            }
        )
    return {
        "chapters": items,
        "written": written,
        "total": len(items),
        "empty": False,
    }


def render_ascii(track: dict[str, Any]) -> list[str]:
    """渲染 ASCII 张力曲线（目标 ● 实际已写 ○）。"""
    chapters = track.get("chapters") or []
    if not chapters:
        return ["（暂无情绪轨迹，请先运行 payoff-plan 生成爽点剧本）"]
    max_ch = track.get("total", len(chapters)) or 1
    lines = ["张力曲线（1=平 ▁ 2=缓 ▂ 3=中 ▃ 4=高 ▅ 5=顶 ▇）  目标=● 实际=○"]
    for level in (5, 4, 3, 2, 1):
        row = f"{level} "
        for c in chapters:
            t = int(c.get("target_tension", 3) or 3)
            if t == level:
                row += "●" if c.get("written") else "○"
            else:
                row += " " * len(_TENSION_GLYPH.get(t, "▃"))
        lines.append(row)
    # 章节轴
    width = max_ch
    axis = "  " + "".join(str(i % 10) for i in range(1, width + 1))
    lines.append(axis)
    # 情绪标签摘要（每 5 章）
    tags = []
    for c in chapters:
        if c.get("emotion"):
            tags.append(f"{c['chapter']}:{c['emotion']}")
    lines.append("情绪：" + " ".join(tags[:40]))
    return lines


@command(global_=True)
def emotion_track(
    project_dir: str = typer.Option(
        "projects/my-novel", "--dir", "-d", help="小说项目目录"
    ),
    json_output: bool = typer.Option(
        False, "--json", help="以 JSON 形式输出轨迹到 stdout"
    ),
    env_file: str = typer.Option(None, "--env", help="指定 .env 文件（透传）"),
) -> None:
    """情绪轨迹展示 - 全书张力曲线（目标 vs 实际推进）+ 情绪标签

    只读展示；目标曲线来自 `.state/payoff_script.json`（payoff-plan 生成）。
    """
    _env_file = _cli_value(env_file, None)
    if _env_file:
        os.environ["NOVEL_AGENT_DOTENV"] = _env_file

    _dir = _cli_value(project_dir, "projects/my-novel")
    _json = bool(_cli_value(json_output, False))

    from agent.cli._shared import enforce_gate

    enforce_gate(str(_dir), "emotion_track", json_mode=_json)

    track = build_track(_dir)

    if _json:
        emit_result({"success": True, **track}, json_mode=True)
        return

    workflow_console = make_quiet_console() if _json else console
    for line in render_ascii(track):
        workflow_console.print(line)
    if not track.get("empty"):
        workflow_console.print(
            f"[dim]已写 {track['written']}/{track['total']} 章；情绪标签见上（每章目标）。[/dim]"
        )
