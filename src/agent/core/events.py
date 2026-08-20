"""G9 进度事件流（拍板 1/2 + 补充边界 1：全 try/except 不阻断主流程）。

放 core/ 而非 workflows/：事件总线是跨层基础设施（pipeline / m5 / agentic_write /
cli/_shared 均可 import，无循环依赖）；next_steps 映射表也在此（CLI 失败信封复用）。

设计要点：
- ProgressEventBus 是唯一 seq/ts 所有者：任何发射（pipeline 直接发射 / writer 层
  子阶段 partial）都走 bus，保证 seq 单调、elapsed_s 单调递增（PRD 验收）。
- 发射/落盘全 try/except：on_event 回调异常、progress.json 写失败均不阻断主流程。
- elapsed_s（每事件）= 距事件流起点（首事件）的秒数，round int，单调递增；
  chapter_elapsed_s（章内阶段/章完成事件）= 本章内已耗时（ETA 素材，PRD §6-4 口径）。
- progress.json = {"events": [...], "summary": {...}}，tmp + replace 原子写
  （仿 state_machine.save 行 96-106），与 state.json 并存互不覆盖。
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional


def _now_ts() -> str:
    """ISO8601 UTC 时间戳（毫秒精度 + Z 后缀）。"""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _atomic_write_progress(progress_file: Path, events: list[dict], summary: dict) -> None:
    """原子写 progress.json（tmp + replace，仿 state_machine.save 行 96-106）。

    事件数组在内存 append，每次发射全量原子替换（事件数 O(N) 有界，非全量重算）。
    落盘失败静默降级（拍板 3/补充边界 1：不阻断主流程）。

    Args:
        progress_file: progress.json 路径。
        events: 事件数组（内存已 append，此处全量序列化）。
        summary: 运行摘要；中间发射时为 {}，收尾 flush 时写最终摘要。
    """
    try:
        progress_file.parent.mkdir(parents=True, exist_ok=True)
        payload = {"events": events, "summary": summary}
        tmp = progress_file.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(progress_file)
    except Exception:  # noqa: BLE001 - 落盘失败不阻断（G3 哲学）
        pass


class ProgressEventBus:
    """进度事件总线：构造事件（seq/ts/elapsed_s）+ 回调转发 + 原子落盘。

    Args:
        on_event: 订阅回调（CLI 渲染等）；None 表示未订阅（零开销，行为与现状一致）。
        progress_file: progress.json 路径；None 表示不落盘（--no-progress）。
    """

    def __init__(
        self,
        on_event: Callable[[dict[str, Any]], None] | None = None,
        progress_file: str | Path | None = ".state/progress.json",
    ) -> None:
        self.on_event = on_event
        self.progress_file = Path(progress_file) if progress_file else None
        self.events: list[dict[str, Any]] = []
        self.seq = 0
        self._t0 = time.monotonic()
        # 续接既有 progress.json（跨运行可追溯；读失败降级为空列表，不阻断）
        try:
            if self.progress_file and self.progress_file.exists():
                data = json.loads(self.progress_file.read_text(encoding="utf-8"))
                evs = data.get("events", []) or []
                self.events = evs if isinstance(evs, list) else []
                self.seq = max((int(e.get("seq", 0)) for e in self.events), default=0)
        except Exception:  # noqa: BLE001 - 读失败降级空列表
            self.events = []
            self.seq = 0

    def emit(self, type_: str, **fields: Any) -> None:
        """构造并发射一个事件（pipeline 主路径）。全 try/except 不阻断。"""
        try:
            self.seq += 1
            event: dict[str, Any] = {
                "seq": self.seq,
                "type": type_,
                "ts": _now_ts(),
                "elapsed_s": round(time.monotonic() - self._t0),
            }
            event.update(fields)
            self.events.append(event)
            if self.on_event is not None:
                self.on_event(event)  # 回调异常不外抛（下方 except 兜底）
            if self.progress_file is not None:
                _atomic_write_progress(self.progress_file, self.events, {})
        except Exception:  # noqa: BLE001 - 事件发射异常不阻断主流程（拍板 3）
            pass

    def emit_partial(self, partial: dict[str, Any]) -> None:
        """从 writer 层（m5/agentic_write）发射章内子阶段事件：partial 已含
        type/chapter/substage 等字段，本方法补 seq/ts/elapsed_s 后走同一通道。"""
        type_ = str(partial.get("type", "chapter_substage"))
        fields = {k: v for k, v in partial.items() if k != "type"}
        self.emit(type_, **fields)

    def flush(self, summary: dict[str, Any]) -> None:
        """收尾：写最终 summary（done 事件后调用）。"""
        if self.progress_file is not None:
            _atomic_write_progress(self.progress_file, self.events, summary)


def compute_eta_s(
    events: list[dict[str, Any]], target: int, current: int | None = None
) -> Optional[int]:
    """ETA = 已写章平均耗时 × 剩余章数（拍板 4，确定性可离线）。

    素材 = chapter_done 事件的 chapter_elapsed_s（本章实际耗时，从 chapter_start 起）；
    无已完成章（首章）→ None（「预计多久」在第一章完成后才出现）；
    target <= current（已写完）→ 0（不足 1 章按 0）。

    兼容简化调用 compute_eta_s(avg_chapter_elapsed, remaining)：当第一个参数为
    数值时直接视为「平均章耗时」、第二个参数视为「剩余章数」（B1 任务描述口径）。
    """
    # 简化两参调用：compute_eta_s(avg_chapter_elapsed, remaining)
    if isinstance(events, (int, float)) and not isinstance(events, bool) and isinstance(
        target, (int, float)
    ):
        remaining = int(target)
        if remaining <= 0:
            return 0
        return round(float(events) * remaining)

    if current is None:
        return None  # 缺 current 无法按事件口径计算（防御）

    done = [
        e
        for e in events
        if e.get("type") == "chapter_done" and isinstance(e.get("chapter_elapsed_s"), (int, float))
    ]
    if not done or target <= current:
        return 0 if target <= current else None
    avg = sum(float(e["chapter_elapsed_s"]) for e in done) / len(done)
    return round(avg * (target - current))


# step -> 建议命令模板（{dir} 占位符 = 项目目录）；命令全部为真实存在的 CLI 命令
NEXT_STEPS_MAP: dict[str, list[str]] = {
    # step -> 建议命令模板（{dir} 占位符 = 项目目录）；命令全部为真实存在的 CLI 命令
    "plan_block":      ["novel-agent doctor -d {dir}", "novel-agent status -d {dir}"],
    "budget_trip":     ["novel-agent status -d {dir}",
                        "novel-agent autowrite -d {dir} --cost-tier economy"],
    "write_chapter":   ["novel-agent doctor -d {dir}", "novel-agent write -d {dir}"],
    "eval":            ["novel-agent doctor -d {dir}", "novel-agent autowrite -d {dir}"],
    "gate":            ["novel-agent status -d {dir}", "novel-agent write -d {dir}"],
    "adjust":          ["novel-agent doctor -d {dir}", "novel-agent status -d {dir}"],
    "state_parse":     ["novel-agent reset-state -d {dir}"],           # state.json 解析失败
    "draft_residue":   ["novel-agent draft-status -d {dir}", "novel-agent write -d {dir}"],
}


def next_steps_for(step: str, project_dir: str | Path) -> list[str]:
    """确定性建议命令（零 LLM）：映射缺失时回退 doctor + status。

    Args:
        step: 失败步骤名（NEXT_STEPS_MAP 键；未知 step 回退通用建议）。
        project_dir: 项目目录（用于替换 {dir} 占位符）。

    Returns:
        建议命令列表（真实可复制的 CLI 命令）。
    """
    templates = NEXT_STEPS_MAP.get(
        step, ["novel-agent doctor -d {dir}", "novel-agent status -d {dir}"]
    )
    return [t.format(dir=str(project_dir)) for t in templates]


def _fmt_dur(s: int) -> str:
    """格式化耗时（运行摘要人话模式）：h 存在输出 Xh Ym，否则 Xm Ys。"""
    m, sec = divmod(int(s or 0), 60)
    h, m = divmod(m, 60)
    return f"{h}h{m:02d}m" if h else f"{m}m{sec:02d}s"


def build_run_summary(events: list[dict[str, Any]], result: Any = None) -> dict[str, Any]:
    """运行摘要（G9 补充边界 3）：共 X 事件 · N 失败 · 各失败（step/reason/next_steps）· 总耗时。

    纯确定性模板拼装（零 LLM，仿 G7 `_build_summary`/`_SUMMARY_REASONS` 模式）；
    失败条目按事件 seq 顺序输出；无失败输出「无失败」一行。

    Args:
        events: 事件数组（含 failure / done）。
        result: PipelineResult（可选；提供时取 chapters_written 进摘要）。

    Returns:
        摘要 dict：text（人话模板）/ events / failures / failed_steps /
        total_elapsed_s / chapters_written。
    """
    failures = [e for e in events if e.get("type") == "failure"]
    done = [e for e in events if e.get("type") == "done"]
    total_s = 0
    if done:
        total_s = int(done[0].get("total_elapsed_s", 0))
    elif events:
        total_s = int(events[-1].get("elapsed_s", 0))
    lines = [f"运行摘要：共 {len(events)} 事件 · {len(failures)} 失败 · 总耗时 {_fmt_dur(total_s)}"]
    for f in failures:
        lines.append(f"- 失败[{f.get('step', '?')}]（{f.get('severity', 'warn')}）：{f.get('reason', '')}")
        for cmd in f.get("next_steps", []):
            lines.append(f"  下一步：{cmd}")
    if done:
        d = done[0]
        flags = [k for k in ("blocked", "tripped", "escalated") if d.get(k)]
        lines.append("结局：" + ("、".join(flags) if flags else "正常完成"))
    chapters = result.chapters_written if result is not None else None
    return {
        "text": "\n".join(lines),
        "events": len(events),
        "failures": len(failures),
        "failed_steps": [f.get("step") for f in failures],
        "total_elapsed_s": total_s,
        "chapters_written": chapters,
    }
