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

    def new_run(
        self,
        name: str,
        command: str,
        argv: list[str],
        env_extra: dict[str, str] | None = None,
    ) -> str:
        """登记一次新运行，返回 run_id。argv 为已切分好的参数列表。

        env_extra：注入子进程的额外环境变量（如 NOVEL_MODEL_PROFILE 指定
        本次运行使用的模型档案），不改变既有 CLI 逻辑。
        """
        run_id = uuid.uuid4().hex[:12]
        self.runs[run_id] = {
            "id": run_id,
            "project": name,
            "command": command,
            "argv": argv,
            "env_extra": dict(env_extra or {}),
            # 订阅者广播：每个 SSE 连接持有独立队列，事件全量投递。
            # 独享队列（而非共享队列）保证重连的新连接不会被残留的
            # 旧连接抢走事件，断线期间的日志由存量回放补齐。
            "subscribers": set(),
            "logs": [],
            # 进度事件缓存：供晚订阅者（如切走又切回的页面）回放时间线
            "progress_events": [],
            "done": False,
            "exit_code": None,
            "done_data": None,
            "proc": None,
        }
        return run_id

    def _emit(self, run: dict[str, Any], ev: dict[str, Any]) -> None:
        """向所有订阅者广播事件；同步执行，与快照之间不会发生事件丢失。

        队列积压超过上限视为死连接（页面已丢弃，未走到 finally 清理），
        停止投递防止无界增长；该订阅者下次 stream 调用时仍会被清理。
        """
        for q in list(run["subscribers"]):
            if q.qsize() < 2000:
                q.put_nowait(ev)

    async def execute(self, run_id: str) -> None:
        """执行指定 run 的子进程，并把事件推入其队列。"""
        run = self.runs.get(run_id)
        if run is None:
            return
        pdir = project_path(run["project"])
        progress_file = pdir / ".state" / "progress.json"

        # 单写者锁预检：同一小说已有活跃写进程时不再启动，直接给前端明确反馈
        if run["command"] in WRITER_COMMANDS:
            from agent.core.project_lock import probe_project_lock

            holder = probe_project_lock(pdir, run["command"])
            if holder:
                self._emit(
                    run,
                    {
                        "type": "log",
                        "data": {
                            "text": (
                                f"✗ 已有写任务在运行（pid={holder.get('pid')}，"
                                f"启动于 {holder.get('started_at')}，命令 {holder.get('command')}），"
                                "本次未启动。请等待其完成或先停止该进程。"
                            )
                        },
                    },
                )
                await self._finish(run, exit_code=9)
                return

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
        # 把当前项目空间透传给 CLI 子进程：CLI 侧 compose_runner 等依赖
        # NOVEL_DATA_ROOT 定位数据根，保证 Web 切换空间后 CLI 读写同一目录。
        env["NOVEL_DATA_ROOT"] = str(project_path(run["project"]).parent)
        env.update(run.get("env_extra") or {})

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
            self._emit(run, {"type": "log", "data": {"text": f"✗ 启动失败：{e}"}})
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
                # 累计日志：SSE 掉线时轮询兜底也能回看，晚订阅者重连时回放
                run["logs"].append(text)
                if len(run["logs"]) > 300:
                    run["logs"] = run["logs"][-300:]
                self._emit(run, {"type": "log", "data": {"text": text}})

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
                                    run["progress_events"].append(ev)
                                    if len(run["progress_events"]) > 200:
                                        run["progress_events"] = (
                                            run["progress_events"][-200:]
                                        )
                                    self._emit(run, {"type": "progress", "data": ev})
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
        done_data = {
            "exit_code": exit_code,
            "summary": summary,
            "state": state_val,
        }
        run["done_data"] = done_data
        self._emit(run, {"type": "done", "data": done_data})

    async def stream(self, run_id: str):
        """生成 SSE 事件序列（dict 形式，由路由层序列化为 text/event-stream）。

        支持晚订阅回放：页面切走再切回、EventSource 重连、或任务已结束后
        才打开页面，都会先收到存量日志 + 进度时间线，再进入实时监听；
        任务已结束时补发 done 后收尾。订阅者使用独立队列，重连不会与
        残留的旧连接争抢事件。
        """
        run = self.runs.get(run_id)
        if run is None:
            return
        # 快照必须在订阅之前同步完成（两步之间无 await，事件循环不会切换），
        # 保证「回放的存量」与「队列里的增量」恰好互补：不重复、不遗漏。
        logs_snapshot = list(run["logs"])
        progress_snapshot = list(run["progress_events"])
        q: asyncio.Queue = asyncio.Queue()
        run["subscribers"].add(q)
        try:
            for text in logs_snapshot:
                yield {"type": "log", "data": {"text": text}}
            for ev in progress_snapshot:
                yield {"type": "progress", "data": ev}
            if run["done"]:
                yield {
                    "type": "done",
                    "data": run["done_data"]
                    or {"exit_code": run["exit_code"] or 0, "summary": None, "state": None},
                }
                return
            while True:
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=2.0)
                except asyncio.TimeoutError:
                    yield {"type": "ping", "data": {}}
                    continue
                yield ev
                if ev["type"] == "done":
                    return
        finally:
            run["subscribers"].discard(q)


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

# 会向小说项目落盘的写命令：启动前做单写者锁预检（UX 层拦截，
# CLI 侧 acquire_project_lock 仍是兜底）
WRITER_COMMANDS = {"autowrite", "rewrite"}


def sanitize_project_name(name: str) -> str:
    """把任意项目名规整为安全目录名。"""
    return "".join(c for c in name.strip() if c.isalnum() or c in "-_") or "my-novel"


def split_args(raw: str) -> list[str]:
    """把原始参数字符串安全切分为 argv（支持引号）。"""
    if not raw.strip():
        return []
    return shlex.split(raw)
