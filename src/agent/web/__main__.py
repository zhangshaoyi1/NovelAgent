"""``python -m agent.web`` 入口（等价于 CLI 的 ``web`` 命令）。"""

from __future__ import annotations

import sys

import uvicorn

from agent.web.app import app


def _parse(argv: list[str]) -> tuple[str, int]:
    host, port = "127.0.0.1", 8000
    for i, a in enumerate(argv):
        if a in ("--host",) and i + 1 < len(argv):
            host = argv[i + 1]
        elif a in ("--port", "-p") and i + 1 < len(argv):
            port = int(argv[i + 1])
    return host, port


if __name__ == "__main__":
    host, port = _parse(sys.argv[1:])
    uvicorn.run(app, host=host, port=port)
