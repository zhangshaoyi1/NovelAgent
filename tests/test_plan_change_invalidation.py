"""A5 红线：规划变更必须触发失效扇出（原实现只在「回滚」时触发）。

缺口（2026-09-20 立项）：``chapter_invalidation`` 建成后**只在 ``m10_rollback``
被调用**（``m10_rollback.py:164``）⇒ 规划改了、下游派生状态与已写章节不知情。

锁死的不变式：
    ① 规划变更的扇出入口存在且**真被规划变更点调用**（否则＝孤儿 API）；
    ② 作用域**刻意窄**：只清算「计划派生」的状态 + 对已写正文**只报数**；
       **绝不**把正文派生的状态（指纹/索引/质量标记/水位线）当垃圾清掉
       ——规划变更不动正文，它们仍然有效，清掉属越权销毁（且清指纹会制造
       「与自己上一版撞相似度」的假阳性）；
    ③ 新状态必须是 ``detect``（机械重写正文＝不可逆销毁）；
    ④ 未写章节不产生噪音。
"""

from __future__ import annotations

import json
from pathlib import Path

from agent.core.story import chapter_invalidation as ci


def _write_chapter(proj: Path, n: int) -> None:
    d = proj / "chapters"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"ch{n:03d}.md").write_text(f"第{n}章正文", encoding="utf-8")


def _write_payoff(proj: Path, nums: list[int]) -> Path:
    p = proj / ".state" / "payoff_script.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(
        {"chapters": [{"chapter": n, "beat": f"爽点{n}"} for n in nums]},
        ensure_ascii=False,
    ), encoding="utf-8")
    return p


class TestPlanChangeFanoutScope:
    def test_new_state_is_detect_not_auto(self) -> None:
        """R1：已写正文只能报数——绝不许机械重写（不可逆销毁）。"""
        st = ci.get_state("written_chapters_after_plan_change")
        assert st is not None, "新状态未登记进 REGISTRY"
        assert st.mode == ci.MODE_DETECT, "已写正文被授权机械重写＝不可逆销毁"
        assert st.detect is not None and st.invalidate is None

    def test_plan_change_states_exclude_text_derived(self) -> None:
        """R2：规划变更的作用域不得包含正文派生状态（越权销毁）。"""
        forbidden = {
            "chapter_fingerprints", "rag_index",
            "chapter_quality_flags", "foreshadow_sync_watermark",
        }
        assert not (set(ci.PLAN_CHANGE_STATES) & forbidden), (
            f"规划变更越权清算正文派生状态：{set(ci.PLAN_CHANGE_STATES) & forbidden}"
        )


class TestPlanChangeFanoutBehaviour:
    def test_written_chapters_reported_as_stale(self, tmp_path: Path) -> None:
        """R3：计划变了且正文已存在 ⇒ 报数（stale），不 applied。"""
        _write_payoff(tmp_path, [1, 2, 3, 9])
        for n in (1, 2, 3):
            _write_chapter(tmp_path, n)

        rep = ci.invalidate_for_plan_change(tmp_path, [1, 2, 3])

        stale = {r.name: r.count for r in rep.stale}
        assert stale.get("written_chapters_after_plan_change") == 3
        assert not any(
            r.name == "written_chapters_after_plan_change" and r.applied
            for r in rep.results
        )

    def test_unwritten_chapters_produce_no_noise(self, tmp_path: Path) -> None:
        """R4：未写章节不产生 stale 噪音。"""
        _write_payoff(tmp_path, [1, 2])
        _write_chapter(tmp_path, 1)

        rep = ci.invalidate_for_plan_change(tmp_path, [2])

        stale = {r.name: r.count for r in rep.stale}
        assert stale.get("written_chapters_after_plan_change", 0) == 0

    def test_plan_derived_schedule_is_cleared(self, tmp_path: Path) -> None:
        """R5：计划派生的逐章排期按受影响章被清算（auto 生效）。"""
        p = _write_payoff(tmp_path, [1, 2, 3, 9])

        rep = ci.invalidate_for_plan_change(tmp_path, [1, 2])

        left = [c["chapter"] for c in json.loads(p.read_text("utf-8"))["chapters"]]
        assert left == [3, 9], "受影响章的旧排期残留"
        assert any(r.name == "payoff_script" and r.applied for r in rep.changed)

    def test_text_derived_states_untouched(self, tmp_path: Path) -> None:
        """R6：正文派生的状态**一个都不能动**（即使文件存在）。"""
        fp = tmp_path / ".state" / "chapter_fingerprints.json"
        fp.parent.mkdir(parents=True, exist_ok=True)
        payload = {"1": "aaa", "2": "bbb"}
        fp.write_text(json.dumps(payload), encoding="utf-8")
        _write_payoff(tmp_path, [1, 2])
        _write_chapter(tmp_path, 1)

        ci.invalidate_for_plan_change(tmp_path, [1, 2])

        assert json.loads(fp.read_text("utf-8")) == payload, "指纹被越权清掉"


class TestFanoutIsWired:
    def test_batch_replan_calls_plan_change_fanout(self) -> None:
        """R7（纪律 #7）：扇出入口必须有真实消费者，否则只是孤儿 API。"""
        src = (
            Path(__file__).resolve().parents[1]  # tests/ → agent 仓根
            / "src" / "agent" / "workflows" / "pipeline" / "batch_replan.py"
        ).read_text(encoding="utf-8")
        assert "invalidate_for_plan_change(" in src, (
            "规划变更点未调用失效扇出——规划改了，已写章节与派生状态仍不知情"
        )
        assert "plan_chapter_digest(" in src, "未按『计划排期 ∩ 已写正文』限定作用域"
