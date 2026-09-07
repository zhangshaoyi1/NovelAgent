"""``python -m agent.daemon`` 入口：writer daemon 前台运行。

用法::

    python -m agent.daemon --root D:/project/NovelAgent/novels [--poll 1.0]

多个 ``--root`` 可同时监听多个数据根。正常停止：写 stop 标志
（``agent daemon-stop``）后 daemon 会在**当前任务完成后**退出，
不半途杀写进程。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agent.daemon", description="NovelAgent writer daemon")
    parser.add_argument(
        "--root",
        action="append",
        default=None,
        help="监听的数据根（novels 目录），可重复；缺省用 NOVEL_DATA_ROOT",
    )
    parser.add_argument("--poll", type=float, default=1.0, help="轮询间隔秒数（默认 1.0）")
    ns = parser.parse_args(argv)

    roots = [Path(r) for r in (ns.root or [])]
    if not roots:
        from agent.daemon.core import default_root

        roots = [default_root()]

    for r in roots:
        r.mkdir(parents=True, exist_ok=True)

    from agent.daemon.core import WriterDaemon

    print(f"[daemon] 启动：roots={[str(r) for r in roots]} pid={__import__('os').getpid()}")
    WriterDaemon(roots, poll_interval=ns.poll).run_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
