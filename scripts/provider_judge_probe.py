#!/usr/bin/env python
"""provider 敏感判据抽检探针（薄壳；逻辑全在 ``core/quality/provider_sensitivity.py``）

用法::

    python scripts/provider_judge_probe.py                      # 打印登记表 + 抽检当前 trace
    python scripts/provider_judge_probe.py -d <项目目录>          # 只抽检指定项目
    python scripts/provider_judge_probe.py --json out.json       # 机器可读

★ 硬约束：provider 数 < 2 时**只输出"未验证"**，不给出任何"稳健/不稳健"结论
—— 单 provider 数据上的一切"跨 provider 结论"都是假结论。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

NOVELS = REPO_ROOT.parent / "novels"


def _collect_spans(project: str) -> list[dict]:
    from agent.core.llmops.trace import TraceStore

    rows: list[dict] = []
    targets = [Path(project)] if project else [
        p for p in sorted(NOVELS.iterdir()) if (p / ".state" / "llmops" / "trace.jsonl").is_file()
    ]
    for proj in targets:
        try:
            store = TraceStore(proj)
        except Exception as e:  # noqa: BLE001 - 读取失败显性报出，不静默
            print(f"⚠ {proj}: trace 读取失败：{e}", file=sys.stderr)
            continue
        for s in store.spans():
            d = s.to_dict()
            d["_project"] = proj.name
            rows.append(d)
    return rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="provider 敏感判据抽检")
    ap.add_argument("-d", "--project", default="", help="只抽检该项目目录")
    ap.add_argument("--json", default="", help="把结果写到该 JSON 路径")
    ns = ap.parse_args(argv)

    from agent.core.quality.provider_sensitivity import (
        JUDGES,
        by_source,
        llm_absolute_judges,
        probe_provider_shift,
        registry_path_valid,
    )
    from agent.core.llmops.trace import find_duplicate_pairs

    src_root = REPO_ROOT / "src" / "agent"

    print("=" * 74)
    print("provider 敏感判据登记表")
    print("=" * 74)
    for source, items in sorted(by_source().items()):
        print(f"\n[{source}]")
        for j in items:
            flag = "⚠ 敏感" if j.provider_sensitive else "✓ 无关"
            rel = "已相对化" if j.relative else "绝对阈值"
            ok, why = registry_path_valid(j.location, src_root)
            mark = "" if ok else f"  ❌僵尸({why})"
            print(f"  {flag:6s} {rel:6s} {j.key:<26s} {j.label}{mark}")

    risk = llm_absolute_judges()
    print()
    print(f"★ 风险面（LLM 分值 + 绝对阈值 + 未相对化）：{len(risk)} 条")
    for j in risk:
        print(f"  · {j.key}（{j.location.split('::')[-1]}）")

    spans = _collect_spans(ns.project)
    dup = find_duplicate_pairs(spans)
    print()
    print("=" * 74)
    print(f"抽检样本：{len(spans)} 条 span（去重对 {len(dup)}）")
    print("=" * 74)
    if dup:
        print(f"⚠ trace 完整性：{len(dup)} 对重复 span ⇒ 下方读数被虚增，先修 trace 再看位移")
    report = probe_provider_shift(spans)
    for prov, v in sorted(report["by_provider"].items()):
        print(f"  {prov:<12} calls={v['calls']:>5}  "
              f"in p50={v['tokens_in_p50']} p90={v['tokens_in_p90']}  "
              f"out p50={v['tokens_out_p50']} p90={v['tokens_out_p90']}")
    print()
    print(f"可比较：{report['comparable']}")
    print(f"结论：{report['verdict']}")
    print(f"分值类判据可比较：{report['score_judges_comparable']}")
    print(f"  └ {report['score_judges_blocker']}")

    if ns.json:
        payload = {
            "judges": [j.__dict__ for j in JUDGES],
            "risk_surface": [j.key for j in risk],
            "duplicate_pairs": len(dup),
            "probe": report,
        }
        Path(ns.json).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"\n已写：{ns.json}")

    # advisory：本探针**不作为关卡**（无阈值型判据；升为关卡须另立登记单）
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
