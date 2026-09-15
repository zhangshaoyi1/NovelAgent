#!/usr/bin/env python
"""基线差异归因：一条命令给出「当前工作区 vs 指定基线」的测试失败差集。

为什么存在（AGENTS.md 第 13 条「失败必须先归因再动手」）：
    看到测试失败时，第一件事是判定「这是不是我引入的」。手工做法是
    ``git archive <父sha> | tar -x -C $(mktemp -d)`` 再跑一遍对照 ——
    2026-09-15 实测要 3~4 轮工具调用才能判明归属，且容易写错。

用法（在 agent/ 目录下）：
    python scripts/baseline_diff.py --base e37c679
    python scripts/baseline_diff.py --base e37c679 --paths tests/architecture tests/core
    python scripts/baseline_diff.py --base HEAD --keep      # HEAD=只看工作区改动
    python scripts/baseline_diff.py --base e37c679 -k "quality or gate"

输出三分类：
    ① 仅当前失败   → 很可能是**本次改动引入**（REGRESSION，要处理）
    ② 仅基线失败   → 已被本次改动**修好**（FIXED，好消息但需确认非偶发）
    ③ 两侧都失败   → **存量失败**（与本次无关，别背锅；但应登记）

实现要点（都来自踩坑）：
- 用 ``--junitxml`` 解析 XML 判定失败，**不解析 stdout 文本**（文本口径易漂）。
- 基线快照用 ``git archive`` + Python ``tarfile`` 解包，**不依赖 tar 二进制**，
  也不用 ``git worktree``（避免污染 .git/worktrees 与 linked worktree 状态）。
- 两侧都带 ``CODEBUDDY_SAFE_DELETE_ENABLED=0``（safe-delete 护栏会拦长测）。
- 判定看失败用例**清单**，不看 pytest 退出码。
"""

from __future__ import annotations

import argparse
import io
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def run_pytest(cwd: Path, paths: list[str], kexpr: str | None, xml: Path, extra: list[str]) -> set[str]:
    """跑 pytest，返回失败/错误用例 id 集合（从 junit XML 解析）。"""
    cmd = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", f"--junitxml={xml}"]
    if kexpr:
        cmd += ["-k", kexpr]
    cmd += list(paths) + list(extra)

    env = dict(os.environ, CODEBUDDY_SAFE_DELETE_ENABLED="0")
    proc = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, encoding="utf-8")
    sys.stderr.write(f"    [pytest] cwd={cwd} rc={proc.returncode}\n")

    if not xml.exists():
        sys.stderr.write("    [warn] 未生成 junit xml，无法判定（检查 pytest 是否可用）\n")
        return set()

    failed: set[str] = set()
    for tc in ET.parse(xml).getroot().iter("testcase"):
        if tc.find("failure") is not None or tc.find("error") is not None:
            cls = tc.get("classname") or ""
            failed.add(f"{cls}::{tc.get('name')}" if cls else (tc.get("name") or "?"))
    return failed


def snapshot(base: str, dest: Path) -> None:
    """把基线提交导出到 dest（git archive + Python tarfile，不依赖 tar 二进制）。"""
    proc = subprocess.run(
        ["git", "archive", "--format=tar", base],
        cwd=REPO,
        capture_output=True,
    )
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", "replace")[:300]
        sys.exit(f"✗ git archive {base} 失败：{err}\n  （base 是否为本仓的有效 sha？）")
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(proc.stdout)) as tf:
        tf.extractall(dest)


def short(s: str) -> str:
    return s.split("::")[-1]


def main() -> int:
    ap = argparse.ArgumentParser(description="当前工作区 vs 基线的测试失败差集")
    ap.add_argument("--base", required=True, help="基线 commit（也接受 HEAD / 分支名）")
    ap.add_argument("--paths", nargs="*", default=[], help="限定测试路径（默认全量）")
    ap.add_argument("-k", dest="kexpr", default=None, help="pytest -k 表达式")
    ap.add_argument("--keep", action="store_true", help="保留基线快照目录以便复查")
    ap.add_argument("extra", nargs="*", help="透传给 pytest 的额外参数")
    args = ap.parse_args()

    base_sha = subprocess.run(
        ["git", "rev-parse", "--short", args.base], cwd=REPO, capture_output=True, text=True
    ).stdout.strip()
    head_sha = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"], cwd=REPO, capture_output=True, text=True
    ).stdout.strip()
    if not base_sha:
        sys.exit(f"✗ 无法解析 --base {args.base}")

    tmp = Path(tempfile.mkdtemp(prefix="na_baseline_"))
    try:
        snap = tmp / "base"
        print(f"== 快照基线 {base_sha} → {snap}")
        snapshot(base_sha, snap)
        print(f"== 跑基线（{args.paths or ['全量']}, base={base_sha}）")
        base_fail = run_pytest(snap, args.paths, args.kexpr, tmp / "base.xml", args.extra)

        print(f"== 跑当前工作区（HEAD={head_sha} + 未提交改动）")
        cur_fail = run_pytest(REPO, args.paths, args.kexpr, tmp / "cur.xml", args.extra)

        only_cur = cur_fail - base_fail
        only_base = base_fail - cur_fail
        both = cur_fail & base_fail

        print()
        print("=" * 74)
        print(f"基线 {base_sha}: 失败 {len(base_fail)}   当前 {head_sha}: 失败 {len(cur_fail)}")
        print("=" * 74)
        print(f"① 仅当前失败（可能是你引入的 REGRESSION）: {len(only_cur)}")
        for t in sorted(only_cur):
            print(f"   ✗ {t}")
        print(f"② 仅基线失败（已被修好 / 或本次引入的偶发）: {len(only_base)}")
        for t in sorted(only_base):
            print(f"   ✓ {t}")
        print(f"③ 两侧都失败（存量，与本次无关）: {len(both)}")
        for t in sorted(both):
            print(f"   = {t}")

        print()
        if only_cur:
            print("结论：存在 REGRESSION —— 先归因再改码（AGENTS.md 第 13 条）。")
            rc = 1
        else:
            print("结论：无新增失败。若当前仍有失败，均为存量（可另立登记单清偿）。")
            rc = 0
        if only_base:
            print(f"注意：{len(only_base)} 个用例在基线上失败、当前通过 —— 重跑确认非偶发。")
        return rc
    finally:
        if args.keep:
            print(f"\n（保留快照目录：{tmp}）")
        else:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
