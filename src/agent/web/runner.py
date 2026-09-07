"""SSE 命令运行器（Web UI 实时性核心）。

D2 起 Web 降级为**薄客户端**：``execute`` 不再 spawn 子进程，而是把命令
提交到 writer daemon 的落盘任务队列（``agent.daemon``），由 daemon 全局
串行执行；Web 只轮询任务状态 / 日志增量 / progress.json（G9）并经 SSE
推送给前端。因此 Web 重启不再影响运行中的写任务，也不产生孤儿进程。

保留的 L1 单写者锁预检：对「绕过 daemon 的遗留直跑者」给出即时反馈。
"""

from __future__ import annotations

import asyncio
import atexit
import json
import os
import re
import shlex
import subprocess
import uuid
from pathlib import Path
from typing import Any

from agent.daemon.task_queue import NO_DIR_COMMANDS  # noqa: F401  # 单一真相源（re-export）
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


def _kill_process_tree(proc: Any) -> None:
    """终止子进程及其子孙（Windows 需要 /T，否则只杀直接子进程留孤儿）。"""
    pid = getattr(proc, "pid", None)
    if pid is None:
        return
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                timeout=10,
                check=False,
            )
            return
    except Exception:  # noqa: BLE001 - taskkill 失败退回 proc.kill()
        pass  # noqa: SILENT_DEGRADE
    try:
        proc.kill()
    except Exception:  # noqa: BLE001 - 进程已退出
        pass  # noqa: SILENT_DEGRADE


class RunManager:
    """管理进行中 / 已完成的命令运行实例。"""

    def __init__(self) -> None:
        self.runs: dict[str, dict[str, Any]] = {}
        # L2-A：项目 → 进行中 run_id。同一项目同一时刻只允许一个运行实例，
        # 防止用户在 Web 上重复点击（或页面重载后重发）fork 出第二个写进程——
        # 这是「Web 与 CLI 双写」之外另一条并发写通道。
        self.active_by_project: dict[str, str] = {}
        atexit.register(self._kill_all_children)

    # ---------- L2-A：进程主权 ----------

    def active_run_for(self, project: str) -> dict[str, Any] | None:
        """返回该项目进行中的运行实例（无则 None）。"""
        run_id = self.active_by_project.get(project)
        if run_id is None:
            return None
        run = self.runs.get(run_id)
        if run is None or run.get("done"):
            self.active_by_project.pop(project, None)
            return None
        return run

    def kill_all_children(self) -> None:
        """终止所有仍在运行的子进程（Web 关闭 / 重启时调用，杜绝孤儿写者）。"""
        self._kill_all_children()

    def _kill_all_children(self) -> None:
        for run in list(self.runs.values()):
            proc = run.get("proc")
            if proc is not None and proc.returncode is None:
                _kill_process_tree(proc)

    async def stop(self, run_id: str) -> bool:
        """请求停止某次运行；返回是否找到可停止的任务。

        D2 起 Web 不再自己持有子进程：运行主体是 daemon 队列里的任务，
        停止 = 向任务文件写 ``stop_requested`` 标记，daemon 轮询到后
        ``taskkill /T /F`` 杀进程树。无 task_id 的历史 run 退回旧路径。
        """
        run = self.runs.get(run_id)
        if run is None:
            return False
        run["stop_requested"] = True
        task_id = run.get("task_id")
        if not task_id:
            proc = run.get("proc")
            if proc is None or proc.returncode is not None:
                return False
            self._emit(run, {"type": "log", "data": {"text": "■ 已请求停止，正在终止进程…"}})
            await asyncio.to_thread(_kill_process_tree, proc)
            return True
        from agent.daemon import task_queue as tq

        pdir = project_path(run["project"])
        result = await asyncio.to_thread(tq.request_stop, pdir, task_id)
        if result:
            self._emit(run, {"type": "log", "data": {"text": "■ 已请求停止，daemon 正在终止任务进程树…"}})
            return True
        return False

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
            "task_id": None,  # D2：daemon 队列任务 ID（submit 后填充）
            "stop_requested": False,
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
        # L2-A：登记为该项目活跃实例（同一项目后续启动请求会被 api_run 拦截）
        self.active_by_project[run["project"]] = run_id

        # 单写者锁预检：同一小说已有活跃写进程时不再启动，直接给前端明确反馈。
        # 名单来自注册表（writer_commands()），锁为全项目唯一的 writer.lock。
        if run["command"] in writer_commands():
            from agent.core.project_lock import probe_project_lock

            holder = probe_project_lock(pdir)
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

        # D2：Web 降级为薄客户端——不再自己 spawn 子进程，而是把任务提交到
        # writer daemon 的落盘队列，由 daemon 全局串行执行（单一权威）。
        # Web 进程只负责「看盘」：轮询任务状态 / 日志 / progress.json 回灌 SSE。
        # 好处：Web 重启不影响写任务、无孤儿进程、停止走统一停止标记。
        from agent.daemon import task_queue as tq
        from agent.daemon.core import ensure_daemon

        task = await asyncio.to_thread(
            tq.submit_task,
            pdir,
            run["command"],
            run["argv"],
            "web",
            run.get("env_extra") or {},
        )
        run["task_id"] = task["task_id"]
        self._emit(
            run,
            {
                "type": "log",
                "data": {
                    "text": (
                        f"□ 任务已提交 daemon 队列：{task['task_id']}"
                        f"（{run['command']}），等待串行执行"
                    )
                },
            },
        )
        ok = await asyncio.to_thread(ensure_daemon, [pdir.parent])
        if not ok:
            self._emit(
                run,
                {
                    "type": "log",
                    "data": {
                        "text": "⚠ daemon 自动拉起失败；任务已排队，请手动运行 `python -m agent.daemon`"
                    },
                },
            )

        await self._watch_task(run, task["task_id"])

    async def _watch_task(self, run: dict[str, Any], task_id: str) -> None:
        """轮询 daemon 任务：任务文件（状态/停止）+ 日志增量 + progress.json 增量。"""
        from agent.daemon import task_queue as tq

        pdir = project_path(run["project"])
        log_path = tq.tasks_root(pdir) / "logs" / f"{task_id}.log"
        seen_seq = -1
        last_mtime = 0.0
        log_offset = 0
        log_remainder = ""

        def read_log_increment() -> list[str]:
            """读日志文件新增部分，返回完整行列表（半行留到下轮）。"""
            nonlocal log_offset, log_remainder
            try:
                with open(log_path, "rb") as f:
                    f.seek(log_offset)
                    chunk = f.read()
                if not chunk:
                    return []
                log_offset += len(chunk)
            except OSError:
                return []
            text = log_remainder + chunk.decode("utf-8", "replace")
            if not text.endswith("\n"):
                *lines, log_remainder = text.split("\n")
            else:
                lines = text.split("\n")[:-1]
                log_remainder = ""
            return [strip_rich(line).rstrip("\r") for line in lines if line.strip()]

        while True:
            task = await asyncio.to_thread(tq.get_task, pdir, task_id)
            task_status = (task or {}).get("status")

            for text in await asyncio.to_thread(read_log_increment):
                run["logs"].append(text)
                if len(run["logs"]) > 300:
                    run["logs"] = run["logs"][-300:]
                self._emit(run, {"type": "log", "data": {"text": text}})

            # progress.json 增量（G9 事件流，逻辑与旧 tail_progress 一致）
            progress_file = pdir / ".state" / "progress.json"
            try:
                mtime = progress_file.stat().st_mtime
                if mtime != last_mtime:
                    last_mtime = mtime
                    try:
                        data = json.loads(progress_file.read_text(encoding="utf-8"))
                    except Exception:
                        data = None  # noqa: SILENT_DEGRADE
                    if data:
                        for ev in data.get("events", []):
                            seq = ev.get("seq", 0)
                            if seq > seen_seq:
                                seen_seq = seq
                                run["progress_events"].append(ev)
                                if len(run["progress_events"]) > 200:
                                    run["progress_events"] = run["progress_events"][-200:]
                                self._emit(run, {"type": "progress", "data": ev})
            except FileNotFoundError:
                pass  # noqa: SILENT_DEGRADE

            if task_status in tq.TERMINAL_STATUSES:
                rc = (task or {}).get("exit_code")
                if task_status == tq.STATUS_STOPPED:
                    self._emit(run, {"type": "log", "data": {"text": "■ 任务已停止"}})
                    await self._finish(run, exit_code=rc if rc is not None else 9)
                elif task_status == tq.STATUS_FAILED:
                    self._emit(
                        run,
                        {"type": "log", "data": {"text": f"✗ 任务失败（exit={rc}），日志见 .state/tasks/logs/"}},
                    )
                    await self._finish(run, exit_code=rc if rc is not None else 1)
                else:
                    await self._finish(run, exit_code=rc or 0)
                return
            await asyncio.sleep(0.5)

    async def _finish(self, run: dict[str, Any], exit_code: int) -> None:
        run["exit_code"] = exit_code
        run["done"] = True
        # L2-A：释放项目活跃占位，允许下一次启动
        if self.active_by_project.get(run["project"]) == run["id"]:
            self.active_by_project.pop(run["project"], None)
        # 收尾：附上看板摘要 + 最新状态（供前端刷新）
        try:
            from agent.web.state import get_project_state, get_summary

            summary = get_summary(run["project"])
            state_val = get_project_state(run["project"]).get("state")
        except Exception:  # noqa: BLE001
            summary = None
            state_val = None  # noqa: SILENT_DEGRADE
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
                    continue  # noqa: SILENT_DEGRADE
                yield ev
                if ev["type"] == "done":
                    return
        finally:
            run["subscribers"].discard(q)


