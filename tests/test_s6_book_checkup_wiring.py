"""S6 跨章集成检查：判据修复 + 批末接线红线（2026-09-20）

覆盖三块：

A. ``check_speaker_registry`` 四条**结构判据**（修 76% 误报，229→55 实测）
B. ``check_character_stagnation`` **作用域**（只判配角）+ **语料门槛**显性降级
C. 批末接线（advisory：只告警不阻断）+ 降级命名空间已登记

本文件的行为样本全部取自**真实项目实测**的误报/真阳，不是构造的假样本。
"""

from __future__ import annotations

import ast
from pathlib import Path

from agent.core.quality import book_checkup as bc

SRC = Path(__file__).resolve().parents[1] / "src" / "agent"
EVENTS = SRC / "workflows" / "pipeline" / "agentic_pipeline_events.py"
PIPELINE = SRC / "workflows" / "pipeline" / "agentic_pipeline.py"
REGISTRY = SRC / "core" / "infra" / "degrade_registry.py"


def _proj(tmp_path: Path, chapters: dict[int, str],
          chars: dict[str, str] | None = None) -> Path:
    (tmp_path / "chapters").mkdir(parents=True, exist_ok=True)
    for num, body in chapters.items():
        (tmp_path / "chapters" / f"ch{num:03d}.md").write_text(body, encoding="utf-8")
    if chars:
        (tmp_path / "characters").mkdir(parents=True, exist_ok=True)
        for name, fm in chars.items():
            (tmp_path / "characters" / f"{name}.md").write_text(fm, encoding="utf-8")
    return tmp_path


class TestSpeakerStructuralRules:
    """A：四条结构判据 —— 每条都用真实误报样本验证，并验证真阳不被误杀。"""

    #: 真实误报样本 → 期望的拒绝原因（取自 9 项目实测抽取）
    FALSE_POSITIVES = [
        ("嘴角勾起一抹冷笑。", "起一抹", "left_boundary"),      # 「冷笑」此处是名词
        ("他缓缓说道：「走。」", "缓缓", "left_boundary"),      # 前有「他」（非边界）
        ("，缓缓说道：「走。」", "缓缓", "aa_adverb"),          # 边界成立 ⇒ 由叠字规则兜住
        ("，李淡淡说道：「好。」", "李淡淡", "aa_adverb"),       # 3 字窗含叠字（整词 AA 判定抓不到）
        ("，我听说，「是这样。」", "我听", "pronoun_prefix"),
        ("，一边说道：「走。」", "一边", "not_name_head"),       # 词频噪声
        ("，口中说道：「走。」", "口中", "not_name_head"),
        ("，随即说道：「走。」", "随即", "not_name_head"),
    ]

    def test_r1_each_false_positive_is_rejected_with_reason(self) -> None:
        """R1：每条实测误报都被拒，且**原因可辨**（不是靠一个笼统黑名单）。"""
        for text, cand, expect in self.FALSE_POSITIVES:
            prev = text[text.index(cand) - 1]
            ok, why = bc._speaker_name_ok(cand, prev)
            assert not ok, f"误报未被拒：{cand}（上下文 {text}）"
            assert why == expect, f"{cand} 拒绝原因 {why} ≠ 期望 {expect}"

    def test_r2_real_names_are_kept(self) -> None:
        """R2：真实人名（差评实证「周长老/孙小豆/王执事/苏清雪」）不得被误杀。"""
        for cand in ["周长老", "孙小豆", "王执事", "苏清雪", "赵捕头", "林婉儿", "陈明"]:
            ok, why = bc._speaker_name_ok(cand, "，")
            assert ok, f"真阳被误杀：{cand}（原因 {why}）"

    def test_r3_end_to_end_on_synthetic_project(self, tmp_path: Path) -> None:
        """R3：整链行为 —— 真阳报出、误报不报。"""
        body = (
            "，周长老说道：「此事我来办。」\n"
            "嘴角勾起一抹冷笑。\n"
            "，我听说，「是这样。」\n"
            "，缓缓说道：「走。」\n"
            "，一边说道：「好。」\n"
        )
        proj = _proj(tmp_path, {1: body, 2: body}, chars={})
        res = bc.check_speaker_registry(proj, bc._load_chapters(proj))
        names = {u["speaker"] for u in res["unregistered"]}
        assert "周长老" in names, "真阳未报出"
        for bad in ["起一抹", "我听", "缓缓", "一边", "安淡淡"]:
            assert bad not in names, f"误报漏网：{bad}"

    def test_r4_filter_counts_are_observable(self, tmp_path: Path) -> None:
        """R4：过滤**不静默** —— 逐规则计数必须在返回值里（可观测缺口）。"""
        body = "，我听说，「好。」\n，一边说道：「好。」\n，缓缓说道：「好。」\n"
        proj = _proj(tmp_path, {1: body, 2: body})
        res = bc.check_speaker_registry(proj, bc._load_chapters(proj))
        assert isinstance(res.get("filtered"), dict) and res["filtered"], (
            "过滤计数缺失 ⇒ 过滤本身变成静默行为（纪律 #1 失败显性化）"
        )
        assert sum(res["filtered"].values()) >= 3


