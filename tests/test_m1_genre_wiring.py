"""T-2 M1 题材模板去硬编码 + 选项动态化验收

覆盖：
- GenrePackRegistry.list_genres() 至少含 wuxia / xiuxian。
- M1Input(genre="wuxia") 运行后，world.md 注入武侠模板（含「内力」「江湖」），
  且不含修仙专属境界（炼气/筑基/金丹）——证明不再硬编码 xiuxian 路径。
- M1Input(genre="xiuxian") 仍注入修仙模板（含「炼气」「筑基」「金丹」）。
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from agent.core.registry.genre_pack import GenrePackRegistry
from agent.core.story.setting_manager import SettingManager
from agent.core.engine.state_machine import State, StateMachine
from agent.workflows.planning.m1_config import M1ConfigWorkflow, M1Input


MOCK_GENRE_JSON = {
    "synopsis": "少年初入江湖",
    "worldview": "中原武林，群雄并起",
    "power_system": "内力+招式",
    "factions": "- 少林\n- 武当",
    "golden_finger": "无名剑谱",
}


@pytest.fixture
def mock_llm() -> MagicMock:
    llm = MagicMock()
    llm.chat.return_value = SimpleNamespace(
        text=json.dumps(MOCK_GENRE_JSON, ensure_ascii=False)
    )
    return llm


def _workflow(tmp_path: Path, mock_llm: MagicMock) -> M1ConfigWorkflow:
    return M1ConfigWorkflow(
        project_dir=tmp_path,
        llm_client=mock_llm,
        setting_manager=SettingManager(tmp_path),
        state_machine=StateMachine(tmp_path),
    )


def test_list_genres_includes_wuxia_and_xiuxian() -> None:
    genres = GenrePackRegistry().list_genres()
    assert "wuxia" in genres
    assert "xiuxian" in genres


def test_m1_wuxia_injects_wuxia_template(
    tmp_path: Path, mock_llm: MagicMock
) -> None:
    wf = _workflow(tmp_path, mock_llm)
    user_input = M1Input(
        title="江湖录",
        scope="long",
        genre="wuxia",
        style={"tone": "热血", "pov": "第三人称限制", "rhythm": "快",
               "chapter_length": 3000, "info_density": "中", "banned_elements": []},
        story_core="少年初入江湖",
    )
    result = wf.run(user_input=user_input)

    content = result.world_file.read_text(encoding="utf-8")
    # 武侠模板标记
    assert "内力" in content
    assert "江湖" in content
    # 不应出现修仙专属境界（证明未硬编码 xiuxian 路径）
    assert "炼气" not in content
    assert "筑基" not in content
    assert "金丹" not in content
    # 记录当前题材（T-2：run 中建立 registry 并记 _current_genres，多题材为列表）
    assert wf._current_genres == ["wuxia"]


def test_m1_xiuxian_still_injects_xiuxian_template(
    tmp_path: Path, mock_llm: MagicMock
) -> None:
    wf = _workflow(tmp_path, mock_llm)
    user_input = M1Input(
        title="修仙录",
        scope="long",
        genre="xiuxian",
        style={"tone": "热血", "pov": "第三人称限制", "rhythm": "快",
               "chapter_length": 3000, "info_density": "中", "banned_elements": []},
        story_core="废柴少年修仙",
    )
    result = wf.run(user_input=user_input)

    content = result.world_file.read_text(encoding="utf-8")
    assert "炼气" in content
    assert "筑基" in content
    assert "金丹" in content


def test_m1_unknown_genre_does_not_crash(tmp_path: Path, mock_llm: MagicMock) -> None:
    wf = _workflow(tmp_path, mock_llm)
    user_input = M1Input(
        title="x", scope="short", genre="nonexistent-genre-xyz", story_core="核心"
    )
    # 未知题材：world-template 为空字符串，不应抛异常
    result = wf.run(user_input=user_input)
    assert result.world_file.exists()
