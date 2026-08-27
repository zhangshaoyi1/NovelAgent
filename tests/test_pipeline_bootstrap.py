"""自主模式设定集引导回归测试（修复 bug2 / bug3 根因）

验证 ``AgenticPipelineWorkflow._ensure_setting_set`` 在「缺 world.md /
缺 architecture / 缺支线」时能从 brief + MasterPlan 自动补齐，并把状态机置为
可写状态，使下游 M5 ``_load_context`` 不再因缺文件硬抛错（进而章节号正常递进）。

纯离线：注入 planner stub，不触发任何真实 LLM 调用。
"""

from __future__ import annotations

from pathlib import Path

from agent.agents.planner import MasterPlan
from agent.core.confirmation import is_architecture_confirmed
from agent.core.setting_manager import SettingManager
from agent.core.state_machine import State
from agent.workflows.agentic_pipeline import AgenticPipelineWorkflow
from tests._g3_fakes import _FakeLLM


class _StubPlanner:
    """返回固定 Master Plan 的 planner 桩（不调用 LLM）。"""

    def __init__(self, plan: MasterPlan) -> None:
        self._plan = plan

    def load_plan(self):
        return None

    def run(self, brief: str) -> MasterPlan:
        return self._plan


def _make_plan() -> MasterPlan:
    return MasterPlan(
        brief="贫寒高二生觉醒二次元进度条系统，逆袭人生",
        title="进度条人生",
        genre="modern",
        total_chapters=12,
    )


def test_ensure_setting_set_bootstraps_missing_files(tmp_path: Path) -> None:
    """全新项目（无任何设定文件）→ 引导后应齐全且状态可写。"""
    pipeline = AgenticPipelineWorkflow(
        project_dir=tmp_path,
        brief="测试思路",
        planner=_StubPlanner(_make_plan()),
        llm_client=_FakeLLM(),
    )
    pipeline._ensure_setting_set()

    # world.md 生成
    assert (tmp_path / "world.md").exists()
    # architecture.md 存在且 confirmed
    assert is_architecture_confirmed(tmp_path) is True
    # 至少一个支线
    assert SettingManager(tmp_path).list_sublines()
    # 状态机进入可写状态
    pipeline.state_machine.load()
    assert pipeline.state_machine.state == State.WRITING
    # 进度初始化
    assert pipeline.state_machine.progress.get("total_written", 0) == 0


def test_ensure_setting_set_is_idempotent(tmp_path: Path) -> None:
    """已齐备的项目不应被覆盖/重复生成。"""
    # 先用一次引导生成全套
    p1 = AgenticPipelineWorkflow(
        project_dir=tmp_path, brief="b", planner=_StubPlanner(_make_plan()),
        llm_client=_FakeLLM(),
    )
    p1._ensure_setting_set()
    world_text_1 = (tmp_path / "world.md").read_text(encoding="utf-8")

    # 再调用一次（模拟续写/重入）
    p2 = AgenticPipelineWorkflow(
        project_dir=tmp_path, brief="b", planner=_StubPlanner(_make_plan()),
        llm_client=_FakeLLM(),
    )
    p2._ensure_setting_set()
    world_text_2 = (tmp_path / "world.md").read_text(encoding="utf-8")

    assert world_text_1 == world_text_2  # 未重复改写
    assert is_architecture_confirmed(tmp_path) is True
    assert SettingManager(tmp_path).list_sublines()


def test_ensure_setting_set_preserves_existing_progress(tmp_path: Path) -> None:
    """已有进度（续写场景）不应被重置为 0。"""
    sm = SettingManager(tmp_path)
    sm.save_world({"title": "旧作", "genre": "modern", "style": {}}, "# 旧 world\n")
    arch_file = tmp_path / "architecture.md"
    arch_file.write_text(
        "---\nconfirmed: true\n---\n# 架构\n", encoding="utf-8"
    )
    sm.save_subline(
        "S01_主线",
        {"subline_name": "主线", "characters": []},
        "# 支线\n\n## 支线目标\n推进\n\n## 剧集压力曲线\n| 阶段 | 章节 | 张力等级 |\n|---|---|---|\n| 铺垫 | 1-100 | 低 |\n",
    )
    # 预置进度
    from agent.core.state_machine import StateMachine

    st = StateMachine(tmp_path)
    st.state = State.WRITING
    st.progress = {"total_written": 5, "current_chapter": 5}
    st.save()

    p = AgenticPipelineWorkflow(
        project_dir=tmp_path, brief="b", planner=_StubPlanner(_make_plan()),
        llm_client=_FakeLLM(),
    )
    p._ensure_setting_set()
    p.state_machine.load()
    assert p.state_machine.progress.get("total_written") == 5  # 进度保留
