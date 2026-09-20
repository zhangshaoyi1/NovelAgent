"""B4（H3）红线：两类「通过」必须机器可区分。

事故形态：章级写时门禁（规则 + 单章 LLM）与批级全书体检（跨章窗口 LLM 严评）
**都输出一个叫「通过」的结论、字段都叫 ``overall_pass``** ⇒ 消费方（Web/审计/
日志/复盘）把「章级规则通过」读成「全书体检通过」，判据强度被混为一谈
（同族：``evaluator_types`` 旧实现只看 ``overall_pass``，降级维取 safe_default
达标即显示「✅ 通过」）。

锁死的不变式：
    ① 作用域是**登记制**（SSOT 成员）且两者互不相同；
    ② ``stamp`` 只增不删、不覆盖已有标签、对非 dict 不抛异常；
    ③ 章级判定**真的**被打上 ``chapter_gate``（接线存在，非孤儿 API）；
    ④ 批级审计记录带 ``book_health``，且 ``to_dict`` 含该键（只增不删）。
"""

from __future__ import annotations

from pathlib import Path

from agent.core.quality import pass_scope as ps
from agent.core.quality.audit import record_from_report


class TestScopeRegistry:
    def test_scopes_registered_and_distinct(self) -> None:
        """R1：两类作用域都在册且互不相同（不然标签等于没打）。"""
        assert ps.PASS_SCOPE_CHAPTER in ps.PASS_SCOPES
        assert ps.PASS_SCOPE_BOOK in ps.PASS_SCOPES
        assert ps.PASS_SCOPE_CHAPTER != ps.PASS_SCOPE_BOOK

    def test_key_name_is_stable(self) -> None:
        """标签键是契约面，改名即双向破裂（消费方与产出方各写一份）。"""
        assert ps.PASS_SCOPE_KEY == "pass_scope"


class TestStampContract:
    def test_stamp_adds_without_removing(self) -> None:
        rec = {"overall_pass": True, "rules": []}
        ps.stamp(rec, ps.PASS_SCOPE_CHAPTER)
        assert rec["overall_pass"] is True and rec["rules"] == []
        assert rec[ps.PASS_SCOPE_KEY] == ps.PASS_SCOPE_CHAPTER

    def test_stamp_does_not_override_existing(self) -> None:
        """R2：已带标签的记录不得被下游串味（防冒名顶替）。"""
        rec = {ps.PASS_SCOPE_KEY: ps.PASS_SCOPE_BOOK}
        ps.stamp(rec, ps.PASS_SCOPE_CHAPTER)
        assert rec[ps.PASS_SCOPE_KEY] == ps.PASS_SCOPE_BOOK

    def test_stamp_is_total(self) -> None:
        """R3：非 dict 输入原样返回、不抛异常（判定路径不得被标签设施打崩）。"""
        assert ps.stamp(None, ps.PASS_SCOPE_CHAPTER) is None
        obj = object()
        assert ps.stamp(obj, ps.PASS_SCOPE_CHAPTER) is obj


class TestChapterGateIsStamped:
    def test_write_path_stamps_chapter_scope(self) -> None:
        """R4（纪律 #7）：章级判定收口点必须真打标签，否则只是孤儿设施。"""
        src = (
            Path(__file__).resolve().parents[1]
            / "src" / "agent" / "workflows" / "writing" / "agentic_write.py"
        ).read_text(encoding="utf-8")
        assert "pass_scope.stamp(" in src, "章级判定未打作用域标签"
        assert "PASS_SCOPE_CHAPTER" in src, "章级判定未使用章级作用域"


class TestBookAuditIsStamped:
    def test_audit_record_carries_book_scope(self) -> None:
        """R5：批级审计记录带 book_health，且落盘键在场。"""

        class _D:
            name = "coherence"
            value = 90.0
            source = "llm/default"
            spec = None
            evidence = None

        class _R:
            dimensions = [_D()]
            overall_pass = True
            score = 95.0

        rec = record_from_report(_R())
        assert rec.pass_scope == ps.PASS_SCOPE_BOOK
        assert rec.to_dict()[ps.PASS_SCOPE_KEY] == ps.PASS_SCOPE_BOOK

    def test_two_surfaces_do_not_share_scope(self) -> None:
        """R6：两个面必须落在不同作用域（一个章级、一个批级）。"""

        class _R:
            dimensions: list = []
            overall_pass = True
            score = 1.0

        book = record_from_report(_R()).pass_scope
        chapter = ps.PASS_SCOPE_CHAPTER
        assert book != chapter
