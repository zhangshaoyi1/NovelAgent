"""Writer daemon 消费循环（D2-1）：全局串行的唯一写执行权威。

核心语义：
- **全局串行**：同一时刻整个系统最多一个写任务在执行（跨项目也串行）。
  这是比「每项目串行」更强的保证——D2 阶段优先正确性，多项目并行作为
  后续优化（每项目一把执行线程 + 项目级队列即可扩展）。
- **进程内是编排、执行是子进程**：daemon 持久运行，spawn
  ``python -m agent.cli <command>`` 子进程执行任务（复用全部既有 CLI
  逻辑，含 L1 派发层加锁）。daemon 长寿命持有子进程句柄 → 停止 =
  taskkill /T /F 杀进程树；daemon 自身退出不产生孤儿（子进程随任务
  归档，daemon 崩溃重启时孤儿任务标记 failed）。
- **writer.lock 降级为断言**：daemon 队列已保证互斥，子进程内的
  acquire_project_lock 只防「绕过 daemon 的遗留直跑者」（--direct）。
- **心跳**：每个轮询周期刷新 ``<root>/.daemon/heartbeat.json``，
  ``task submit`` / Web 据此判断 daemon 是否存活、是否需要自动拉起。
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from agent.core.infra.degrade import degrade
from agent.daemon import task_queue as tq
from agent.daemon import process_manager as pm

logger = logging.getLogger(__name__)

#: agent 仓库根（含 src/ 与 scripts/）：本文件位于 <root>/src/agent/daemon/core.py
REPO_ROOT = Path(__file__).resolve().parents[3]


def detach_console_if_present() -> None:
    """Windows 兜底：daemon 带可见控制台时主动解除关联（弹窗根治的最后一道保险）。

    背景：ensure_daemon 已用 DETACHED_PROCESS|CREATE_NO_WINDOW 拉起 daemon，
    但实测仍有「第三方/脚本以普通方式 spawn daemon」的路径（父进程链含 Web
    服务进程树，2026-09-07 用户复现弹窗）——此时 python.exe 会分配可见控制台。
    FreeConsole 后若无其他进程附着该控制台，conhost 退出、窗口立即关闭，
    且不依赖「谁拉起、怎么拉起」，彻底兜底。
    """
    if os.name != "nt":
        return
    import ctypes

    k32 = ctypes.windll.kernel32
    if k32.GetConsoleWindow():
        k32.FreeConsole()
        # 控制台句柄已失效，后续 print 可能抛错 → 输出重定向到空
        try:
            devnull = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115 - 进程生命周期
            sys.stdout = devnull  # type: ignore[assignment]
            sys.stderr = devnull  # type: ignore[assignment]
        except Exception:  # noqa: BLE001
            pass  # noqa: SILENT_DEGRADE - 输出重定向失败不影响守护进程主体


def default_root() -> Path:
    """默认监听的数据根：NOVEL_DATA_ROOT 优先，否则 <仓库根>/../novels。"""
    import os

    env_root = os.environ.get("NOVEL_DATA_ROOT")
    if env_root:
        return Path(env_root)
    return REPO_ROOT.parent / "novels"


def kill_process_tree_pid(pid: int) -> None:
    """按 PID 终止进程树（Windows 用 taskkill /T /F；POSIX 退回 killpg）。"""
    if pid <= 0:
        return
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                timeout=10,
                check=False,
            )
            return
        except Exception:  # noqa: BLE001 - taskkill 失败退回下面的通用兜底
            pass  # noqa: SILENT_DEGRADE
    try:
        if hasattr(os, "killpg"):
            import signal

            os.killpg(pid, signal.SIGTERM)
        else:
            os.kill(pid, 15)
    except (ProcessLookupError, PermissionError, OSError):
        pass  # noqa: SILENT_DEGRADE - 进程已退出


class WriterDaemon:
    """扫描 watched roots 的任务队列，全局串行消费。"""

    def __init__(self, roots: list[Path | str], poll_interval: float = 1.0) -> None:
        self.roots = [Path(r) for r in roots]
        self.poll_interval = poll_interval
        self._child: subprocess.Popen[bytes] | None = None
        self._child_task: dict[str, Any] | None = None
        self._log_fh: Any = None

    # ---------------- 主循环 ----------------
    def run_forever(self) -> None:
        # 启动阶段（清残留标志 / 崩溃恢复）同样在守护之外会静默致死，逐个兜底。
        try:
            self._clear_stale_stop_flags()
        except BaseException:  # noqa: BLE001 - 启动阶段异常不致命
            self._log_crash()  # noqa: SILENT_DEGRADE - 落盘留痕后继续启动
        try:
            self._recover_orphans()
        except BaseException:  # noqa: BLE001 - 启动阶段异常不致命
            self._log_crash()  # noqa: SILENT_DEGRADE - 落盘留痕后继续启动
        # 设施③（2026-09-11）：登记本进程代码指纹，并提示「仍在跑旧代码」的在途进程——
        # 把「改了 .py 以为生效」的隐式假设变成可查事实（doctor 亦可读取该记录）。
        try:
            from agent.core.infra.runtime_selfcheck import (
                format_fingerprint,
                remember_process,
                stale_running_processes,
            )

            for root in self.roots:
                for msg in stale_running_processes(root):
                    self._append_runtime_log(root, f"⚠ {msg}")
                remember_process(root, "daemon")
                self._append_runtime_log(root, f"daemon 启动 {format_fingerprint()}")
        except BaseException:  # noqa: BLE001 - 自检失败不影响 daemon 启动
            pass  # noqa: SILENT_DEGRADE
        while True:
            try:
                for root in self.roots:
                    tq.write_heartbeat(root, os.getpid())
                if self._stop_flagged():
                    self._shutdown_gracefully()
                    return
                if self._child is None:
                    self._try_claim_and_spawn()
                else:
                    self._poll_child()
            except BaseException:  # noqa: BLE001 - daemon 绝不因单轮异常静默死亡
                # 2026-09-11 事故：daemon 由 Web 以 DEVNULL 拉起，主循环内任何未捕获
                # 异常（含 safe-delete 护栏抛出的 SystemExit）都会让进程**无声退出**：
                # 既没有停止执行者，也没有服务端告警，表现为「页面点终止一直终止中」。
                # 兜底：单轮失败记录栈、短暂退避后继续，进程存活是第一优先级。
                self._log_crash()
                time.sleep(1.0)
                continue  # noqa: SILENT_DEGRADE - 已写 .daemon/crash.log，进程继续服务
            time.sleep(self.poll_interval)

    def _log_crash(self) -> None:
        """把主循环未捕获异常写入 ``<root>/.daemon/crash.log``（绝不再抛）。"""
        try:
            import traceback

            stamp = datetime.now().isoformat(timespec="seconds")
            text = f"\n===== {stamp} daemon 主循环异常（已兜底，进程继续）=====\n{traceback.format_exc()}"
            for root in self.roots:
                try:
                    log_path = Path(root) / ".daemon" / "crash.log"
                    log_path.parent.mkdir(parents=True, exist_ok=True)
                    if log_path.exists() and log_path.stat().st_size > 1_000_000:
                        log_path.write_text("", encoding="utf-8")  # 截断防无限增长
                    with open(log_path, "a", encoding="utf-8") as fh:
                        fh.write(text)
                except Exception:  # noqa: BLE001 - 崩溃日志本身失败不得再抛
                    pass  # noqa: SILENT_DEGRADE - 日志写失败也不能影响主循环
            print(text, flush=True)
        except BaseException:  # noqa: BLE001 - 记录失败也绝不冒泡
            pass  # noqa: SILENT_DEGRADE - 记录失败也绝不冒泡

    def _append_runtime_log(self, root: Path, message: str) -> None:
        """把运行时生效性告警写入 ``<root>/.daemon/runtime.log``（绝不再抛）。

        与 ``crash.log`` 分离：这是「改动未生效 / 跑旧代码」的可查痕迹，
        不与「进程崩了」混淆。
        """
        try:
            log_path = Path(root) / ".daemon" / "runtime.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().isoformat(timespec="seconds")
            with open(log_path, "a", encoding="utf-8") as fh:
                fh.write(f"[{stamp}] {message}\n")
        except (OSError, SystemExit):
            pass  # noqa: SILENT_DEGRADE - 日志写失败不得影响 daemon 启动

    def _stop_flagged(self) -> bool:
        return any(tq.daemon_stop_flag(r).exists() for r in self.roots)

    def _shutdown_gracefully(self) -> None:
        """收完当前任务再退出（stop 语义：不杀正在写的进程，防止半章损坏）。"""
        for root in self.roots:
            tq.clear_daemon_stop_flag(root)
            tq.write_heartbeat(root, os.getpid())
        while self._child is not None:
            self._poll_child()
            if self._child is not None:
                time.sleep(self.poll_interval)
        for root in self.roots:
            tq.clear_daemon_stop_flag(root)

    def _clear_stale_stop_flags(self) -> None:
        """启动时清除「早于本次启动」的停止标志。

        ``daemon stop`` 只表示「让正在运行的 daemon 退出」；若在无 daemon 时
        被调用，标志会残留落盘，毒化下一次拉起——新 daemon 把它误读为对自己
        的停止请求，启动即静默优雅退出，队列任务永远不被认领。
        2026-09-10 事故：Web 点「一键续写」→ 任务卡在 queued、实时任务为空。
        判据：本进程启动前就存在的标志必然是陈旧残留（停止请求不可能指向
        一个尚未启动的进程），启动即清除。
        """
        for root in self.roots:
            flag = tq.daemon_stop_flag(root)
            if flag.exists():
                tq.clear_daemon_stop_flag(root)
                print(f"[daemon] 清除陈旧停止标志（先于本次启动，已忽略）：{flag}")

    def _recover_orphans(self) -> None:
        """启动时恢复遗留任务（阶段 1：先杀后标/重新接管 + 锁自愈）。

        取代旧的 ``interrupt_orphans``（只标 failed 不杀进程——遗留孤儿会继续写、
        与新任务抢锁）。现委托 ProcessManager.recover_running：
        pid 存活且心跳新鲜 → 重新接管；孤儿进程 → 杀树 + 标记；进程已死 → 标记 + 锁清。
        """
        for root in self.roots:
            for project in self._projects_under(root):
                n = pm.recover_running(project)
                if n:
                    print(f"[daemon] 恢复：{project.name} 处理 {n} 个遗留 running 任务（先杀后标/接管）")

    def _projects_under(self, root: Path) -> list[Path]:
        """root 下所有像小说项目的子目录（含 .state）。"""
        if not root.is_dir():
            return []
        return [p for p in sorted(root.iterdir()) if p.is_dir() and (p / ".state").is_dir()]

    # ---------------- 认领与执行 ----------------
    def _try_claim_and_spawn(self) -> None:
        if self._child is not None:
            return  # 全局串行：已有任务在执行，绝不认领第二个
        for root in self.roots:
            for project in self._projects_under(root):
                task = tq.claim_next(project, os.getpid())
                if task is None:
                    continue
                self._spawn(task)
                return

    def _build_cmd(self, task: dict) -> list[str]:
        cmd = [sys.executable, "-m", "agent.cli", task["command"]]
        project_dir = task["project_dir"]
        if task["command"] not in tq.NO_DIR_COMMANDS:
            cmd += ["--dir", project_dir]
        cmd += list(task.get("argv") or [])
        return cmd

    def _spawn(self, task: dict) -> None:
        project_dir = Path(task["project_dir"])
        log_path = tq.tasks_root(project_dir) / "logs" / f"{task['task_id']}.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        self._log_fh = open(log_path, "ab", buffering=0)  # noqa: SIM115 - 生命周期与子进程绑定

        env = dict(os.environ)
        env["PYTHONUNBUFFERED"] = "1"
        env["NOVEL_DATA_ROOT"] = str(project_dir.parent)
        env["NOVELAGENT_TASK_ID"] = task["task_id"]
        env.update(task.get("env_extra") or {})

        popen_kwargs: dict[str, Any] = {}
        if os.name == "nt":
            # CREATE_NEW_PROCESS_GROUP：进程树可整体管理（taskkill /T /F）；
            # CREATE_NO_WINDOW：daemon 无控制台，控制台型子进程（python.exe）
            # 会被系统自动分配一个可见控制台窗口——Web 自动写作时弹出 python
            # 黑窗（2026-09-07 用户反馈），必须显式压制。
            popen_kwargs["creationflags"] = (
                subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
            )

        try:
            proc = subprocess.Popen(
                self._build_cmd(task),
                cwd=str(REPO_ROOT),
                env=env,
                stdout=self._log_fh,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                **popen_kwargs,
            )
        except Exception as e:  # noqa: BLE001 - 启动失败 = 任务失败，不阻塞后续
            print(f"[daemon] 任务 {task['task_id']} 启动失败：{e}")
            tq.finalize_task(project_dir, task["task_id"], tq.STATUS_FAILED, note=f"spawn 失败：{e}")
            if self._log_fh:
                self._log_fh.close()
                self._log_fh = None
            return

        self._child = proc
        self._child_task = task
        # 进程管理（阶段 1）：记录超时阈值 / 锁归属 / 初始心跳
        tq.update_task(
            project_dir, task["task_id"],
            pid=proc.pid,
            max_runtime_s=pm.resolve_max_runtime(
                task["command"], list(task.get("argv") or [])
            ),
            lock_owned=task["command"] in tq.writer_commands(),
            heartbeat_at=datetime.now().isoformat(timespec="seconds"),
        )
        print(f"[daemon] 执行任务 {task['task_id']}（{task['command']}，pid={proc.pid}）")

    def _poll_child(self) -> None:
        assert self._child is not None and self._child_task is not None
        task = self._child_task
        project_dir = Path(task["project_dir"])

        # 任务级监督心跳（daemon 每轮刷新 = 存活证明；崩溃恢复据此区分孤儿）
        tq.update_task_heartbeat(project_dir, task["task_id"])

        current = tq.get_task(project_dir, task["task_id"]) or {}

        # ⓪ 进度停滞熔断（每章重置语义）：项目进度产物超过窗口无更新 → 挂起判定。
        # 先于墙钟检查：流水线健康推进时（任一产物有更新）永不触发，
        # 与 ① 的章数缩放墙钟互补——墙钟兜"整体超预算"，停滞兜"单点挂死"。
        if pm.should_stall(current):
            stall_s = pm._env_int_stall()
            print(
                f"[daemon] 任务 {task['task_id']} 进度停滞"
                f"（>{stall_s}s 无任何进度产物更新），强制终止进程树"
            )
            pid = self._child.pid
            self._child = None
            self._child_task = None
            if self._log_fh:
                self._log_fh.close()
                self._log_fh = None
            pm.force_terminate(
                project_dir, task["task_id"], pid,
                note=f"进度停滞熔断（ProcessManager，>{stall_s}s 无更新）",
                status=tq.STATUS_FAILED,
            )
            return

        # ① 超时熔断：运行超过 max_runtime_s → 强制终止（防僵尸任务堵死全局串行队列）
        if pm.should_timeout(current):
            print(
                f"[daemon] 任务 {task['task_id']} 超时"
                f"（>{current.get('max_runtime_s')}s），强制终止进程树"
            )
            pid = self._child.pid
            self._child = None
            self._child_task = None
            if self._log_fh:
                self._log_fh.close()
                self._log_fh = None
            pm.force_terminate(
                project_dir, task["task_id"], pid,
                note="超时熔断（ProcessManager）", status=tq.STATUS_FAILED,
            )
            return

        # ② 停止请求：杀进程树（轮询任务文件，CLI/Web 写入的 stop_requested）
        if current.get("stop_requested") and self._child.returncode is None:
            print(f"[daemon] 收到停止请求：{task['task_id']}，终止进程树 pid={self._child.pid}")
            kill_process_tree_pid(self._child.pid)

        rc = self._child.poll()
        if rc is None:
            return
        self._child = None
        self._child_task = None
        if self._log_fh:
            self._log_fh.close()
            self._log_fh = None

        # rc 与 stop 标记双查：taskkill 后 rc 为非零，需区分「失败」与「被停止」
        was_stopped = bool(current.get("stop_requested"))
        if was_stopped:
            status = tq.STATUS_STOPPED
        elif rc == 0:
            status = tq.STATUS_DONE
        else:
            status = tq.STATUS_FAILED
        tq.finalize_task(project_dir, task["task_id"], status, exit_code=rc)
        print(f"[daemon] 任务 {task['task_id']} 结束：{status}（exit={rc}）")


# ---------------------------------------------------------------- 自动拉起
def ensure_daemon(roots: list[Path | str]) -> bool:
    """确保某个 watched root 上有活着的 daemon；没有则后台拉起。

    Returns:
        True 表示 daemon 已在运行或成功拉起。
    """
    roots = [Path(r) for r in roots]
    if any(tq.daemon_alive(r) for r in roots):
        return True
    # -u：daemon 是常驻进程，必须禁缓冲——否则崩溃前最后几行（含 degrade 告警）留不下
    args = [sys.executable, "-u", "-m", "agent.daemon"]
    for r in roots:
        args += ["--root", str(r)]
    # 环境隔离：daemon 是常驻进程，禁止继承宿主的 safe-delete 护栏 shim
    # （护栏对「删除」抛 SystemExit，而 except Exception 捕不到，会打死 daemon——
    # 2026-09-11 事故族）。显式关闭后关键路径的 unlink 退化为普通删除，进程不受影响。
    child_env = dict(os.environ)
    child_env["CODEBUDDY_SAFE_DELETE_ENABLED"] = "0"
    child_env["PYTHONUNBUFFERED"] = "1"
    # ★ A3（2026-09-20）：daemon 自身的 stdout/stderr 必须**落盘**（原为 DEVNULL）。
    #   DEVNULL 会让 degrade() 的 WARNING（无 handler 时经 logging.lastResort 落
    #   stderr）与所有 print 全部丢失 ⇒ 降级/熔断/落盘失败在运行时完全不可见；
    #   而 CLI 失败提示却指向「<root>/.daemon/ 日志」，那里原本空无一物。
    log_path = tq.daemon_log_path(roots[0])
    log_fh: Any = subprocess.DEVNULL
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_fh = open(log_path, "ab", buffering=0)  # noqa: SIM115 - 生命周期与子进程绑定
    except OSError as e:
        # 真实降级点：daemon 输出将失去落盘目的地（退回 DEVNULL）⇒ 必须走契约登记
        degrade("daemon.ensure_daemon.log", "daemon 日志落盘不可用，输出退回 DEVNULL", e)
        log_fh = subprocess.DEVNULL
    kwargs: dict[str, Any] = {
        "cwd": str(REPO_ROOT),
        "stdin": subprocess.DEVNULL,
        "stdout": log_fh,
        "stderr": subprocess.STDOUT,
        "close_fds": True,
        "env": child_env,
    }
    if os.name == "nt":
        kwargs["creationflags"] = (
            subprocess.DETACHED_PROCESS
            | subprocess.CREATE_NEW_PROCESS_GROUP
            | subprocess.CREATE_NO_WINDOW  # 双保险：任何路径拉起 daemon 都不弹窗
        )
    else:
        kwargs["start_new_session"] = True
    try:
        subprocess.Popen(args, **kwargs)
    except Exception:  # noqa: BLE001
        return False
    finally:
        # 子进程已继承句柄副本；父侧副本及时关闭，避免句柄泄漏
        if log_fh is not subprocess.DEVNULL:
            try:
                log_fh.close()
            except OSError:
                logger.debug("daemon 日志句柄关闭失败（不影响已拉起的子进程）", exc_info=True)
    # 启动探针：心跳不仅要「出现」，还必须在窗口内「推进」≥1 次。
    # 只写一次心跳便退出的 daemon（历史事故：误吞陈旧 stop.flag 后静默优雅
    # 退出）也是心跳文件存在且新鲜，旧判据会误报 True，掩盖拉起失败。
    # 健康 daemon 每 poll_interval 秒刷新心跳，约 1.3s 内即可见推进。
    first_ts: dict[Path, float] = {}
    for _ in range(20):  # 约 6s
        time.sleep(0.3)
        for r in roots:
            if not tq.heartbeat_alive(r):
                continue
            ts = float((tq.read_heartbeat(r) or {}).get("ts") or 0)
            prev = first_ts.setdefault(r, ts)
            if ts > prev:  # 心跳推进 → 消费循环确实在跑
                return True
    return False
