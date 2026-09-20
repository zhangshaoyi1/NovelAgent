#!/usr/bin/env python
"""「计数器/账本 → 动作」链条普查（纪律 #31 · B1）。

用法（在 agent/ 目录下）：
    python scripts/audit_counter_action_chains.py            # 打印候选与未复核清单
    python scripts/audit_counter_action_chains.py --json out.json
    python scripts/audit_counter_action_chains.py --fail-on-unreviewed   # 用作 CI 关卡

语义：**普查工具，不做判定**。判据是名字启发式，目标是"不漏"（纪律 #28：
误报远多于真阳性）。复核结论记进 ``counter_action_audit.KNOWN_CHAINS``，
未复核数的下降即收敛证据。退出码默认 0（advisory）；加 ``--fail-on-unreviewed``
才变成关卡（纪律 #20：闸门强度与证据匹配——普查阶段不宜硬拦）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src" / "agent"
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from agent.core.infra.counter_action_audit import (  # noqa: E402
    KNOWN_CHAINS, scan_chains,
)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="计数器/账本→动作 链条普查（#31/B1）")
    ap.add_argument("--json", default="", help="把结果写到该 JSON 路径")
    ap.add_argument("--fail-on-unreviewed", action="store_true",
                    help="存在未复核候选时返回 1（默认 advisory）")
    ns = ap.parse_args(argv)

    chains = []
    for p in sorted(SRC.rglob("*.py")):
        rel = p.relative_to(SRC).as_posix()
        chains.extend(scan_chains(p.read_text(encoding="utf-8", errors="replace"), rel))

    known = [c for c in chains if c.key in KNOWN_CHAINS]
    unreviewed = [c for c in chains if c.key not in KNOWN_CHAINS]
    zombies = sorted(k for k in KNOWN_CHAINS if k not in {c.key for c in chains})

    print(f"[counter-audit] 候选 {len(chains)}｜已复核 {len(known)}｜未复核 {len(unreviewed)}")
    if unreviewed:
        print("[counter-audit] 未复核（需人读代码后写进 KNOWN_CHAINS）：")
        for c in unreviewed:
            print(f"  · {c.key}  自增={c.increments} 阈值={c.thresholds}")
    if zombies:
        print("[counter-audit] ⚠ 台账僵尸条目（已扫不到，请删除或更正）：")
        for z in zombies:
            print(f"  · {z}")

    if ns.json:
        out = Path(ns.json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(
            {
                "candidates": [
                    {"key": c.key, "increments": c.increments,
                     "thresholds": c.thresholds, "reviewed": c.key in KNOWN_CHAINS}
                    for c in chains
                ],
                "unreviewed": [c.key for c in unreviewed],
                "zombies": zombies,
            },
            ensure_ascii=False, indent=2,
        ), encoding="utf-8")
        print(f"[counter-audit] 报告已写：{out}")

    return 1 if (ns.fail_on_unreviewed and unreviewed) else 0


if __name__ == "__main__":
    raise SystemExit(main())
