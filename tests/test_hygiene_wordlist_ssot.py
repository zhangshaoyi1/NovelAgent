"""红线：文体卫生词表 SSOT —— blocking 判据必须配修复手段（2026-09-18）。

背景
----
受控实验 ch4 实测未过门禁，``chapter_quality_flags.json`` 记:

    {"chapter": 4, "violations": ["AI 承接词残留：说起来"]}

根因**不是**"写手用词不当"，而是同一批词在**三处各写一份字面量**：

- ``core/story/text_hygiene._HARD_BRIDGE_PHRASES``   —— blocking，**无替换手段**
- ``core/quality/text_hygiene._FILLER_PHRASES``      —— warning
- ``workflows/writing/m5_text_hygiene.L1_AI_PHRASES`` —— 硬替换，但表里没有承接词

⇒ 「判得最死的一侧没有修复手段」⇒ 只能**整章重写**（每轮再付一次全章生成 +
全章审稿，是成本放大器）。同族根因 R1「动作强度 ↔ 可修复性零对账」。

断言口径
--------
1. 词表唯一真源：三消费点的短语集合必须由 ``HYGIENE_PHRASES`` **派生**；
2. 元规则（纪律 #17 的可执行形态）：blocking 条目必须有 replacement 或显式
   ``unrepairable=True + reason`` —— 永久禁止「判而不可修」再生；
3. **行为级闭环**：门禁 ``scan_hard_pollutions`` 扫得出的承接词，
   ``clean_hard_pollutions`` 必须**真的能修掉**（不是只报）。
"""

from __future__ import annotations

from agent.core.quality.text_hygiene import _FILLER_PHRASES
from agent.core.story.text_hygiene import (
    AI_TONE_REPLACEMENTS,
    BRIDGE_REPLACEMENTS,
    HYGIENE_PHRASES,
    _HARD_BRIDGE_PHRASES,
    audit_phrase_ledger,
    clean_hard_pollutions,
    scan_hard_pollutions,
)
from agent.workflows.writing.m5_text_hygiene import L1_AI_PHRASES


def test_phrase_ledger_blocks_have_repair_or_declared_unrepairable() -> None:
    """元规则：blocking 级条目「有替换」或「显式标 unrepairable+reason」。"""
    bad = audit_phrase_ledger()
    assert not bad, (
        "存在「判而不可修」的 blocking 判据（纪律 #17）：" + "；".join(bad)
    )


def test_l1_ai_phrases_derived_from_ssot() -> None:
    """L1 硬替换表必须是 SSOT 的派生（含全部 ai_tone 条目、逐字相同）。"""
    assert L1_AI_PHRASES == AI_TONE_REPLACEMENTS, (
        "L1 词表与 SSOT 脱钩——又出现了第二份字面量（2026-09-18 ch4 的根因）"
    )
    assert "喃喃自语" in L1_AI_PHRASES, "禁词表口径已变，请同步本测试"


def test_bridge_and_filler_lists_derived_from_ssot() -> None:
    """门禁侧承接词清单与体检侧填充词清单同源于 SSOT 的 bridge 类。"""
    ssot_bridge = {s.phrase for s in HYGIENE_PHRASES if s.category == "bridge"}
    assert set(_HARD_BRIDGE_PHRASES) == ssot_bridge
    assert set(_FILLER_PHRASES) == ssot_bridge, (
        "体检侧词表与门禁侧不同源——历史上正是「一处 warning、一处 blocking」"
        "导致口径打架"
    )
    assert set(BRIDGE_REPLACEMENTS) == ssot_bridge, (
        "承接词必须全部带确定性替换（否则又成「判而不可修」）"
    )


def test_wordlists_are_not_redeclared_as_literals() -> None:
    """回归锁：三个消费点不得再各自写一份词表字面量。"""
    import inspect

    from agent.core.quality import text_hygiene as quality_hygiene
    from agent.workflows.writing import m5_text_hygiene

    l1_src = inspect.getsource(m5_text_hygiene)
    assert '"喃喃自语"' not in l1_src, (
        "m5_text_hygiene 又把 L1 词表写成字面量了——应派生自 HYGIENE_PHRASES"
    )

    q_src = inspect.getsource(quality_hygiene)
    for literal in ('"就这么着"', '"你别说"', '"说起来"'):
        assert literal not in q_src, (
            f"quality/text_hygiene 又出现词表字面量 {literal}——应派生自 HYGIENE_PHRASES"
        )


def test_gate_detected_bridge_is_repairable() -> None:
    """行为级闭环：门禁扫得出的承接词，落盘清理必须真能修掉。

    这是本红线最核心的一条 —— 断言「判据产出在修复端可达」（纪律 #8/#17），
    而不是断言"两个常量相等"。
    """
    sample = "说起来，他当年也是个人物。话说回来，这事儿透着古怪。"
    hits = scan_hard_pollutions(sample)
    assert any("承接词" in h for h in hits), f"叙述层承接词未被门禁识别：{hits}"

    fixed, traced = clean_hard_pollutions(sample)
    assert not scan_hard_pollutions(fixed), (
        f"清理后仍被门禁拦下——「扫得出但修不掉」会把人推回整章重写：{fixed}"
    )
    assert traced, "清理未留痕（批间反思的『对策→执行记录』闭环缺证据）"
    assert "说起来" not in fixed and "这么一想" in fixed, fixed
