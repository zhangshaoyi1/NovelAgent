"""连续性账本 · 持久化 + 归档（G15 P0-1）

位置：``<项目>/.state/continuity/ledger.json``（原子写 tmp+replace，损坏时降级为空账本，
贯彻「降级不阻断」）。

核心操作：
- ``load``：读账本（失败 → 空账本 + 置位 need_init）。
- ``save``：原子落盘。
- ``commit``：把一章产生的 deltas（facts/knowledge/open_loops/handoff）合并进账本，
  并落盘；返回 commit_id。
"""

from __future__ import annotations

import json
from pathlib import Path

from agent.core.base.validation import validate_model
from agent.core.continuity.models import (
    ContinuityFact,
    ContinuityHandoff,
    ContinuityKnowledge,
    ContinuityOpenLoop,
    ContinuityLedger,
)

_DEFAULT_LEDGER_FILE = Path(".state/continuity/ledger.json")


class ContinuityLedgerStore:
    """账本存储与归档。"""

    def __init__(self, project_dir: str | Path, file: str | Path | None = None) -> None:
        self.project_dir = Path(project_dir)
        self.file = self.project_dir / (file or _DEFAULT_LEDGER_FILE)
        self.ledger = ContinuityLedger()
        self._loaded = False

    # ---------------- 读写 ----------------
    def load(self) -> ContinuityLedger:
        """读取账本；文件缺失/损坏 → 空账本（降级不阻断）。"""
        self._loaded = True
        if not self.file.exists():
            self.ledger = ContinuityLedger()
            return self.ledger
        try:
            data = json.loads(self.file.read_text(encoding="utf-8"))
            ok, msg, model = validate_model(ContinuityLedger, data)
            self.ledger = model if ok and model is not None else ContinuityLedger()
            if not ok:
                # 损坏账本：备份后落到空账本，避免持续性脏数据
                bak = self.file.with_suffix(".json.bak")
                try:
                    self.file.replace(bak)
                except Exception:
                    pass
        except Exception:  # noqa: BLE001 - 读失败降级空账本
            self.ledger = ContinuityLedger()
        return self.ledger

    def save(self) -> None:
        """原子写账本。落盘失败静默降级（不阻断主流程）。"""
        try:
            self.file.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.file.with_suffix(".json.tmp")
            tmp.write_text(
                json.dumps(
                    self.ledger.model_dump(mode="json"),
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            tmp.replace(self.file)
        except Exception:  # noqa: BLE001
            pass

    # ---------------- 归档 ----------------
    def commit(
        self,
        *,
        chapter: int,
        facts: list[ContinuityFact],
        knowledge: list[ContinuityKnowledge],
        open_loops: list[ContinuityOpenLoop],
        handoff: ContinuityHandoff,
    ) -> str:
        """把一章增量合并进账本并按章归档。返回 commit_id（= 章号）。

        facts 按 ``(domain, subject_id, field)`` 覆盖更新；open_loops 按 loop_id
        合并；handoff 覆盖该章（保持按章有序）。
        """
        if not self._loaded:
            self.load()
        commit_id = str(chapter)

        # facts 覆盖更新（保持键唯一）
        index = {(f.domain, f.subject_id, f.field): i for i, f in enumerate(self.ledger.facts)}
        for f in facts:
            key = (f.domain, f.subject_id, f.field)
            if key in index:
                self.ledger.facts[index[key]] = f
            else:
                self.ledger.facts.append(f)
                index[key] = len(self.ledger.facts) - 1

        # knowledge 覆盖更新（subject_id + audience + audience_id）
        k_index = {
            (k.subject_id, k.audience, k.audience_id): i
            for i, k in enumerate(self.ledger.knowledge)
        }
        for k in knowledge:
            key = (k.subject_id, k.audience, k.audience_id)
            if key in k_index:
                self.ledger.knowledge[k_index[key]] = k
            else:
                self.ledger.knowledge.append(k)
                k_index[key] = len(self.ledger.knowledge) - 1

        # open_loops 合并（同 id 覆盖，新增追加）
        loop_index = {lo.loop_id: i for i, lo in enumerate(self.ledger.open_loops)}
        for lo in open_loops:
            if lo.loop_id in loop_index:
                self.ledger.open_loops[loop_index[lo.loop_id]] = lo
            else:
                self.ledger.open_loops.append(lo)
                loop_index[lo.loop_id] = len(self.ledger.open_loops) - 1

        # handoff 校验通过后覆盖该章
        ok, msg, hmodel = validate_model(ContinuityHandoff, handoff)
        if not ok or hmodel is None:
            raise ValueError(f"handoff 校验失败: {msg}")
        self.ledger.handoffs = [
            h for h in self.ledger.handoffs if h.chapter != chapter
        ]
        self.ledger.handoffs.append(hmodel)
        self.ledger.handoffs.sort(key=lambda h: h.chapter)

        # 触发唯一性校验（构建重建会执行 model_validator）
        self.ledger = ContinuityLedger.model_validate(
            self.ledger.model_dump(mode="json")
        )
        self.save()
        return commit_id

    # ---------------- 查询 ----------------
    def initialised(self) -> bool:
        return self._loaded

    def has_any(self) -> bool:
        return bool(
            self.ledger.facts
            or self.ledger.knowledge
            or self.ledger.open_loops
            or self.ledger.handoffs
        )

    def last_commit_chapter(self) -> int | None:
        return self.ledger.latest_handoff().chapter if self.ledger.latest_handoff() else None


__all__ = ["ContinuityLedgerStore"]