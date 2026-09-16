"""红线：L1 禁词硬拦截必须在**生产入口**可达（2026-09-16）。

背景
----
能力对账豁免表把 ``_maybe_deslop`` 登记为「去 AI 味改由
``AgenticWriteWorkflow._run_deslop`` 承担」——但该去向**只对了「去 AI 味」一半**：
``hard_replace_ai_phrases``（L1 确定性硬拦截）与 ``.state/l1_trace.jsonl``
（对策执行轨迹）**只存在于废弃 M5 入口内**，写章入口收敛到 agentic 时从未随迁
⇒ 生产路径不跑 L1 硬拦截、``l1_trace.jsonl`` 恒空 ⇒ 批间反思的
「对策→执行记录→回归→销账」闭环缺执行证据。

与 2026-09-11 G15 ``_archive_chapter`` 事故**同构**（收敛丢能力）；且此前
能力对账红线**漏检**——它以「整方法」为对账粒度，豁免表登记的方法去向看似成立，
掩盖了其内部**子能力**（L1 段）从未迁移的事实。

断言口径：**行为级**——断言禁词真的被替换、轨迹真的落盘；不采信
「函数被调用 / 字段存在」这类形状断言（历史教训：静默截断能骗过形状断言）。
"""

from __future__ import annotations

import json
from pathlib import Path

from agent.workflows.writing.agentic_write import AgenticWriteWorkflow
from agent.workflows.writing.m5_text_hygiene import L1_AI_PHRASES


class _QuietConsole:
    def print(self, *a, **k) -> None:  # noqa: ANN002, ANN003
        pass


def _harness(tmp_path: Path) -> AgenticWriteWorkflow:
    """最小实例：只备 L1 段所需属性。

    ``llm=None`` 是有意的——L1 段零 LLM；后续 deslop 段必然失败并降级
    ``return text``，而该 ``text`` 已是 L1 替换后的文本（L1 段在前），
    因此恰好证明「L1 替换不会被后续失败吃掉」。
    """
    wf = object.__new__(AgenticWriteWorkflow)
    wf.project_dir = tmp_path
    wf.console = _QuietConsole()
    wf.deslop_enabled = True
    wf.llm = None
    return wf


def test_run_deslop_applies_l1_hard_block(tmp_path: Path) -> None:
    """``_run_deslop`` 必须真的替换 L1 禁词（不是只 import 不调用）。"""
    phrase = "喃喃自语"
    assert phrase in L1_AI_PHRASES, "禁词表口径已变，请同步本测试"
    wf = _harness(tmp_path)
    out = wf._run_deslop(f"他{phrase}，像是在跟自己说话。", {"chapter_num": 7})
    assert phrase not in out, (
        "生产入口 _run_deslop 未执行 L1 禁词硬拦截——该能力只留在废弃 M5 入口"
    )
    assert L1_AI_PHRASES[phrase] in out, "L1 替换未落成替词"


def test_run_deslop_writes_l1_trace(tmp_path: Path) -> None:
    """L1 替换必须落 ``.state/l1_trace.jsonl``（批间反思的执行记录）。"""
    wf = _harness(tmp_path)
    wf._run_deslop("他心中一动，又按了下去。", {"chapter_num": 7})
    trace = tmp_path / ".state" / "l1_trace.jsonl"
    assert trace.exists(), (
        "L1 替换轨迹未落盘 —— 批间反思的「对策→执行记录→回归→销账」闭环缺证据"
    )
    rec = json.loads(trace.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert rec["ch"] == 7
    assert rec["replaced"], "轨迹未记录替换明细"


def test_run_deslop_no_hit_writes_nothing(tmp_path: Path) -> None:
    """未命中禁词时不落轨迹（避免噪声记录冒充执行记录）。"""
    wf = _harness(tmp_path)
    wf._run_deslop("他沉默着走过了长街。", {"chapter_num": 8})
    assert not (tmp_path / ".state" / "l1_trace.jsonl").exists()