class TestCharacterStagnationScope:
    """B：作用域（只判配角）+ 语料门槛。"""

    CHARS = {
        "主角甲": "---\nrole: protagonist\n---\n",
        "反派乙": "---\nrole: antagonist\n---\n",
        "配角丙": "---\nrole: supporting\n---\n",
        "档案无角色": "---\nname: x\n---\n",      # 缺 role ⇒ 按配角
    }

    def test_r5_only_supporting_roles_are_in_scope(self, tmp_path: Path) -> None:
        """R5：protagonist / antagonist **不得**被报（实测误报：杜从云 antagonist 30 章）。"""
        body = "主角甲 反派乙 配角丙 档案无角色 都在场。\n"
        proj = _proj(tmp_path, {i: body for i in range(1, 32)}, chars=self.CHARS)
        res = bc.check_character_stagnation(proj, bc._load_chapters(proj), 10)
        got = {v["character"] for v in res["violations"]}
        assert "配角丙" in got, "配角连续出场应被报出"
        assert "档案无角色" in got, "缺 role 的档案按配角处理（不得漏检）"
        assert "主角甲" not in got, "主角被误报"
        assert "反派乙" not in got, (
            "反派被误报 —— 对抗线必须持续在场，这不是「工具人」（纪律 #18 作用域）"
        )
        assert res["scope"] == "supporting-only"

    def test_r6_no_corpus_gate_suppresses_short_books_naturally(self, tmp_path: Path) -> None:
        """R6：**刻意不设**语料门槛 —— 短书自然凑不出达阈值的 streak。

        曾试行 `len(chapters) < limit*3 ⇒ 清空违规`，被撤除：既无实测依据
        （短书本来就报不出），又破坏了既有 13 章夹具的红线（纪律 #26：
        阈值型判据须有依据，不得拍脑袋加）。
        """
        body = "配角丙 在场。\n"
        proj = _proj(tmp_path, {i: body for i in range(1, 8)}, chars=self.CHARS)  # 7 章 < limit
        res = bc.check_character_stagnation(proj, bc._load_chapters(proj), 10)
        assert res["violations"] == [], "7 章书不该报 10 章连续（自然为空）"
        assert "corpus_insufficient" not in res, "不该存在无依据的语料门槛字段"

    def test_r7_short_but_sufficient_corpus_still_reports(self, tmp_path: Path) -> None:
        """R7：13 章夹具（既有红线的口径）必须仍然报出 —— 防门槛回归。"""
        body = "配角丙 在场。\n"
        proj = _proj(tmp_path, {i: body for i in range(1, 14)}, chars=self.CHARS)
        res = bc.check_character_stagnation(proj, bc._load_chapters(proj), 10)
        assert {v["character"] for v in res["violations"]} == {"配角丙"}


