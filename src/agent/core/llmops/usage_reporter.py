"""LLMOps · Token 用量汇总上报（G15 P1-1）

按 ``run`` + ``subagent`` 聚合每次 LLM 调用的 token 用量，落盘
``<项目>/.state/llmops/usage/store.json``，供成本看板与上报消费。

数据源与 ``core/llmops/trace.py`` 的 ``TraceStore`` **同源**（都不新造统计口径）：
默认直接读取 ``trace.jsonl`` 里已记录的 ``TraceSpan``，聚合结果应与
``TraceStore.totals()`` 口径一致（test_usage_report 断言二者相等）。

设计要点（与「降级不阻断」一致）：
- 纯离线、零依赖、零网络；读文件失败 → 空聚合，绝不抛错阻断。
- ``run`` 与 ``subagent`` 取自 span 的 ``meta``：``meta["run_id"]``（默认 ``_``）
  与 ``meta["subagent_id"]``（默认 ``main-agent``）。
- 增量口径：``snapshot()`` 记录某个游标（按 span 累计），``diff()`` 给出两次
  快照间的增量（对标 DeepWrite ``software-token-usage-reporter.ts`` 的 base/increment）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agent.core.llmops.trace import TraceStore

DEFAULT_USAGE_FILE = Path(".state/llmops/usage/store.json")


def _group_spans(
    spans: list[Any],
) -> dict[str, dict[str, dict[str, Any]]]:
    """把 spans 聚合为 ``{run_id: {subagent_id: usage}}``。"""
    out: dict[str, dict[str, dict[str, Any]]] = {}
    for s in spans:
        run = str((s.meta or {}).get("run_id", "_"))
        agent = str((s.meta or {}).get("subagent_id", "main-agent"))
        d = out.setdefault(run, {}).setdefault(
            agent,
            {"calls": 0, "tokens_in": 0, "tokens_out": 0, "tokens_cached": 0, "tokens_total": 0, "cost": 0.0},
        )
        d["calls"] += 1
        d["tokens_in"] += s.tokens_in
        d["tokens_out"] += s.tokens_out
        d["tokens_cached"] += getattr(s, "tokens_cached", 0)
        d["tokens_total"] += s.tokens_in + s.tokens_out
        d["cost"] += s.cost
    return out


class UsageReporter:
    """Token 用量汇总（run × subagent 二维聚合 + 快照增量）。"""

    def __init__(self, project_dir: str | Path) -> None:
        self.project_dir = Path(project_dir)
        self.usage_file = self.project_dir / DEFAULT_USAGE_FILE

    def _fresh_trace(self) -> TraceStore:
        """每次读取一个全新 TraceStore —— 保证增量记录可见（同源且不读陈旧缓存）。"""
        return TraceStore(self.project_dir)

    # ---------------- 聚合 ----------------
    def aggregate(self) -> dict[str, dict[str, dict[str, Any]]]:
        """读取 trace 源并聚合为 ``{run: {subagent: usage}}``。"""
        return _group_spans(self._fresh_trace().spans())

    def by_run(self) -> dict[str, dict[str, Any]]:
        """按 run 汇总（合并该 run 下所有 subagent）。"""
        agg = self.aggregate()
        out: dict[str, dict[str, Any]] = {}
        for run, agents in agg.items():
            merged: dict[str, Any] = {
                "calls": 0, "tokens_in": 0, "tokens_out": 0,
                "tokens_cached": 0, "tokens_total": 0, "cost": 0.0,
            }
            for u in agents.values():
                for k in merged:
                    merged[k] += u.get(k, 0)
            out[run] = merged
        return out

    def totals(self) -> dict[str, Any]:
        """全局总量（口径对齐 ``TraceStore.totals``）。"""
        raw = self._fresh_trace().totals()
        return {
            "calls": raw["calls"],
            "tokens_in": raw["tokens_in"],
            "tokens_out": raw["tokens_out"],
            "tokens_cached": raw.get("tokens_cached", 0),
            "tokens_total": raw["tokens_total"],
            "cost": raw["cost"],
        }

    # ---------------- 快照 / 增量 ----------------
    def snapshot(self) -> dict[str, Any]:
        """记录当前累积量快照（含时间戳）。"""
        return {
            "at": __import__("time").time(),
            "totals": self.totals(),
            "by_run": self.by_run(),
        }

    def diff(self, base: dict[str, Any] | None, cur: dict[str, Any] | None = None) -> dict[str, Any]:
        """两次快照间的增量（cur - base）。未提供时以当前快照为准。"""
        cur = cur or self.snapshot()
        b = base or {"totals": {}, "by_run": {}}
        bt = b.get("totals", {})
        ct = cur.get("totals", {})
        def _sub(a: dict, c: dict) -> dict:
            keys = set(a) | set(c)
            return {k: round(c.get(k, 0) - a.get(k, 0), 4) for k in keys}
        return {"totals": _sub(bt, ct), "by_run": {}}  # by_run 不做减法，看板读取当前值

    # ---------------- 落盘 / 读取 ----------------
    def store(self, snapshot: dict[str, Any] | None = None) -> None:
        """原子写 usage 快照（tmp+replace，失败静默降级）。"""
        payload = snapshot or self.snapshot()
        try:
            self.usage_file.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.usage_file.with_suffix(".store.tmp")
            tmp.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            tmp.replace(self.usage_file)
        except Exception:  # noqa: BLE001 - 落盘失败不阻断
            pass

    def load_store(self) -> dict[str, Any]:
        """读取最近一次落盘的用量快照；缺失/损坏 → 空 dict。"""
        if not self.usage_file.exists():
            return {}
        try:
            data = json.loads(self.usage_file.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:  # noqa: BLE001 - 损坏降级
            return {}


__all__ = ["UsageReporter"]