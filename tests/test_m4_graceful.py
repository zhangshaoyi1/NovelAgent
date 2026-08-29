"""M4 解析失败加固验证：注入非 JSON / 非 dict 响应

纯离线：构造一个持续返回纯文本（非 JSON）的 fake LLM；M4 ``_llm_generate_characters``
解析失败自动重试一次，重试仍失败则响亮抛错（对齐 M1/M14/M3 策略），
绝不静默降级为占位角色——否则真实角色设计会被静默丢弃（生成类写操作失败要响亮报错）。
"""

from __future__ import annotations

import frontmatter
from pathlib import Path

import pytest

from agent.client import LLMResponse
from agent.core.story.setting_manager import SettingManager
from agent.core.engine.state_machine import State, StateMachine
from agent.workflows.m4_character import M4CharacterWorkflow


class _NonJsonLLM:
    """返回纯文本（非 JSON）的 fake LLM：模拟 LLM 抽风返回不可解析内容。"""

    def chat_creative(self, messages, **kwargs) -> LLMResponse:
        return LLMResponse(
            text="我觉得这个主角应该很热血，先写一段背景吧，没必要给 JSON。",
            usage={"prompt_tokens": 1, "completion_tokens": 1},
            model="fake",
        )


def _seed_project(tmp_path: Path) -> StateMachine:
    """前置：world.md / architecture.md(confirmed) / outline.md / state=OUTLINING。"""
    sm = SettingManager(tmp_path)
    sm.save_world(
        {"title": "测试作", "genre": "xiuxian", "style": {}},
        "# world\n\n## 故事简介\n测试简介\n\n## 金手指登记\n无\n",
    )
    arch = tmp_path / "architecture.md"
    post = frontmatter.Post(
        "# arch\n", title="测试作", confirmed=True, version=1, architecture={}
    )
    arch.write_text(frontmatter.dumps(post), encoding="utf-8")
    outline = tmp_path / "outline.md"
    outline.write_text(
        "---\nsublines: []\n---\n\n# 大纲\n\n## 故事简介\n测试简介\n",
        encoding="utf-8",
    )
    st = StateMachine(tmp_path)
    st.state = State.OUTLINING
    st.save()
    return st


def test_m4_parse_failure_raises(tmp_path: Path) -> None:
    """持续非 JSON 响应：两次尝试后必须响亮抛错，绝不静默写占位角色。"""
    st = _seed_project(tmp_path)
    sm = SettingManager(tmp_path)
    wf = M4CharacterWorkflow(
        tmp_path,
        llm_client=_NonJsonLLM(),
        setting_manager=sm,
        state_machine=st,
    )
    with pytest.raises(RuntimeError):
        wf.run()
    # 不落盘占位角色（不产生残缺产物）
    assert not (tmp_path / "characters").exists() or not list(
        (tmp_path / "characters").glob("*.md")
    ), "失败时不应写入占位角色文件"


def test_m4_non_dict_raises(tmp_path: Path) -> None:
    """返回 JSON 但顶层是 list（非 dict）：两次尝试后也应响亮抛错。"""

    class _ListLLM:
        def chat_creative(self, messages, **kwargs) -> LLMResponse:
            return LLMResponse(
                text='[{"name": "错误结构"}]',
                usage={"prompt_tokens": 1, "completion_tokens": 1},
                model="fake",
            )

    st = _seed_project(tmp_path)
    sm = SettingManager(tmp_path)
    wf = M4CharacterWorkflow(
        tmp_path,
        llm_client=_ListLLM(),
        setting_manager=sm,
        state_machine=st,
    )
    with pytest.raises(RuntimeError):
        wf.run()
    assert not (tmp_path / "characters").exists() or not list(
        (tmp_path / "characters").glob("*.md")
    ), "失败时不应写入占位角色文件"
