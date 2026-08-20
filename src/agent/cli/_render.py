"""G9 CLI 渲染层（纯展示，零判定；不参与熔断/回溯/门禁）。

三件事：逐段拼接渲染（模拟增量）/ 单行进度条 / 事件行 + 运行摘要。
所有渲染异常静默退化（整块打印 / 跳过），不阻断主流程（拍板 3）。
"""

from __future__ import annotations

import sys
import time
from typing import Any

from rich.console import Console


def _fmt_clock(s: int) -> str:
    """格式化耗时（进度条/事件行用）：Xm Ys / Xh Ym。"""
    m, sec = divmod(int(s or 0), 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}h{m:02d}m"
    return f"{m}m{sec:02d}s"


class RenderStreamer:
    """逐段拼接渲染器：把整章文本按段落/约 200 字一批模拟增量打印。

    拍板 3 要点：
    - LLM 仍整块返回（R3），本层按段切分逐批输出，模拟「逐段可见」；
    - 渲染节流 ≥1s 采用**跳过式**（interval 未到则本批并入下一批，绝不 sleep），
      避免长任务被渲染拖慢（PRD §7 风险 4）；终端渲染极快时自然退化为整块输出；
    - 异常 → 静默退化整块打印（不报错、不崩、仍完成整章）。
    """

    def __init__(
        self,
        console: Any | None = None,
        *,
        min_batch_chars: int = 200,
        throttle_s: float = 1.0,
    ) -> None:
        self.console = console or Console()
        self.min_batch_chars = max(1, int(min_batch_chars))
        self.throttle_s = max(0.0, float(throttle_s))

    def _write(self, text: str, *, end: str = "\n", sink: Any = None) -> None:
        """写一段文本：默认走 rich Console（markup=False 防解析）；可注入 sink 供测试。"""
        if sink is not None:
            sink(text, end=end)
        else:
            self.console.print(text, markup=False, end=end)

    def stream_text(
        self,
        text: str,
        *,
        batch_chars: int | None = None,
        min_interval_s: float | None = None,
        sink: Any = None,
    ) -> dict[str, Any]:
        """输出整章文本（批次顺序 == 原文分段顺序；拼接 == 原文）。

        Args:
            text: 整章正文（LLM 整块返回）。
            batch_chars: 单批字数上限（默认取构造 min_batch_chars，约 200）。
            min_interval_s: 节流间隔（默认取构造 throttle_s，1s；跳过式不 sleep）。
            sink: 输出回调（测试注入）；None 走 self.console.print（stdout）。

        Returns:
            渲染元信息：{"mode": "stream"|"block", "batches": N, "chars": M}。
        """
        try:
            batch = int(batch_chars or self.min_batch_chars)
            interval = float(min_interval_s) if min_interval_s is not None else self.throttle_s
            paragraphs = [p for p in text.split("\n\n") if p.strip()]
            buf = ""
            last_ts = 0.0
            batches = 0
            for p in paragraphs:
                buf = (buf + "\n\n" + p) if buf else p
                while len(buf) >= batch:
                    now = time.monotonic()
                    if now - last_ts < interval:
                        break  # 跳过式节流：不足 1s 并入下批（绝不 sleep）
                    chunk, buf = buf[:batch], buf[batch:]
                    self._write(chunk, end="", sink=sink)
                    last_ts = now
                    batches += 1
            if buf:
                self._write(buf, end="\n", sink=sink)
                batches += 1
            return {"mode": "stream", "batches": batches, "chars": len(text)}
        except Exception:  # noqa: BLE001 - 渲染异常退化整块（拍板 3）
            try:
                self._write(text, end="\n", sink=sink)
            except Exception:  # noqa: BLE001 - 退化打印再失败也不抛
                pass
            return {"mode": "block", "batches": 1, "chars": len(text)}

    def progress_line(
        self,
        ev: dict[str, Any],
        *,
        final: bool = False,
        last_eta_s: Any = None,
        total: int | None = None,
    ) -> None:
        """单行动态进度条 / 关键事件行（stderr + \\r 刷新；异常退化跳过）。

        Args:
            ev: 事件 dict（chapter_start / chapter_substage / chapter_done）。
            final: chapter_done 时置 True（关键事件行，换行输出）。
            last_eta_s: 最近一次 chapter_done 的 eta_s（预计剩余；None 不显示）。
            total: 目标章数（chapter_substage 事件缺 total 时由调用方传入）。
        """
        try:
            t = ev.get("type")
            ch = int(ev.get("chapter", 0) or 0)
            cur_total = int(total or ev.get("total") or 0)
            if t == "chapter_start":
                subline = str(ev.get("subline", "") or "")
                elapsed = int(ev.get("elapsed_s", 0) or 0)
                line = f"第 {ch}/{cur_total} 章 · 准备中 · 已耗时 {_fmt_clock(elapsed)}"
                if subline:
                    line += f" · {subline}"
                if last_eta_s is not None:
                    line += f" · 预计剩余 {_fmt_clock(int(last_eta_s))}"
                sys.stderr.write("\r" + line + "   ")
            elif t == "chapter_substage":
                stage_names = {"generate": "生成中", "quality_check": "质检中", "revise": "修订中"}
                substage = str(ev.get("substage", ""))
                elapsed = int(ev.get("chapter_elapsed_s", 0) or 0)
                line = (
                    f"第 {ch}/{cur_total} 章 · {stage_names.get(substage, substage)}"
                    f" · 已耗时 {_fmt_clock(elapsed)}"
                )
                if last_eta_s is not None:
                    line += f" · 预计剩余 {_fmt_clock(int(last_eta_s))}"
                sys.stderr.write("\r" + line + "   ")
            elif t == "chapter_done" and final:
                words = int(ev.get("words", 0) or 0)
                qp = bool(ev.get("quality_passed", True))
                ch_elapsed = int(ev.get("chapter_elapsed_s", 0) or 0)
                eta = ev.get("eta_s")
                line = (
                    f"✓ 第 {ch} 章完成（{words} 字 · {'质量通过' if qp else '质量未过'}"
                    f" · 本章 {_fmt_clock(ch_elapsed)}"
                )
                if eta is not None:
                    line += f" · 预计剩余 {_fmt_clock(int(eta))}"
                line += "）"
                sys.stderr.write("\r" + line + "\n")
        except Exception:  # noqa: BLE001 - 进度条渲染异常退化（不阻断）
            pass

    def render_run_summary(self, summary: dict[str, Any]) -> None:
        """非 JSON 收尾打印运行摘要（rich 引导命令可复制；异常退化跳过）。"""
        try:
            text = str(summary.get("text", ""))
            for line in text.splitlines():
                if line.strip().startswith("下一步："):
                    cmd = line.strip().split("下一步：", 1)[1]
                    self.console.print(f"  [dim]下一步：[/dim][bold cyan]{cmd}[/bold cyan]")
                else:
                    self.console.print(line)
        except Exception:  # noqa: BLE001 - 摘要渲染异常不阻断
            pass
