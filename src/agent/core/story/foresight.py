"""伏笔确定性状态机 · Thread→Beats（G15 P0-2）

对标 DeepWrite `plot.ts`，把伏笔从扁平 4 态升级为「thread + beats」生命周期：

- ``ForesightThread``：一条伏笔（core_question / hidden_truth / planned_span /
  beats[]），状态由已 commit 的 beat **纯函数推导**（不靠 LLM 主观断言）。
- ``ForesightBeat``：一个生命周期动作（plant/reinforce/misdirect/partial_reveal/
  reveal/payoff/aftermath），规划锚（volume/arc/event/chapter）与执行锚
  （exec_status + commit_id）解耦；只有 ``committed`` 才允许携带 commit_id。
- ``derive_status``：纯函数 → planned / open / progressing / resolved / abandoned。

与 M13（`workflows/m13_foreshadow.py` 的扁平表格）**并存**：本模块是确定性管理层，
不在 doctype 上替换既有表格命令。
"""

from __future__ import annotations

import json
from typing import Literal

from pathlib import Path

from pydantic import BaseModel, field_validator, model_validator

BeatType = Literal[
    "plant", "reinforce", "misdirect",
    "partial_reveal", "reveal", "payoff", "aftermath",
]
Span = Literal["local", "within_volume", "cross_volume"]
BeatExec = Literal["planned", "written", "committed", "missed"]
ThreadStatus = Literal["planned", "open", "progressing", "resolved", "abandoned"]

_RESOLVING_TYPES = {"reveal", "payoff"}
_PROGRESSING_TYPES = {"reinforce", "misdirect", "partial_reveal"}


class ForesightBeat(BaseModel):
    """伏笔生命周期的单个动作。"""

    beat_id: str
    type: BeatType
    # 规划锚（与执行锚解耦）
    anchor_volume: str | None = None
    anchor_arc: str | None = None
    anchor_event: str | None = None
    anchor_chapter: int | None = None
    # 执行锚
    exec_status: BeatExec = "planned"
    commit_id: str | None = None

    @field_validator("beat_id")
    @classmethod
    def _nonempty_id(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("beat_id 不可为空")
        return v.strip()

    @model_validator(mode="after")
    def _commit_only_when_committed(self) -> "ForesightBeat":
        if self.exec_status == "committed" and not self.commit_id:
            raise ValueError("committed beat 必须有 commit_id")
        if self.commit_id and self.exec_status != "committed":
            raise ValueError("只有 committed beat 才能携带 commit_id")
        return self


class ForesightThread(BaseModel):
    """一条带生命周期的伏笔。"""

    fid: str
    core_question: str
    hidden_truth: str
    planned_span: Span = "local"
    expected_reader_effect: str = ""
    expected_resolve: str = ""          # 预期回收点（兼容 M13 口径，如 "S04/ch40"）
    status: ThreadStatus = "planned"    # 由 derive_status 维护（可被显式设置供只读导入）
    beats: list[ForesightBeat] = []

    @field_validator("fid", "core_question", "hidden_truth")
    @classmethod
    def _nonempty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("不可为空")
        return v.strip()

    @model_validator(mode="after")
    def _beat_unique(self) -> "ForesightThread":
        seen = set()
        for b in self.beats:
            if b.beat_id in seen:
                raise ValueError(f"beat 重复: {b.beat_id}")
            seen.add(b.beat_id)
        return self


def derive_status(thread: ForesightThread, *, set_status: bool = True) -> ThreadStatus:
    """**纯函数**由已 commit 的 beat 推导伏笔状态。

    - 有 reveal 或 payoff 已 commit → ``resolved``（回收完成）；
    - 有 reinforce/misdirect/partial_reveal 已 commit → ``progressing``（演进中）；
    - 只有 plant 已 commit → ``open``（已埋待回收）；
    - 尚无任何 committed beat → ``planned``（未埋）。
    - ``abandoned`` 不由此推导，仅显式设置。

    set_status=True 时同步写回 thread.status（供调用方直接调用后立即可读）。
    """
    committed = {
        b.type for b in thread.beats if b.exec_status == "committed"
    }
    if committed & _RESOLVING_TYPES:
        status: ThreadStatus = "resolved"
    elif committed & _PROGRESSING_TYPES:
        status = "progressing"
    elif "plant" in committed:
        status = "open"
    else:
        status = "planned"
    if set_status:
        thread.status = status
    return status


def mark_committed(thread: ForesightThread, beat: ForesightBeat, commit_id: str) -> None:
    """把一个规划中的 beat 标记为已落地（committed）并绑定证明链。

    复用「只有 committed 才可带 commit_id」的 model_validator 不变式。
    """
    beat.exec_status = "committed"
    beat.commit_id = commit_id
    ForesightBeat.model_validate(beat)
    derive_status(thread)


class ForesightStore:
    """伏笔状态机持久化（``<项目>/.state/foresight.json``，原子写 tmp+replace）。

    损坏时降级为空；不阻断写作（与「降级不阻断」一致）。
    """

    def __init__(self, project_dir: str | Path) -> None:
        p = Path(project_dir)
        self.file = p / ".state" / "foresight.json"

    def load(self) -> list[ForesightThread]:
        if not self.file.exists():
            return []
        try:
            data = json.loads(self.file.read_text(encoding="utf-8"))
            return [
                ForesightThread.model_validate(t) for t in (data.get("threads") or [])
            ]
        except Exception:  # noqa: BLE001 - 损坏降级空
            return []

    def save(self, threads: list[ForesightThread]) -> None:
        for t in threads:
            derive_status(t)
        try:
            self.file.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.file.with_suffix(".json.tmp")
            tmp.write_text(
                json.dumps(
                    {"threads": [t.model_dump(mode="json") for t in threads]},
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            tmp.replace(self.file)
        except Exception:  # noqa: BLE001
            pass

    def upsert(self, thread: ForesightThread) -> None:
        threads = [t for t in self.load() if t.fid != thread.fid]
        threads.append(thread)
        self.save(threads)


__all__ = [
    "BeatType",
    "Span",
    "BeatExec",
    "ThreadStatus",
    "ForesightBeat",
    "ForesightThread",
    "ForesightStore",
    "derive_status",
    "mark_committed",
]