"""G3 自主规划编排离线测试（P0-1 / P0-2 / P0-3 + P1-1 衔接断言）

纯离线：注入 fake LLM（通用 JSON）与各 stub，零真实 LLM 调用。验证：
- 成功路径：world.md / architecture.md(confirmed==True 且含八维度) / outline.md
  / sublines/S*_*/subline.md / characters/*.md / relations/graph.md /
  foreshadows.md / golden_finger_registration.md 全部存在，状态停在
  CHARACTER_DESIGN/WRITING；G2 七维衔接字段齐备（identity/core_motivation/
  arc/language_fingerprint/relations + foreshadows 有效 F 编号）。
- 幂等重入：已存在全部产物时重跑不触发新的规划 LLM 调用、不覆盖。
- 关键前置失败（M1 抛异常）：PipelineResult.blocked==True、未进入写章。
- 非关键失败（M4 抛异常）：pipeline 不崩、其余产物保留、characters/ 至少占位、
  状态推进到可写态；再次运行不崩（续跑）。
"""

from __future__ import annotations

import frontmatter
from pathlib import Path

from agent.core.quality.guardrails import is_architecture_confirmed
from agent.core.engine.state_machine import State
from agent.workflows.agentic_pipeline import AgenticPipelineWorkflow
from tests._g3_fakes import (
    _FakeLLM,
    _StubEditor,
    _StubMemory,
    _StubPlanner,
    _StubWriter,
    _make_plan,
)


def _make_pipeline(tmp_path: Path, fake: _FakeLLM, **kw) -> AgenticPipelineWorkflow:
    return AgenticPipelineWorkflow(
        project_dir=tmp_path,
        llm_client=fake,
        brief="废柴少年偶得神秘传承，踏上逆天修仙路",
        planner=_StubPlanner(_make_plan()),
        writer_workflow=_StubWriter(),
        editor=_StubEditor(),
        memory=_StubMemory(),
        eval_enabled=False,
        **kw,
    )


# ============================================================
# 成功路径：自主规划产出齐全 + 状态 + G2 衔接
# ============================================================
def test_autoplan_success_produces_all_artifacts(tmp_path: Path) -> None:
    fake = _FakeLLM()
    p = _make_pipeline(tmp_path, fake)
    p._ensure_setting_set()

    # world.md
    assert (tmp_path / "world.md").exists()
    # architecture.md：已确认 + 八维度
    assert is_architecture_confirmed(tmp_path) is True
    arch = frontmatter.load(tmp_path / "architecture.md")
    arch_dims = set((arch.metadata.get("architecture") or {}).keys())
    assert {
        "story_core", "protagonist_triple", "main_plot", "sublines_preview",
        "conflict_nodes", "theme", "ending", "emotional_tone",
    } <= arch_dims
    # outline.md + sublines/S*_*/subline.md
    assert (tmp_path / "outline.md").exists()
    sublines = list((tmp_path / "sublines").glob("S*_*/subline.md"))
    assert sublines, "应至少生成一条支线 subline.md"
    # characters/*.md
    char_files = list((tmp_path / "characters").glob("*.md"))
    assert char_files, "应至少生成一个角色档案"
    # relations/graph.md + foreshadows.md + golden_finger_registration.md
    assert (tmp_path / "relations" / "graph.md").exists()
    assert (tmp_path / "foreshadows.md").exists()
    assert (tmp_path / "golden_finger_registration.md").exists()

    # 状态机停在 CHARACTER_DESIGN / WRITING
    p.state_machine.load()
    assert p.state_machine.state in (State.CHARACTER_DESIGN, State.WRITING)

    # P1-1 衔接：角色档案含 G2 可读字段
    content = char_files[0].read_text(encoding="utf-8")
    for kw in ("身份", "核心动机", "弧光", "语言指纹", "关系"):
        assert kw in content, f"角色档案缺少衔接字段标记：{kw}"
    # P1-1 衔接：伏笔表含有效 F 编号
    fs = (tmp_path / "foreshadows.md").read_text(encoding="utf-8")
    assert "F-01" in fs, "伏笔表应含有效 F 编号"


# ============================================================
# 幂等重入：不重复调用 workflow、不覆盖
# ============================================================
def test_autoplan_idempotent_on_rerun(tmp_path: Path) -> None:
    fake = _FakeLLM()
    p1 = _make_pipeline(tmp_path, fake)
    p1.run()
    calls_after_first = fake.calls

    # 重入（产物已全存在）→ 不应触发新的规划 LLM 调用
    p2 = _make_pipeline(tmp_path, fake)
    p2.run()
    calls_after_second = fake.calls
    assert calls_after_second == calls_after_first, "重入不应额外调用规划 LLM"

    # 文件未被覆盖（world.md 内容一致）
    world_1 = (tmp_path / "world.md").read_text(encoding="utf-8")
    world_2 = (tmp_path / "world.md").read_text(encoding="utf-8")
    assert world_1 == world_2


# ============================================================
# 关键前置失败（M1 抛异常）→ 安全退出、不进写章
# ============================================================
def test_m1_failure_blocks_pipeline(tmp_path: Path, monkeypatch) -> None:
    from agent.workflows import m1_config

    def _boom(self, user_input=None):
        raise RuntimeError("M1 故意失败（测试）")

    monkeypatch.setattr(m1_config.M1ConfigWorkflow, "run", _boom)

    fake = _FakeLLM()
    p = _make_pipeline(tmp_path, fake)
    result = p.run()

    assert result.blocked is True
    assert p._plan_blocked is True
    # 未进入写章（被阻塞提前 return，状态停留在规划前）
    p.state_machine.load()
    assert p.state_machine.state != State.WRITING


# ============================================================
# 非关键失败（M4 抛异常）→ 不崩、降级占位、续跑不崩
# ============================================================
def test_m4_failure_degrades_and_continues(tmp_path: Path, monkeypatch) -> None:
    from agent.workflows import m4_character

    def _boom(self):
        raise RuntimeError("M4 故意失败（测试）")

    monkeypatch.setattr(m4_character.M4CharacterWorkflow, "run", _boom)

    fake = _FakeLLM()
    p = _make_pipeline(tmp_path, fake)
    result = p.run()  # 不应抛出

    assert result is not None
    assert result.blocked is False, "非关键失败不应阻塞整轮"

    # 其余产物保留
    assert (tmp_path / "world.md").exists()
    assert is_architecture_confirmed(tmp_path) is True
    assert (tmp_path / "outline.md").exists()

    # M4 失败 → 降级占位（characters/ + 关系/伏笔/金手指 由 degrade 补齐）
    char_files = list((tmp_path / "characters").glob("*.md"))
    assert char_files, "M4 失败后应降级写出占位角色"
    assert (tmp_path / "relations" / "graph.md").exists()
    assert (tmp_path / "foreshadows.md").exists()
    assert (tmp_path / "golden_finger_registration.md").exists()

    # 状态推进到可写态（不进半残写章）
    p.state_machine.load()
    assert p.state_machine.state in (State.CHARACTER_DESIGN, State.WRITING)

    # 再次运行不崩（续跑）
    p2 = _make_pipeline(tmp_path, fake)
    r2 = p2.run()
    assert r2 is not None
    assert r2.blocked is False