# 全局单例（进程内）
run_manager = RunManager()


def writer_commands() -> set[str]:
    """会向项目落盘的写命令名单——从 ``@command(writes=True)`` 注册表推导。

    历史教训（2026-09-07 五灵破归档事故）：此前此处硬编码 ``{"autowrite",
    "rewrite"}``，与 CLI 侧真正建锁的命令**完全错位**——CLI 的 rewrite 从不建
    ``rewrite.lock``，预检恒判「空闲」形同虚设；而真正会建锁的 ``write`` 反而不在
    名单里。现改为单一真相源：命令在注册表声明 ``writes=True``，Web 自动跟随。
    """
    try:
        import agent.cli.commands  # noqa: F401  # 触发 @command 注册副作用
    except Exception:  # noqa: BLE001 - 注册表加载失败时退回保守名单
        pass  # noqa: SILENT_DEGRADE
    try:
        from agent.core.engine.command_router import WRITE_COMMANDS

        if WRITE_COMMANDS:
            return set(WRITE_COMMANDS)
    except Exception:  # noqa: BLE001
        pass  # noqa: SILENT_DEGRADE
    # 兜底：宁可多拦，不可漏拦
    return {"autowrite", "write", "rewrite", "compose", "rollback"}


def is_write_command(name: str) -> bool:
    """命令是否会计入项目写锁（供 API 层做同项目去重判断）。"""
    return name in writer_commands()


def sanitize_project_name(name: str) -> str:
    """把任意项目名规整为安全目录名。"""
    return "".join(c for c in name.strip() if c.isalnum() or c in "-_") or "my-novel"


def split_args(raw: str) -> list[str]:
    """把原始参数字符串安全切分为 argv（支持引号）。"""
    if not raw.strip():
        return []
    return shlex.split(raw)
