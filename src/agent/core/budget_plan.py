"""预算计划配置（G10 P1-1，拍板 6）：.state/budget.json 三态加载。

放 core/ 而非 cli/：配置加载是跨层基础设施（autowrite CLI 消费；后续 cost-plan/
报告可复用），无循环依赖。仿 load_guardrail_config（guardrails.py 369-407）哲学：
文件缺失/损坏一律降级默认，全 try/except 不阻断。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

DEFAULT_BUDGET_PLAN: dict[str, Any] = {
    "tier": "balanced",
    "budget_margin": 1.0,
    "auto_downgrade": True,
    "hard_limit_tokens": None,
}

_KNOWN_KEYS = ("tier", "budget_margin", "auto_downgrade", "hard_limit_tokens")


def load_budget_plan(path: str | Path | None = None) -> dict[str, Any]:
    """三态加载：缺失/损坏 → 默认；有效 → 合并（只取已知四键，忽略未知键）。

    - tier 仅接受 economy/balanced/quality；
    - budget_margin 需 > 0 数值；
    - auto_downgrade 需 bool；
    - hard_limit_tokens 接受 None 或 > 0 数值（**本期仅回显不参与判定**，§13 待确认 4）。
    任何异常/非法值 → 该键保持默认，绝不抛错（补充边界 3）。

    Args:
        path: budget.json 路径；None/缺失/损坏 → 返回默认配置。

    Returns:
        预算计划 dict（始终含 DEFAULT_BUDGET_PLAN 四键）。
    """
    cfg: dict[str, Any] = dict(DEFAULT_BUDGET_PLAN)
    if path is None:
        return cfg
    p = Path(path)
    if not p.exists():
        return cfg
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - 损坏降级默认
        return cfg
    if not isinstance(raw, dict):
        return cfg
    if raw.get("tier") in ("economy", "balanced", "quality"):
        cfg["tier"] = raw["tier"]
    if isinstance(raw.get("budget_margin"), (int, float)) and float(raw["budget_margin"]) > 0:
        cfg["budget_margin"] = float(raw["budget_margin"])
    if isinstance(raw.get("auto_downgrade"), bool):
        cfg["auto_downgrade"] = raw["auto_downgrade"]
    if raw.get("hard_limit_tokens") is None or (
        isinstance(raw.get("hard_limit_tokens"), (int, float))
        and float(raw["hard_limit_tokens"]) > 0
    ):
        cfg["hard_limit_tokens"] = raw["hard_limit_tokens"]
    return cfg
