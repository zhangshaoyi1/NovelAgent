"""红线：契约字段标签不得泄漏进正文（2026-09-18，受控实验 ch3 实测）。

背景
----
``prompts/m3/outline.md`` v4 把契约字段名定为
``章首钩子/章尾钩子/爽点/目标情绪/在场/禁/验收``，而
``guardrails._META_LEAK_RE`` 恰把 ``章末悬念`` 列为 **error 级**禁词
⇒ **同一批词：供给侧当"字段名"用、检测侧当"污染词"拦**，两边各写一份字面量。

实测泄漏（``chapters/ch003.md:254``）::

    - *钩子：章末悬念从笼统的「外域词汇」收敛到更具体的……*

⇒ 被「写作元指令泄漏」判 blocking ⇒ ch3 未过门禁（**重写后仍失败**）。
这是「提示词语言锚 ↔ 消费者解析式是同一件事的两半」的**反向破裂**。

断言口径
--------
1. 词表唯一真源：guardrails 的检测词表由 ``chapter_contract.CONTRACT_LEAK_LABELS``
   派生；提示词里的字段名与 ``CONTRACT_FIELDS`` **机器交叉核对**（缺失/多余须为 0）；
2. 类级指纹：与措辞无关 —— 写手把字段名改写成「钩子：…」也拦得住；
3. **判据产出必须在修复端可达**：判 blocking 的批注行必须能被确定性删除
   （否则又是一次「判而不可修」⇒ 整章重写）；
4. 生产入口可达：``_run_deslop``（写时门禁**之前**）真的清掉，行为级断言。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import agent

from agent.core.quality.guardrails.guardrails import _META_LEAK_RE, Guardrails
from agent.core.story.chapter_contract import (
    CONTRACT_FIELDS,
    CONTRACT_LEAK_LABELS,
    find_contract_annotations,
    is_contract_annotation_line,
    strip_contract_annotations,
)
from agent.core.story.text_hygiene import clean_hard_pollutions, scan_hard_pollutions

_PROMPTS = Path(agent.__file__).parent / "prompts"

#: 受控实验 ch003.md:254 的**真实**泄漏行
LEAK_LINE = (
    "- *钩子：章末悬念从笼统的「外域词汇」收敛到更具体的「为什么上古玉简里会出现和外"
    "域有关的词汇」，并呼应玉简背后的账目结构异常（前几行是数字，最后变成一句无解释的"
    "话），让悬念更具体可感。*"
)
SAMPLE = f"他握着玉简，指节发白。\n{LEAK_LINE}\n窗外的风停了一瞬。"


def _meta_leak_hits(text: str) -> list[str]:
    g = Guardrails()
    return [
        v.rule_id for v in g.check(text).violations if "meta" in v.rule_id or "hard" in v.rule_id
    ]


def test_real_leak_line_is_blocking() -> None:
    """ch3 真实泄漏行必须被判 blocking（两端：guardrails 元指令 + L2 硬污染）。"""
    assert find_contract_annotations(SAMPLE), "批注指纹未识别 ch3 泄漏行"
    assert "meta_instruction_leak" in _meta_leak_hits(SAMPLE), _meta_leak_hits(SAMPLE)
    assert any("契约批注" in h for h in scan_hard_pollutions(SAMPLE)), scan_hard_pollutions(SAMPLE)


def test_annotation_is_deterministically_removable() -> None:
    """判 blocking 的批注行必须能确定性删除 —— 且删完两端都不再报（闭环）。

    这是本红线最核心的一条：ch3 的失分点不是"没检测到"，而是**检测到却只能整章重写**。
    """
    stripped, traced = strip_contract_annotations(SAMPLE)
    assert traced, "清理未留痕（批测反思的『对策→执行记录』闭环缺证据）"
    assert "章末悬念" not in stripped, stripped
    assert "他握着玉简" in stripped and "窗外的风停了一瞬" in stripped, (
        f"清理误删了叙事正文：{stripped}"
    )
    assert not find_contract_annotations(stripped)
    assert "meta_instruction_leak" not in _meta_leak_hits(stripped), (
        f"清理后仍被 guardrails 拦：{_meta_leak_hits(stripped)}"
    )
    assert not scan_hard_pollutions(stripped), scan_hard_pollutions(stripped)


def test_l2_clean_removes_annotation() -> None:
    """落盘兜底路径（rewrite / paragraph_rewriter）也能修，不只写章路径。"""
    cleaned, traced = clean_hard_pollutions(SAMPLE)
    assert not scan_hard_pollutions(cleaned), scan_hard_pollutions(cleaned)
    assert any("契约批注" in t for t in traced), traced


def test_guardrail_wordlist_derived_from_ssot() -> None:
    """guardrails 检测词表必须涵盖契约真源的全部元词标签。"""
    missing = [x for x in CONTRACT_LEAK_LABELS if x not in _META_LEAK_RE.pattern]
    assert not missing, f"检测词表与契约真源脱钩（prompt 改名即双向破裂）：{missing}"


def test_common_short_words_not_in_wordlist() -> None:
    """常用短词**有意**不进词表（正文里「他站在场边」「验收了药材」是正常用语）。"""
    for word in ("爽点", "在场", "禁", "验收"):
        assert word not in CONTRACT_LEAK_LABELS, f"{word} 进词表会大量误杀正文"
        assert not is_contract_annotation_line(f"他站在场边，看着远处。"), word


def test_prompt_field_names_match_ssot() -> None:
    """机器交叉核对：提示词里的契约字段名必须都在 ``CONTRACT_FIELDS`` 内。

    这是"文档/提示词不会悄悄漂移"的可验证判据 —— 比人眼通读可靠。
    """
    src = (_PROMPTS / "m3" / "outline.md").read_text(encoding="utf-8")
    found = {m.strip() for m in re.findall(r"[｜|]([^=｜|\n]{1,8})=", src)}
    assert found, "未从 outline.md 提取到任何字段名（提示词格式可能已变，请同步本测试）"
    unknown = found - set(CONTRACT_FIELDS)
    assert not unknown, (
        f"提示词出现未登记的契约字段名 {sorted(unknown)} —— "
        "必须在 chapter_contract.CONTRACT_FIELDS 登记，否则检测侧无从派生"
    )


def test_class_level_fingerprint_catches_variants() -> None:
    """与措辞无关的类级指纹：写手改写字段名也拦得住。"""
    variants = [
        "- *钩子：他把线索收进袖中*",
        "- 爽点：当场打脸",
        "* 目标情绪：压抑转释然",
        "- 在场=林凡，周管事",
    ]
    for v in variants:
        assert is_contract_annotation_line(v), f"类级指纹漏掉变体：{v}"
        assert find_contract_annotations(v), v


def test_narrative_text_and_frontmatter_untouched() -> None:
    """不误杀：正常叙事句、含关键词的句子、YAML front-matter 列表都不动。"""
    for ok_line in (
        "他站在场边，看着远处的人群，什么也没说。",
        "「禁地不可入内。」老者沉声道。",
        "她验收了那批药材，点点头。",
    ):
        assert not is_contract_annotation_line(ok_line), ok_line

    fm = (
        "---\nchapter: 3\nevidence_chain:\n  characters:\n"
        "  - field: 身份/动机\n---\n正文开始了。"
    )
    out, traced = strip_contract_annotations(fm)
    assert out == fm and not traced, (out, traced)


class _QuietConsole:
    def print(self, *a, **k) -> None:  # noqa: ANN002, ANN003
        pass


def test_production_entry_strips_annotation(tmp_path: Path) -> None:
    """生产入口 ``_run_deslop``（门禁**之前**）真的清掉契约批注并落轨迹。"""
    from agent.workflows.writing.agentic_write import AgenticWriteWorkflow

    wf = object.__new__(AgenticWriteWorkflow)
    wf.project_dir = tmp_path
    wf.console = _QuietConsole()
    wf.deslop_enabled = True
    wf.llm = None

    out = wf._run_deslop(SAMPLE, {"chapter_num": 3})
    assert "章末悬念" not in out, f"生产入口未清契约批注（门禁仍会判 blocking）：{out}"
    trace = tmp_path / ".state" / "l1_trace.jsonl"
    assert trace.exists(), "契约批注清理未落执行轨迹"
    rec = json.loads(trace.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert rec["ch"] == 3
    assert any("契约批注" in r for r in rec["replaced"]), rec
