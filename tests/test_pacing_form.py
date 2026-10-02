"""节奏形态审计与交叉验证红线（登记单 20261001_信任链与叙事上限六项能力·子项 5）。

守卫验收标准的机制层：
- **形态判据**（纯函数）：R1 平推（同档 ≤3 / 放松档 ≤2）、R2 高潮后必回落、
  R3 跳变 ≥2 级缺过渡；未标注档位的章不参与判定。
- **低谷章职责**：``PaceTier.duty`` 为放松档必填（垫片/日常有职责、高潮/推进为空），
  渲染派生自字段（design_brief 两处消费），写手与评委同源。
- **档位-张力交叉验证**：放松档实测 ≥8 = 该平没平；高潮档实测 ≤5 = 该爆没爆。
- **留痕**：形态发现落 `.state/pacing_form.jsonl`，检查点卡透出本窗口内的发现。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.core.story.chapter_contract import PACE_TIERS, PACE_TIER_BY_NAME
from agent.core.story.pacing_form import (
    audit_tier_form,
    load_window_tiers,
)


def _tiers(spec: dict[int, str]) -> dict[int, str]:
    return dict(spec)


# ---------------------------------------------------------------- R1 平推
def test_r1_same_tier_over_limit() -> None:
    # 连续 4 章推进（>3）→ 1 条 R1
    f = audit_tier_form(_tiers({1: "推进", 2: "推进", 3: "推进", 4: "推进"}))
    assert [x.rule for x in f] == ["R1 平推"]
    assert "4 章同为" in f[0].message


def test_r1_relaxed_tier_stricter_limit() -> None:
    # 连续 3 章日常（放松档上限 2）→ R1；连续 3 章推进不触发
    f = audit_tier_form(_tiers({1: "日常", 2: "日常", 3: "日常"}))
    assert [x.rule for x in f] == ["R1 平推"]
    assert "注水段" in f[0].message
    assert audit_tier_form(_tiers({1: "推进", 2: "推进", 3: "推进"})) == []


def test_r1_healthy_rhythm_clean() -> None:
    # 高潮→回落→推进→垫片→推进：健康形态零发现
    tiers = {1: "垫片", 2: "推进", 3: "高潮", 4: "日常", 5: "推进"}
    assert audit_tier_form(_tiers(tiers)) == []


# ---------------------------------------------------------------- R2 无回落
def test_r2_climax_followed_by_advance() -> None:
    f = audit_tier_form(_tiers({1: "高潮", 2: "推进", 3: "垫片"}))
    assert any(x.rule == "R2 无回落" and x.chapter == 2 for x in f)


def test_r2_climax_followed_by_fallback_ok() -> None:
    assert not any(
        x.rule == "R2 无回落"
        for x in audit_tier_form(_tiers({1: "高潮", 2: "日常", 3: "推进"}))
    )


# ---------------------------------------------------------------- R3 跳变
def test_r3_skip_transition() -> None:
    # 日常(序位3) → 高潮(序位0)：差 3，缺过渡
    f = audit_tier_form(_tiers({1: "日常", 2: "高潮", 3: "日常"}))
    assert any(x.rule == "R3 跳变" and x.chapter == 2 for x in f)


def test_r3_adjacent_tier_ok() -> None:
    # 垫片(2)→推进(1)：差 1，合法
    assert not any(
        x.rule == "R3 跳变" for x in audit_tier_form(_tiers({1: "垫片", 2: "推进"}))
    )


def test_unregistered_and_gap_chapters_ignored() -> None:
    # 未标注档位（键缺失）与序列有洞（1→3）不产生相邻判定
    assert audit_tier_form(_tiers({1: "推进", 3: "推进"})) == []
    assert audit_tier_form(_tiers({1: "自定义档", 2: "高潮"})) == []


# ---------------------------------------------------------------- 低谷章职责
def test_relaxed_tiers_have_duty_hard_tiers_do_not() -> None:
    for tier in PACE_TIERS:
        if tier.relaxed:
            assert tier.duty, f"放松档 {tier.name} 必须登记职责（低谷章合法化）"
        else:
            assert not tier.duty, f"非放松档 {tier.name} 不应有职责语义"


def test_duty_rendered_in_design_brief(tmp_path) -> None:
    """写手端设计简报按 duty 字段渲染职责（派生而非硬编码档位名单）。"""
    from agent.core.story.design_brief import _render_chapter_intent

    md = "## 章节强度档位\n\n第5章：日常\n"
    text = _render_chapter_intent(
        subline_md=md, chapter_num=5,
        route_title="", route_result="",
    )
    assert "本章职责" in text and "蓄势" in text


# ---------------------------------------------------------------- 窗口档位装配
def test_load_window_tiers_reads_sublines(tmp_path) -> None:
    sub = tmp_path / "sublines" / "S01"
    sub.mkdir(parents=True)
    (sub / "subline.md").write_text(
        "## 章节强度档位\n\n第3章：推进\n第4章：高潮\n第5章：日常\n",
        encoding="utf-8",
    )
    tiers = load_window_tiers(tmp_path, (3, 5))
    assert tiers == {3: "推进", 4: "高潮", 5: "日常"}


# ---------------------------------------------------------------- 批末交叉验证
def test_crosscheck_flags_flat_climax_and_flat_lows(tmp_path, monkeypatch) -> None:
    import agent.workflows.pipeline.agentic_pipeline_events as ev

    class _Console:
        def print(self, *a, **k):  # noqa: D102
            pass

    class _Memory:
        def __init__(self):
            self.logs = []

        def log(self, kind, title, data):
            self.logs.append((kind, data))

    class _Result:
        chapters_written = 3
        final_chapter = 3

    class _Harness(ev._PipelineEventsMixin):
        def __init__(self, project_dir):
            self.project_dir = Path(project_dir)
            self.console = _Console()
            self.memory = _Memory()

    h = _Harness(tmp_path)
    # 规划：ch1 日常（该平）、ch2 高潮（该爆）、ch3 垫片（该平）
    sub = tmp_path / "sublines" / "S01"
    sub.mkdir(parents=True)
    (sub / "subline.md").write_text(
        "## 章节强度档位\n\n第1章：日常\n第2章：高潮\n第3章：垫片\n",
        encoding="utf-8",
    )
    # 实测张力：ch1=9（该平没平）、ch2=4（该爆没爆）、ch3=3（正常）
    (tmp_path / ".state" / "memory").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".state" / "memory" / "tension_readings.json").write_text(
        json.dumps({"records": [
            {"ch": 1, "tension": 9.0},
            {"ch": 2, "tension": 4.0},
            {"ch": 3, "tension": 3.0},
        ]}),
        encoding="utf-8",
    )
    h._run_pacing_crosscheck_batch_end(_Result())
    assert len(h.memory.logs) == 1
    flags = "；".join(h.memory.logs[0][1]["flags"])
    assert "该平没平" in flags and "该爆没爆" in flags
    # 正常章不进 flags
    assert "第 3 章" not in flags


def test_form_findings_flow_into_checkpoint_card(tmp_path) -> None:
    """批前形态发现经 pacing_form.jsonl 透出进检查点卡 risks。"""
    import agent.workflows.pipeline.agentic_pipeline_events as ev

    (tmp_path / ".state").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".state" / "pacing_form.jsonl").write_text(
        json.dumps({"rule": "R1 平推", "chapter": 2, "severity": "warn", "message": "连续 4 章推进"})
        + "\n"
        + json.dumps({"rule": "R1 平推", "chapter": 90, "severity": "warn", "message": "窗外"}),
        encoding="utf-8",
    )

    class _Console:
        def print(self, *a, **k):  # noqa: D102
            pass

    class _SM:
        autonomy_level = 100
        state = State_dummy = None

        def load(self):  # noqa: D102
            pass

        def get_autonomy_level(self):
            return 100

    class _Result:
        chapters_written = 5
        final_chapter = 5
        escalated = tripped = blocked = False
        failures: list = []
        checkpoint = None

    class _Harness(ev._PipelineEventsMixin):
        def __init__(self, project_dir):
            self.project_dir = Path(project_dir)
            self.console = _Console()
            self.state_machine = _SM()
            self._gate_escalation_reason = ""

        def _emit_event(self, *a, **k):  # noqa: D102
            pass

    h = _Harness(tmp_path)
    r = _Result()
    h._finalize_checkpoint(r)
    card = json.loads((tmp_path / ".state" / "checkpoint.json").read_text(encoding="utf-8"))
    assert any("R1 平推" in x and "第2章" in x for x in card["risks"])
    assert not any("第90章" in x for x in card["risks"]), "窗口外的发现不得进卡"
