#!/usr/bin/env python
"""全量回归关卡（G1 · B2）：跑全量 → 判定可信度 → 记录基线（绑 commit）。

用法（在 agent/ 目录下）：
    python scripts/full_regression.py                    # 跑全量并判定
    python scripts/full_regression.py --baseline .state/regression_baseline.json
    python scripts/full_regression.py --record-baseline  # 判定通过后把读数记为基线
    python scripts/full_regression.py --paths tests/architecture   # 只跑子集

退出码：
    0 = 判定可信（有汇总行、无 failed/errors、不低基线）
    1 = 判定不可信（缺汇总行 / 有失败 / 低于基线）——**不因 rc 侥幸放过**

为什么是脚本而不是塞进 pre-commit：全量约 19 分钟，进 pre-commit 会毁掉提交体验；
本关卡的正确用法是"提交前手动/CI 跑一次，并把读数与 commit 一起记录"
（AGENTS.md 第 13 条：失败先归因）。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from agent.core.infra.regression_gate import (  # noqa: E402
    current_commit,
    evaluate,
    record_baseline,
    run_pytest,
)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="全量回归关卡（G1/B2）")
    ap.add_argument("--paths", nargs="*", default=None, help="只跑指定路径（默认全量）")
    ap.add_argument("--baseline", default="", help="已记录的基线 JSON 路径（可选）")
    ap.add_argument("--record-baseline", action="store_true",
                    help="判定通过时把本次读数写回 --baseline 指定的文件")
    ap.add_argument("--report", default="", help="把判定结果 JSON 写到该路径")
    ap.add_argument("--timeout", type=int, default=5400, help="pytest 超时秒数")
    ns = ap.parse_args(argv)

    os.environ.setdefault("CODEBUDDY_SAFE_DELETE_ENABLED", "0")
    args = list(ns.paths or []) + ["-q", "--tb=short"]

    print(f"[full-regression] cwd={REPO_ROOT} args={' '.join(args)}", flush=True)
    stdout, rc = run_pytest(args, cwd=REPO_ROOT, timeout=ns.timeout)

    baseline = None
    if ns.baseline and Path(ns.baseline).exists():
        try:
            raw = json.loads(Path(ns.baseline).read_text(encoding="utf-8"))
            baseline = raw.get("counts") if isinstance(raw, dict) else None
        except (OSError, ValueError):
            baseline = None

    verdict = evaluate(stdout, commit=current_commit(REPO_ROOT), baseline=baseline)

    print(f"[full-regression] 汇总行：{verdict.summary}")
    print(f"[full-regression] 计数：{verdict.counts}  rc={rc}  commit={verdict.commit}")
    for r in verdict.reasons:
        print(f"[full-regression] · {r}")
    print(f"[full-regression] 判定：{'可信 ✅' if verdict.ok else '不可信 ❌'}")

    if ns.report:
        p = Path(ns.report)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(verdict.to_dict(), ensure_ascii=False, indent=2),
                     encoding="utf-8")
        print(f"[full-regression] 报告已写：{p}")

    if verdict.ok and ns.record_baseline and ns.baseline:
        print(f"[full-regression] 基线已记录：{record_baseline(ns.baseline, verdict)}")

    return 0 if verdict.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
