"""红线：承接词修复必须是**叙述层**确定性替换（引号感知，2026-09-18）。

背景
----
ch4 因「AI 承接词残留：说起来」判 blocking 却无修复路径，整章重写仍未过。
补替换时有一个必须守住的边界：这五个词（话说回来/你别说/就这么着/说起来/
总而言之）**在对话里是合法口语** —— 人物说「你别说，这事儿还真怪」没有任何
问题。所以：

- 检测与替换必须在**同一个叙述层投影**上做（引号内不算）；
- 若判据全局报、修复只在叙述层改（或反之），就会出现「报得出却修不掉」
  或「修了仍报」，两条路都会把流程推回整章重写。

断言口径：**行为级** —— 断言替换真的发生、对话真的没被动、门禁真的转干净；
不采信"函数被调用/常量存在"这类形状断言。
"""

from __future__ import annotations

import json
from pathlib import Path

from agent.core.story.text_hygiene import (
    clean_hard_pollutions,
    narrative_projection,
    replace_bridge_words,
    scan_hard_pollutions,
)
from agent.workflows.writing.m5_text_hygiene import (
    L1_AI_PHRASES,
    hard_replace_ai_phrases,
)


def test_narrative_bridge_is_replaced() -> None:
    """叙述层承接词被替换为弱过渡（而非删除，保留口语节奏）。"""
    out, traced = replace_bridge_words("说起来，他当年也是个人物。")
    assert out.startswith("这么一想，"), out
    assert traced and "说起来" in traced[0], traced


def test_dialogue_bridge_is_untouched() -> None:
    """对话内同词是人物口吻 —— 不报、不改（防误杀）。"""
    sample = "他咧嘴一笑：“你别说，这事儿还真怪。”"
    assert not scan_hard_pollutions(sample), (
        f"对话内的合法口语被误报为 AI 承接词：{scan_hard_pollutions(sample)}"
    )
    out, traced = replace_bridge_words(sample)
    assert out == sample, f"对话内台词被误改：{out}"
    assert not traced, traced


def test_mixed_narrative_and_dialogue() -> None:
    """混合样本：叙述层改、对话层原样保留。"""
    sample = "说起来，他早知道。“你别说，我还真没想过。”他摇摇头。"
    out, _ = replace_bridge_words(sample)
    assert out.startswith("这么一想，他早知道。"), out
    assert "“你别说，我还真没想过。”" in out, out


def test_l1_repairs_bridge_before_gate_scan() -> None:
    """L1 生产入口必须同时清组合腔与叙述层承接词，且清完门禁即干净。

    时序很关键：L1 在写时门禁**之前**跑；若它不清承接词，门禁仍判 blocking，
    就还是「整章重写仍不过」。
    """
    text = "说起来，他心中一动。他喃喃自语着走远。"
    out, replaced = hard_replace_ai_phrases(text)
    assert "心中一动" not in out and "忽然想到" in out, out
    assert "说起来" not in out and "这么一想" in out, out
    assert not scan_hard_pollutions(out), scan_hard_pollutions(out)
    assert len(replaced) == 3, replaced


def test_clean_is_idempotent() -> None:
    """落盘清理幂等：第二次执行不再产生变化（避免重复记账/抖动）。"""
    sample = "说起来，山壁被凿出一个洞口。"
    once, t1 = clean_hard_pollutions(sample)
    twice, t2 = clean_hard_pollutions(once)
    assert once == twice, (once, twice)
    assert t1 and not t2, (t1, t2)


def test_unclosed_quote_resets_at_line_end() -> None:
    """漏掉一个闭合引号不应让后半章退出检测（行尾复位）。"""
    sample = "他说：“你别说，这事儿……\n说起来，外面起了风。"
    hits = scan_hard_pollutions(sample)
    assert not any("你别说" in h for h in hits), hits
    assert any("说起来" in h for h in hits), (
        "行尾未闭合引号吞掉了下一行 —— 检测形同虚设（假阴性会让 AI 腔原样落盘）"
    )
    proj = narrative_projection(sample)
    assert "\n" in proj, "投影必须保留换行（等长映射回原文的前提）"


class _QuietConsole:
    def print(self, *a, **k) -> None:  # noqa: ANN002, ANN003
        pass


def _harness(tmp_path: Path):
    from agent.workflows.writing.agentic_write import AgenticWriteWorkflow

    wf = object.__new__(AgenticWriteWorkflow)
    wf.project_dir = tmp_path
    wf.console = _QuietConsole()
    wf.deslop_enabled = True
    wf.llm = None
    return wf


def test_production_entry_replaces_bridge_and_traces(tmp_path: Path) -> None:
    """生产入口 ``_run_deslop`` 真的替换承接词并落 ``l1_trace.jsonl``。"""
    assert "说起来" not in L1_AI_PHRASES, "承接词不应进 L1 组合腔表（走叙述层替换）"
    wf = _harness(tmp_path)
    out = wf._run_deslop("说起来，他当年也是个人物。", {"chapter_num": 11})
    assert "说起来" not in out, f"生产入口未清叙述层承接词：{out}"
    trace = tmp_path / ".state" / "l1_trace.jsonl"
    assert trace.exists(), "承接词替换未落执行轨迹"
    rec = json.loads(trace.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert rec["ch"] == 11
    assert any("说起来" in r for r in rec["replaced"]), rec