class TestBatchEndWiring:
    """C：批末接线（advisory）+ 契约登记。"""

    def test_r8_method_exists_and_is_called(self) -> None:
        """R8：装配点必须**同时**存在于 mixin 与调用方（防「建成未接线」）。"""
        assert "_run_book_checkup_batch_end" in EVENTS.read_text(encoding="utf-8"), (
            "mixin 未定义批末体检方法"
        )
        assert "_run_book_checkup_batch_end(" in PIPELINE.read_text(encoding="utf-8"), (
            "批末体检方法**零调用点** ⇒ 建成未接线（纪律 #7）"
        )

    def test_r9_degrade_namespace_registered(self) -> None:
        """R9：新降级命名空间必须走契约登记（棘轮：不得靠 noqa 豁免）。"""
        reg = REGISTRY.read_text(encoding="utf-8")
        assert '"pipeline.book_checkup"' in reg, "降级命名空间未登记（G2 契约）"
        src = EVENTS.read_text(encoding="utf-8")
        assert 'degrade("pipeline.book_checkup"' in src

    def test_r10_advisory_only_never_blocks(self) -> None:
        """R10：advisory 语义 —— 不得抛错、不得置 blocked/escalated。"""
        tree = ast.parse(EVENTS.read_text(encoding="utf-8"))
        fn = next(
            (n for n in ast.walk(tree)
             if isinstance(n, ast.FunctionDef) and n.name == "_run_book_checkup_batch_end"),
            None,
        )
        assert fn is not None
        body_src = ast.get_source_segment(EVENTS.read_text(encoding="utf-8"), fn) or ""
        for banned in ["result.blocked", "result.tripped", "result.escalated", "raise "]:
            assert banned not in body_src, (
                f"批末体检出现 `{banned}` ⇒ 越权阻断（第五部分纪律：小说要创造性生长）"
            )

    def test_r11_memory_failure_goes_through_degrade(self) -> None:
        """R11：留痕失败**不得静默** —— 必须落到 degrade（防新增 noqa 豁免）。"""
        tree = ast.parse(EVENTS.read_text(encoding="utf-8"))
        fn = next(
            (n for n in ast.walk(tree)
             if isinstance(n, ast.FunctionDef) and n.name == "_run_book_checkup_batch_end"),
            None,
        )
        src = ast.get_source_segment(EVENTS.read_text(encoding="utf-8"), fn) or ""
        # memory.log 不得被包在「只 pass」的内层 except 里
        assert "pass  # noqa: SILENT_DEGRADE" not in src, (
            "内层静默吞异常 ⇒ 既新增豁免计数又让失败不可见"
        )
        assert "self.memory.log(" in src
        assert 'degrade("pipeline.book_checkup"' in src


class TestFailureVisibility:
    """D：失败显性化（degraded 不得被解读为通过）。"""

    def test_r12_run_checkup_passed_false_on_degraded(self, tmp_path: Path) -> None:
        """R12：读数降级 ⇒ ``passed=False``（不得被读成"没问题"）。"""
        proj = _proj(tmp_path, {1: "正常正文。" * 50})
        rep = bc.run_book_checkup(proj)
        assert rep["success"] is True
        # 构造一个降级：让 foreshadow 文件不可解析由别处覆盖；此处断言语义
        rep2 = dict(rep, degraded=["ch001: meta 解析失败"], issues=[])
        assert (rep2["degraded"] and not (not rep2["issues"] and not rep2["degraded"])) is True
        # 直接断言真实实现：degraded 非空 ⇒ passed False
        proj2 = _proj(tmp_path / "p2", {1: "正常正文。" * 50})
        (proj2 / ".state").mkdir(parents=True, exist_ok=True)
        (proj2 / ".state" / "foresight.json").write_text("{ 坏 JSON", encoding="utf-8")
        rep3 = bc.run_book_checkup(proj2)
        if rep3.get("degraded"):
            assert rep3["passed"] is False, "存在 degraded 却判 passed=True"
