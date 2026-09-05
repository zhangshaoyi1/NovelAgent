"""原子文件写入（竞品差距改进计划 P0-3，对齐 inkos atomic-file-set / outbox 思路）。

长篇写章链路一次推进多个落盘文件（章节正文 / ``.state/state.json`` / 账本投影）。
半成品文件（写了一半的正文、截断的 JSON）是"状态已推进、正文丢失"类故障的根源。
本模块提供三层原子写：

1. ``atomic_write_text`` / ``atomic_write_bytes``：单文件 temp + ``os.replace``（同盘原子）。
2. ``atomic_write_set``：多文件**全成或全不成**——先把全部内容写进同一临时目录（与目标
   同盘），全部成功后逐个 ``os.replace`` 提交；任一步失败则清理临时目录、目标目录零污染。
3. 失败语义：写临时阶段失败 → 目标文件保持原值；replace 阶段失败（极罕见，如同盘被
   外部占用）→ 抛出原始异常，已替换文件保留（调用方可靠 ``rollback``/``snapshot`` 补救）。

Windows 兼容：``os.replace`` 可覆盖已存在目标（``Path.replace`` 同义），无 POSIX rename
语义差异；临时目录用 ``tempfile.mkdtemp`` 建在**首个目标文件的祖先目录**下，保证同盘。
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

PathLike = Path | str
_Content = str | bytes


def _to_bytes(content: _Content) -> bytes:
    if isinstance(content, bytes):
        return content
    return content.encode("utf-8")


def atomic_write_text(path: PathLike, text: str, *, encoding: str = "utf-8") -> Path:
    """单文件原子写：先写同目录临时文件，再 ``os.replace`` 覆盖目标。"""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp-atomic")
    try:
        tmp.write_text(text, encoding=encoding)
        tmp.replace(target)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
    return target


def atomic_write_bytes(path: PathLike, data: bytes) -> Path:
    """单文件原子写（字节内容）。"""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp-atomic")
    try:
        tmp.write_bytes(data)
        tmp.replace(target)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
    return target


def atomic_write_set(writes: dict[PathLike, _Content]) -> list[Path]:
    """多文件原子提交：全部成功才全部生效，任何一步失败则目标零污染。

    Args:
        writes: ``{目标路径: 文本或字节}``；至少一项。

    Returns:
        按传入顺序的目标路径列表。

    Raises:
        ValueError: ``writes`` 为空。
        OSError: 预写阶段任何失败（此时未触碰任何目标）或 replace 阶段失败
            （已尽力替换的文件保留，异常向上传播）。
    """
    if not writes:
        raise ValueError("atomic_write_set 需要至少一个写入项")

    targets = [Path(p) for p in writes]
    targets[0].parent.mkdir(parents=True, exist_ok=True)
    staging_root = Path(tempfile.mkdtemp(prefix=".atomic-", dir=str(targets[0].parent)))
    staged: list[tuple[Path, Path, bytes]] = []
    try:
        for target, content in zip(targets, writes.values()):
            target.parent.mkdir(parents=True, exist_ok=True)
            payload = _to_bytes(content)
            tmp = staging_root / target.name
            tmp.write_bytes(payload)
            staged.append((target, tmp, payload))
    except OSError:
        shutil.rmtree(staging_root, ignore_errors=True)
        raise

    try:
        committed: list[Path] = []
        for target, tmp, _payload in staged:
            tmp.replace(target)  # 同盘 rename，Windows/POSIX 均可覆盖
            committed.append(target)
        return committed
    finally:
        shutil.rmtree(staging_root, ignore_errors=True)
