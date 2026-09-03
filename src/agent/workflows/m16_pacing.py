"""追读力抽取与对账（增量 C / T04，M16）

``PacingTracker``：
- ``extract(chapter_text)``：用 LLM 从单章抽取 钩子(Hook) / 爽点(CoolPoint) /
  微 payoff(MicroPayoff) / 债务(Debt)。LLM 不可用时返回空抽取（降级）。
- ``reconcile(open_debts, new_extraction)``：把本章新债务并入账本、记录爽点密度，
  返回更新后的 ``Ledger``（由调用方落盘，遵循「缺账本则空、不阻断」）。
"""

from __future__ import annotations

from agent.core.infra.prompt_manager import pm
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent.client.gateway_adapter import create_gateway, chat_utility
from llmagent.gateway import Gateway
from agent.core.story.pacing_store import Debt, Ledger, PacingStore
from agent.core.engine.workflow_registry import workflow
from agent.utils import parse_llm_json


@dataclass
class PacingExtraction:
    """单章追读力抽取结果"""

    hooks: list[str] = field(default_factory=list)
    cool_points: list[str] = field(default_factory=list)
    micro_payoffs: list[str] = field(default_factory=list)
    debts: list[Debt] = field(default_factory=list)


@workflow("m16_pacing")
class PacingTracker:
    """追读力抽取与对账器（C）"""

    def __init__(self, project_dir: Path, llm: Gateway | None = None) -> None:
        self.project_dir = Path(project_dir)
        self.llm = llm or create_gateway()
        self.store = PacingStore(self.project_dir)

    # ============================================================
    # 抽取
    # ============================================================
    def extract(self, chapter_text: str) -> PacingExtraction:
        """用 LLM 抽取本章追读力要素

        LLM 不可用 / 调用异常时返回空抽取（降级，不阻断）。
        """
        if self.llm is None:
            return PacingExtraction()
        user = pm.get("m16.pacing").render_user(chapter_text=chapter_text)
        try:
            resp = chat_utility(
                self.llm,
                [
                    {"role": "system", "content": pm.get("m16.pacing").system},
                    {"role": "user", "content": user},
                ],
                max_tokens=1500,
                enable_thinking=False,
            )
            data = parse_llm_json(resp)
        except Exception:  # noqa: BLE001 - 抽取失败降级为空
            return PacingExtraction()
        return self._parse(data)

    @staticmethod
    def _parse(data: dict[str, Any]) -> PacingExtraction:
        debts: list[Debt] = []
        for d in data.get("debts") or []:
            if not isinstance(d, dict):
                continue
            debts.append(
                Debt(
                    id=str(d.get("id", "") or f"D-{len(debts) + 1}"),
                    desc=str(d.get("desc", "")),
                    kind=str(d.get("kind", "general")),
                    planted_ch=int(d.get("planted_ch", 0) or 0),
                    status=str(d.get("status", "open")),
                )
            )
        return PacingExtraction(
            hooks=[str(x) for x in (data.get("hooks") or [])],
            cool_points=[str(x) for x in (data.get("cool_points") or [])],
            micro_payoffs=[str(x) for x in (data.get("micro_payoffs") or [])],
            debts=debts,
        )

    # ============================================================
    # 对账
    # ============================================================
    def reconcile(self, open_debts: list[Debt], new_extraction: PacingExtraction) -> Ledger:
        """把本章抽取结果并入账本

        Args:
            open_debts: 调用前已有的开放债务（用于去重参照）
            new_extraction: 本章抽取结果

        Returns:
            更新后的账本（尚未落盘；调用方负责 store.save）。
        """
        ledger = self.store.load()
        existing_ids = {d.id for d in ledger.open_debts} | {
            d.id for d in (open_debts or [])
        }
        for d in new_extraction.debts:
            if d.id and d.id not in existing_ids:
                ledger.open_debts.append(d)
                existing_ids.add(d.id)
        # 记录爽点密度（用于趋势观测）
        ledger.cool_density.append(float(len(new_extraction.cool_points)))
        return ledger
