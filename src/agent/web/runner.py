"""SSE 命令运行器（Web UI 实时性核心）。

设计要点（遵循项目「降级不阻断」哲学与 G9 进度流复用）：
- 以子进程方式驱动 ``python -m agent.cli <command> --dir <项目> <args>``，
  完全复用既有 CLI 逻辑，零侵入、不触碰 1112 个既有测试。
- stdin=DEVNULL：任何遗漏的交互式命令会在 EOF 处快速失败，绝不卡死。
- stdout/stderr 合并流式读取，剥离 rich 标记后作为 ``log`` 事件推送。
- 轮询项目 ``.state/progress.json``（G9 已落地），按事件 seq 增量推送 ``progress``
  事件，复用既有进度流，无需额外埋点。
- 进程结束后推送 ``done`` 事件（含退出码 + 看板摘要 + 最新状态），供前端收尾。

事件经 asyncio.Queue 在 execute 协程与 SSE 流之间传递；execute 由 API 端点
``asyncio.create_task`` 调度，SSE 流通过 ``run_manager.stream`` 消费。
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shlex
import sys
import uuid
from pathlib import Path
from typing import Any

from agent.web.state import project_path

# 仅剥离常见 rich 样式标记，尽量不误伤正文里的普通方括号
_RICH_TAG_RE = re.compile(
    r"\[(/?)(?:bold|dim|red|green|cyan|yellow|blue|magenta|white|black|"
    r"italic|underline|reverse|strike|on_[a-z]+)(?:=[^\]]*)?\]",
    re.IGNORECASE,
)


def strip_rich(text: str) -> str:
    """去掉 rich 控制台标记（如 [bold green]...[/]）。"""
    return _RICH_TAG_RE.sub("", text)


class RunManager:
    """管理进行中 / 已完成的命令运行实例。"""

    def __init__(self) -> None:
        self.runs: dict[str, dict[str, Any]] = {}

    def new_run(self, name: str, command: str, argv: list[str]) -> str:
        """登记一次新运行，返回 run_id。argv 为已切分好的参数列表。"""
        run_id = uuid.uuid4().hex[:12]
        self.runs[run_id] = {
            "id": run_id,
            "project": name,
            "command": command,
            "argv": argv,
            "queue": asyncio.Queue(),
            "logs": [],
            "done": False,
            "exit_code": None,
            "proc": None,
        }
        return run_id

    async def execute(self, run_id: str) -> None:
        """执行指定 run 的子进程，并把事件推入其队列。"""
        run = self.runs.get(run_id)
        if run is None:
            return
        pdir = project_path(run["project"])
        progress_file = pdir / ".state" / "progress.json"

        cmd: list[str] = [
            sys.executable,
            "-m",
            "agent.cli",
            run["command"],
        ]
        if run["command"] not in NO_DIR_COMMANDS:
            cmd += ["--dir", str(pdir)]
        cmd += run["argv"]
        env = dict(os.environ)
        env["PYTHONUNBUFFERED"] = "1"  # 保证 stdout 逐行实时流出

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=str(project_path(run["project"]).parent.parent),
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                stdin=asyncio.subprocess.DEVNULL,
            )
        except Exception as e:  # noqa: BLE001 - 启动失败也走 done 事件
            await run["queue"].put(
                {"type": "log", "data": {"text": f"✗ 启动失败：{e}"}}
            )
            await self._finish(run, exit_code=-1)
            return

        run["proc"] = proc
        seen_seq = -1
        last_mtime = 0.0

        async def pump_stdout() -> None:
            assert proc.stdout is not None
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break
                text = strip_rich(line.decode("utf-8", "replace")).rstrip("\r\n")
                # 累计日志：SSE 掉线时轮询兜底也能回看
                run["logs"].append(text)
                if len(run["logs"]) > 300:
                    run["logs"] = run["logs"][-300:]
                await run["queue"].put({"type": "log", "data": {"text": text}})

        async def tail_progress() -> None:
            nonlocal seen_seq, last_mtime
            while True:
                finished = proc.returncode is not None
                try:
                    mtime = progress_file.stat().st_mtime
                    if mtime != last_mtime:
                        last_mtime = mtime
                        try:
                            data = json.loads(
                                progress_file.read_text(encoding="utf-8")
                            )
                        except Exception:
                            data = None
                        if data:
                            for ev in data.get("events", []):
                                seq = ev.get("seq", 0)
                                if seq > seen_seq:
                                    seen_seq = seq
                                    await run["queue"].put(
                                        {"type": "progress", "data": ev}
                                    )
                except FileNotFoundError:
                    pass
                if finished:
                    break
                await asyncio.sleep(0.4)

        t_out = asyncio.create_task(pump_stdout())
        t_prog = asyncio.create_task(tail_progress())
        await proc.wait()
        await t_out
        await t_prog

        await self._finish(run, exit_code=proc.returncode or 0)

    async def _finish(self, run: dict[str, Any], exit_code: int) -> None:
        run["exit_code"] = exit_code
        run["done"] = True
        # 收尾：附上看板摘要 + 最新状态（供前端刷新）
        try:
            from agent.web.state import get_project_state, get_summary

            summary = get_summary(run["project"])
            state_val = get_project_state(run["project"]).get("state")
        except Exception:  # noqa: BLE001
            summary = None
            state_val = None
        await run["queue"].put(
            {
                "type": "done",
                "data": {
                    "exit_code": exit_code,
                    "summary": summary,
                    "state": state_val,
                },
            }
        )

    async def stream(self, run_id: str):
        """生成 SSE 事件序列（dict 形式，由路由层序列化为 text/event-stream）。"""
        run = self.runs.get(run_id)
        if run is None:
            return
        while True:
            try:
                ev = await asyncio.wait_for(run["queue"].get(), timeout=2.0)
            except asyncio.TimeoutError:
                if run["done"] and run["queue"].empty():
                    break
                yield {"type": "ping", "data": {}}
                continue
            yield ev
            if ev["type"] == "done":
                break


# 全局单例（进程内）
run_manager = RunManager()


# 不接受 --dir 的全局工具命令（避免误传未知选项）
NO_DIR_COMMANDS = {
    "export-skill",
    "genre-info",
    "help",
    "list-genres",
    "load-skill",
    "version",
}


def sanitize_project_name(name: str) -> str:
    """把任意项目名规整为安全目录名。"""
    return "".join(c for c in name.strip() if c.isalnum() or c in "-_") or "my-novel"


def split_args(raw: str) -> list[str]:
    """把原始参数字符串安全切分为 argv（支持引号）。"""
    if not raw.strip():
        return []
    return shlex.split(raw)
