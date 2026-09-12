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

import os
import shutil
import tempfile
import time
import uuid
from pathlib import Path

from agent.core.infra.degrade import degrade

PathLike = Path | str
_Content = str | bytes


def _discard_tmp(path: Path) -> None:
    """尽力丢弃临时文件 / 临时目录，**绝不向外抛错**。

    异常边界（2026-09-11 事故补）：WorkBuddy safe-delete 批量护栏在删除越线时抛
    ``SystemExit(1)`` 而非 ``OSError``。本函数多用在 ``finally`` 里，异常逃逸会
    掩盖真实结果并让调用方误判原子写失败——故统一吞掉并投递可见降级日志。
    ``shutil.rmtree(ignore_errors=True)`` 只吞 ``OSError``，**不吞** ``SystemExit``。
    """
    try:
        if path.is_file():
            path.unlink()
        elif path.exists():
            shutil.rmtree(path, ignore_errors=True)
    except SystemExit as exc:
        degrade("atomic.discard", f"临时路径清理被 safe-delete 护栏拦截：{path.name}", exc)
    except OSError as exc:
        degrade("atomic.discard", f"临时路径清理失败：{path.name}", exc)

#: ``os.replace`` 撞锁（Windows sharing violation → ``PermissionError``/WinError 5）
# 时的重试退避序列；全部耗尽后做最后一次尝试，仍失败则向上传播。
_REPLACE_BACKOFF_S = (0.1, 0.3, 0.9)


def _unique_tmp(target: Path, tag: str = "atomic") -> Path:
    """同目录唯一临时文件名（pid + 随机后缀）。

    固定 tmp 名（如 ``<name>.tmp-atomic``）在多进程并发写同一目标时会被两个写者
    同时打开、交错写入——tmp 文件本身就是共享资源。唯一名保证各写者的暂存互不可见。
    """
    return target.with_name(
        f"{target.name}.tmp-{tag}-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    )


def _replace_with_retry(tmp: Path, target: Path) -> None:
    """``os.replace`` 带 Windows 撞锁重试。

    同一目标存在多个写者（daemon 心跳 vs 子进程状态更新、CLI vs web）时，
    目标文件可能被另一进程短暂打开，``os.replace`` 抛 ``PermissionError``
    （WinError 5）。按退避序列重试避开撞锁窗口；耗尽后最后一搏，仍失败向上传播。
    """
    for delay in _REPLACE_BACKOFF_S:
        try:
            os.replace(tmp, target)
            return
        except PermissionError:  # noqa: SILENT_DEGRADE - 重试后上抛，非降级点
            time.sleep(delay)
    os.replace(tmp, target)


def _to_bytes(content: _Content) -> bytes:
    if isinstance(content, bytes):
        return content
    return content.encode("utf-8")


def atomic_write_text(path: PathLike, text: str, *, encoding: str = "utf-8") -> Path:
    """单文件原子写：唯一临时文件 + 撞锁重试 + ``os.replace`` 覆盖目标。"""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = _unique_tmp(target)
    try:
        tmp.write_text(text, encoding=encoding)
        _replace_with_retry(tmp, target)
    finally:
        if tmp.exists():
            _discard_tmp(tmp)
    return target


def atomic_write_bytes(path: PathLike, data: bytes) -> Path:
    """单文件原子写（字节内容）：唯一临时文件 + 撞锁重试。"""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = _unique_tmp(target)
    try:
        tmp.write_bytes(data)
        _replace_with_retry(tmp, target)
    finally:
        if tmp.exists():
            _discard_tmp(tmp)
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
        _discard_tmp(staging_root)
        raise

    try:
        committed: list[Path] = []
        for target, tmp, _payload in staged:
            _replace_with_retry(tmp, target)  # 同盘 rename + 撞锁重试
            committed.append(target)
        return committed
    finally:
        _discard_tmp(staging_root)
