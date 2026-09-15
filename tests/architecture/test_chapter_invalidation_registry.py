"""章节变更 → 派生状态失效总线 红线（2026-09-15）

背景（架构复盘第 3 类结构性根因）
--------------------------------
回滚原先只同步 **2** 个派生状态，且都是**逐次手工追加**的钩子
（``_sync_rag_index`` 2026-09-09、``_sync_fingerprints`` 2026-09-15），
而带章节语义的派生状态有 10+ 个 —— 每发现一个漏同步就补一个钩子，是"打地鼠"。

本文件把"哪些派生状态必须随章节变更清算"固化为可检查约束：

- C1 **登记表即清单（棘轮）**：带章节语义的状态集合被冻结，新增派生状态必须登记，
  否则红线失败（不允许悄悄多出一个"没人管的派生文件"）。
- C2 **登记表自洽**：``auto`` 必须给 ``invalidate``、``detect`` 必须给 ``detect``、
  ``none`` 必须给出"为何与章节号无关"的说明。
- C3 **回滚只走统一入口**：``rollback_to_chapter`` 必须调用 ``invalidate_chapters``，
  且不得再出现逐条手工钩子（防复发）。
- C4 **行为正确**：各 ``auto`` 实现真的能失效（退水位线 / 删条目）；
  ``detect`` 只报数不改盘；单条失败不阻断其它条目。
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path

from agent.core.story.chapter_invalidation import (
    MODE_AUTO,
    MODE_DETECT,
    MODE_NONE,
    REGISTRY,
    chapter_keyed_names,
    get_state,
    invalidate_chapters,
    registered_names,
)
from agent.workflows.evaluation.m10_rollback import M10RollbackWorkflow

#: C1 棘轮：带章节语义（必须随章节变更清算）的状态名冻结集合。
#: 新增一个"由章节正文派生"的状态（索引 / 台账 / 水位线 / 逐章缓存）时，
#: 必须同时把它登记进 ``REGISTRY`` 并更新本集合——两个动作缺一即红线失败。
KNOWN_CHAPTER_KEYED: frozenset[str] = frozenset({
    "chapter_fingerprints",
    "chapter_quality_flags",
    "payoff_script",
    "foreshadow_sync_watermark",
    "rag_index",
    "issue_debts",
    "foresight_beats",
})


# ============================================================
# C1 登记表即清单
# ============================================================
class TestC1RegistryIsTheChecklist:
    def test_chapter_keyed_set_is_frozen(self) -> None:
        assert set(chapter_keyed_names()) == KNOWN_CHAPTER_KEYED, (
            "带章节语义的派生状态集合发生变化：\n"
            f"  新增未登记：{sorted(set(chapter_keyed_names()) - KNOWN_CHAPTER_KEYED)}\n"
            f"  已被移除：{sorted(KNOWN_CHAPTER_KEYED - set(chapter_keyed_names()))}\n"
            "新增派生状态必须登记进 chapter_invalidation.REGISTRY，"
            "并同步更新本红线集合（两处都改才算完成）。"
        )

    def test_registry_names_are_unique(self) -> None:
        names = registered_names()
        assert len(names) == len(set(names)), f"登记名重复：{names}"

    def test_every_entry_declares_a_relpath_under_state_or_chapters(self) -> None:
        for d in REGISTRY:
            assert d.relpath, f"{d.name}: 未声明 relpath"
            assert d.relpath.startswith((".state", "chapters")), (
                f"{d.name}: relpath={d.relpath!r} 不在 .state/ 或 chapters/ 下——"
                f"请确认它确实是章节派生的项目内状态"
            )


# ============================================================
# C2 登记表自洽
# ============================================================
class TestC2RegistrySelfConsistency:
    def test_auto_entries_have_invalidator(self) -> None:
        for d in REGISTRY:
            if d.mode == MODE_AUTO:
                assert callable(d.invalidate), f"{d.name}: auto 模式缺少 invalidate 实现"

    def test_detect_entries_have_detector(self) -> None:
        for d in REGISTRY:
            if d.mode == MODE_DETECT:
                assert callable(d.detect), f"{d.name}: detect 模式缺少 detect 实现"

    def test_none_entries_explain_themselves(self) -> None:
        """``none`` 不是"忘了登记"，必须写明为何与章节号无关。"""
        for d in REGISTRY:
            if d.mode == MODE_NONE:
                assert len(d.note) >= 12, (
                    f"{d.name}: none 模式必须说明'为何与章节号无关'（当前 note 过短）"
                )

    def test_mode_vocabulary(self) -> None:
        for d in REGISTRY:
            assert d.mode in (MODE_AUTO, MODE_DETECT, MODE_NONE), (
                f"{d.name}: 未知失效模式 {d.mode!r}"
            )


# ============================================================
# C3 回滚只走统一入口
# ============================================================
class TestC3RollbackUsesSingleEntry:
    def test_rollback_calls_invalidate_chapters(self) -> None:
        src = inspect.getsource(M10RollbackWorkflow.rollback_to_chapter)
        assert "invalidate_chapters(" in src, (
            "rollback_to_chapter 必须经统一失效总线清算派生状态"
        )

    def test_rollback_has_no_adhoc_per_state_hook(self) -> None:
        """防复发：不得再出现逐条手工钩子（旧形态 self._sync_xxx(archived)）。"""
        src = inspect.getsource(M10RollbackWorkflow.rollback_to_chapter)
        for legacy in ("self._sync_rag_index(", "self._sync_fingerprints("):
            assert legacy not in src, (
                f"回滚路径又出现了逐条手工钩子 {legacy}——"
                f"请改为在 chapter_invalidation.REGISTRY 登记"
            )

    def test_deprecated_hooks_delegate_to_bus(self) -> None:
        """兼容入口若保留，必须委托总线（不得留旧实现双跑）。"""
        for name in ("_sync_rag_index", "_sync_fingerprints"):
            fn = getattr(M10RollbackWorkflow, name, None)
            if fn is None:
                continue
            src = inspect.getsource(fn)
            assert "invalidate_chapters(" in src, (
                f"{name} 保留但未委托总线——旧实现会与新总线双跑"
            )


# ============================================================
# C4 行为正确
# ============================================================
def _make_project(tmp_path: Path) -> Path:
    d = tmp_path / "proj"
    (d / ".state").mkdir(parents=True)
    (d / "chapters").mkdir(parents=True)
    return d


class TestC4AutoInvalidatorsActuallyInvalidate:
    def test_fingerprints_dropped(self, tmp_path: Path) -> None:
        """真实 schema：``{"fingerprints": {章号: [[hash, norm], ...]}}``。"""
        d = _make_project(tmp_path)
        fp = d / ".state" / "chapter_fingerprints.json"
        fp.write_text(json.dumps({"fingerprints": {
            "1": [["h1", "t1"]],
            "ch002": [["h2a", "t2a"]],
            "2": [["h2b", "t2b"]],          # 同章号的另一种键形也要清掉
            "3": [["h3", "t3"]],
        }}), encoding="utf-8")

        report = invalidate_chapters(d, [2])

        left = json.loads(fp.read_text(encoding="utf-8"))["fingerprints"]
        assert set(left) == {"1", "3"}
        item = next(r for r in report.results if r.name == "chapter_fingerprints")
        assert item.count == 2 and item.applied is True

    def test_quality_flags_dropped(self, tmp_path: Path) -> None:
        d = _make_project(tmp_path)
        p = d / ".state" / "chapter_quality_flags.json"
        p.write_text(json.dumps({"flags": [
            {"chapter": 1, "violations": ["x"]},
            {"chapter": 2, "violations": ["y"]},
            {"chapter": 3, "violations": ["z"]},
        ]}, ensure_ascii=False), encoding="utf-8")

        invalidate_chapters(d, [2, 3])

        left = json.loads(p.read_text(encoding="utf-8"))
        assert [f["chapter"] for f in left["flags"]] == [1]

    def test_payoff_script_dropped(self, tmp_path: Path) -> None:
        d = _make_project(tmp_path)
        p = d / ".state" / "payoff_script.json"
        p.write_text(json.dumps({
            "generated_at": "2026-09-15",
            "chapters": [{"chapter": 1}, {"chapter": 2}, {"chapter": 3}],
        }), encoding="utf-8")

        invalidate_chapters(d, [2])

        left = json.loads(p.read_text(encoding="utf-8"))
        assert [c["chapter"] for c in left["chapters"]] == [1, 3]

    def test_foreshadow_watermark_rolls_back(self, tmp_path: Path) -> None:
        """最隐蔽的一类：水位线不退 → 被重写章节被判"已扫过" → 永久漏扫。"""
        d = _make_project(tmp_path)
        p = d / ".state" / "foreshadow_sync.json"
        p.write_text(json.dumps({"scanned_to": 24}), encoding="utf-8")

        invalidate_chapters(d, [21, 22, 23, 24, 25])

        left = json.loads(p.read_text(encoding="utf-8"))
        assert left["scanned_to"] == 20, "水位线必须退到最早被归档章之前"

    def test_watermark_not_moved_when_already_behind(self, tmp_path: Path) -> None:
        d = _make_project(tmp_path)
        p = d / ".state" / "foreshadow_sync.json"
        p.write_text(json.dumps({"scanned_to": 5}), encoding="utf-8")

        report = invalidate_chapters(d, [20, 21])

        assert json.loads(p.read_text(encoding="utf-8"))["scanned_to"] == 5
        assert not [r for r in report.changed if r.name == "foreshadow_sync_watermark"]

    def test_missing_files_are_noop(self, tmp_path: Path) -> None:
        d = _make_project(tmp_path)
        report = invalidate_chapters(d, [2, 3])
        assert report.failed == []
        assert not (d / ".state" / "rag").exists(), "回滚不得凭空创建 RAG 索引"


class TestC4DetectNeverRewrites:
    def test_issue_debts_only_counted(self, tmp_path: Path) -> None:
        d = _make_project(tmp_path)
        p = d / ".state" / "issue_debts.json"
        payload = {"debts": [
            {"id": "D1", "registered_ch": 17, "constraint": "第17章一致性警告"},
            {"id": "D2", "registered_ch": 3, "constraint": "第3章…"},
        ]}
        p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        before = p.read_text(encoding="utf-8")

        report = invalidate_chapters(d, [17])

        assert p.read_text(encoding="utf-8") == before, (
            "detect 模式只报数：台账是自由文本，改写比留在原地更危险"
        )
        item = next(r for r in report.results if r.name == "issue_debts")
        assert item.count == 1 and item.applied is False
        assert report.stale and report.stale[0].name == "issue_debts"

    def test_foresight_beats_counted_by_anchor_and_commit(self, tmp_path: Path) -> None:
        d = _make_project(tmp_path)
        p = d / ".state" / "foresight.json"
        p.write_text(json.dumps({"threads": [{"fid": "F-01", "beats": [
            {"beat_id": "b1", "anchor_chapter": 3, "commit_id": "ch003"},
            {"beat_id": "b2", "anchor_chapter": 9, "commit_id": "ch009"},
        ]}]}), encoding="utf-8")

        item = next(
            r for r in invalidate_chapters(d, [3]).results
            if r.name == "foresight_beats"
        )
        assert item.count == 1


class TestC4FailureIsolation:
    def test_one_failure_does_not_block_others(self, tmp_path: Path, monkeypatch) -> None:
        """回滚是救命动作：某条派生状态坏了，其它仍须清算。"""
        d = _make_project(tmp_path)
        (d / ".state" / "chapter_quality_flags.json").write_text(
            json.dumps({"flags": [{"chapter": 2}]}), encoding="utf-8"
        )

        from agent.core.story import chapter_invalidation as ci

        def _boom(project_dir, nums):  # noqa: ANN001
            raise RuntimeError("指纹库损坏")

        # 只替换登记表里该条目的实现（frozen dataclass → dataclasses.replace）
        from dataclasses import replace

        patched = tuple(
            replace(e, invalidate=_boom) if e.name == "chapter_fingerprints" else e
            for e in ci.REGISTRY
        )
        monkeypatch.setattr(ci, "REGISTRY", patched)

        report = ci.invalidate_chapters(d, [2])

        assert [r.name for r in report.failed] == ["chapter_fingerprints"]
        assert any(r.name == "chapter_quality_flags" and r.applied for r in report.changed)


class TestC4RegistryLookup:
    def test_get_state_by_name(self) -> None:
        assert get_state("rag_index") is not None
        assert get_state("nonexistent") is None

    def test_only_filter(self, tmp_path: Path) -> None:
        d = _make_project(tmp_path)
        (d / ".state" / "foreshadow_sync.json").write_text(
            json.dumps({"scanned_to": 9}), encoding="utf-8"
        )
        report = invalidate_chapters(d, [9], only=["foreshadow_sync_watermark"])
        assert [r.name for r in report.results] == ["foreshadow_sync_watermark"]
