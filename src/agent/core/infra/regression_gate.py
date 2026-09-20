"""全量回归关卡（G1 · B2 · 2026-09-20）

为什么存在
----------
此前的关卡只有 ``pre-commit`` 里的**架构红线子集**（``pytest tests/architecture``，
约 30s）。**全量回归**（约 19 分钟）只在人记得时手动跑 ⇒ 两类漏网：

1. **回归漏跑**：改了核心链路但没跑全量，缺陷直接进提交；
2. **取证不完整**：跑了但输出被截断 / 超时中断 / 误读退出码，
   得到"看起来绿"的结论——本仓已发生过（缺汇总行、只看 rc 不看 FAILED 行）。

本模块把"全量回归是否可信"变成**可判定的读数**：
    ① 必须存在 pytest **汇总行**（缺 ⇒ 取证不完整，不可信）；
    ② 不允许出现 ``failed`` / ``errors``（看 FAILED 行，不看退出码）；
    ③ 可选：与**记录的基线**比对通过数（低于基线 ⇒ 有测试未被收集/被跳过）；
    ④ 结论必须**绑定 commit**（纪律 #23③：基线必绑 commit）。

纯函数实现，脚本 ``scripts/full_regression.py`` 只是薄壳（便于单测）。
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

#: 汇总行里认可的计数词（pytest 对 0 值 token 会省略，故顺序无关地扫）
_COUNT_WORDS = (
    "passed", "failed", "errors", "skipped", "xfailed", "xpassed",
    "deselected", "warnings",
)

#: 汇总行特征：含 ``in <数字>`` 且含 passed/failed/error
_SUMMARY_HINT = re.compile(r"\bin \d")


def summary_line(text: str) -> str | None:
    """取 pytest 汇总行；找不到返回 ``None``（＝取证不完整）。

    ⚠ ``no tests ran in 0.01s`` 不算汇总行——那是"什么都没跑"，不是"全绿"。
    """
    for line in reversed((text or "").splitlines()):
        s = line.strip()
        if not _SUMMARY_HINT.search(s):
            continue
        if not any(w in s for w in ("passed", "failed", "error")):
            continue
        return s
    return None


def parse_summary(text: str) -> dict[str, int]:
    """解析汇总行的计数；**无汇总行返回空 dict**。"""
    line = summary_line(text)
    if line is None:
        return {}
    out: dict[str, int] = {}
    for m in re.finditer(r"(\d+)\s+([a-z]+)", line):
        word = m.group(2)
        if word in _COUNT_WORDS:
            out[word] = out.get(word, 0) + int(m.group(1))
    return out


@dataclass
class RegressionVerdict:
    """一次回归结果的判定。"""

    ok: bool
    counts: dict[str, int] = field(default_factory=dict)
    summary: str | None = None
    reasons: list[str] = field(default_factory=list)
    commit: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "commit": self.commit,
            "counts": dict(self.counts),
            "summary": self.summary,
            "reasons": list(self.reasons),
        }


def evaluate(
    text: str,
    *,
    commit: str = "",
    baseline: dict[str, int] | None = None,
) -> RegressionVerdict:
    """判定一次全量回归是否可信。

    Args:
        text: pytest 的 stdout（含汇总行）。
        commit: 本次回归对应的 commit sha（纪律 #23③）。
        baseline: 已记录的基线计数（可选）；低于基线通过数 ⇒ 判不可信。
    """
    counts = parse_summary(text)
    line = summary_line(text)
    reasons: list[str] = []
    ok = True

    if line is None:
        return RegressionVerdict(
            ok=False, counts=counts, summary=None, commit=commit,
            reasons=["缺 pytest 汇总行：取证不完整（输出被截断/超时中断？）"],
        )

    if counts.get("failed", 0) > 0 or counts.get("errors", 0) > 0:
        ok = False
        reasons.append(
            f"存在失败：failed={counts.get('failed', 0)} errors={counts.get('errors', 0)}"
            "（看 FAILED 行，不看退出码）"
        )

    if baseline:
        base_passed = int(baseline.get("passed", 0) or 0)
        base_skipped = int(baseline.get("skipped", 0) or 0)
        now_passed = counts.get("passed", 0)
        if now_passed < base_passed:
            ok = False
            reasons.append(
                f"通过数低于基线：{now_passed} < {base_passed}"
                "（可能有测试未被收集/被跳过——不是「更干净」，是漏跑）"
            )
        elif now_passed > base_passed:
            reasons.append(
                f"通过数高于基线：{now_passed} > {base_passed}（新增测试，需确认来源）"
            )
        if counts.get("skipped", 0) > base_skipped:
            reasons.append(
                f"跳过数高于基线：{counts.get('skipped', 0)} > {base_skipped}"
                "（新增 skip 可能是隐性失能）"
            )

    return RegressionVerdict(ok=ok, counts=counts, summary=line, reasons=reasons,
                             commit=commit)


def current_commit(repo_root: Path | str) -> str:
    """取当前 commit 短 sha；失败返回空串（不抛）。"""
    try:
        r = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, encoding="utf-8", timeout=30, check=False,
        )
        return (r.stdout or "").strip() if r.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def record_baseline(path: Path | str, verdict: RegressionVerdict) -> Path:
    """把一次**可信**的回归读数记录为基线（含 commit，纪律 #23③）。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps(
            {"commit": verdict.commit, "counts": verdict.counts,
             "summary": verdict.summary},
            ensure_ascii=False, indent=2,
        ),
        encoding="utf-8",
    )
    return p


def run_pytest(args: Sequence[str], *, cwd: Path | str, timeout: int = 5400) -> tuple[str, int]:
    """跑 pytest 并返回 (stdout, rc)。调用方负责把 stdout 交给 :func:`evaluate`。"""
    r = subprocess.run(
        [sys.executable, "-m", "pytest", *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(cwd), timeout=timeout, check=False,
    )
    return (r.stdout or "") + "\n" + (r.stderr or ""), r.returncode
