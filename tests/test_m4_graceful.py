"""M4 硬编码崩点修复验证（P0-4）：注入非 JSON / 非 dict 响应

纯离线：构造一个返回纯文本（非 JSON）的 fake LLM；M4 ``_llm_generate_characters``
必须优雅降级（对齐 M1/M14/M3 的 ValueError 兜底），``run()`` 不 raise，且产出
至少 1 个占位角色（M4 模板占位，非"主线待补充"空壳），下游不阻断。
"""

from __future__ import annotations

import frontmatter
from pathlib import Path

from agent.core.llm_client import LLMResponse
from agent.core.setting_manager import SettingManager
from agent.core.state_machine import State, StateMachine
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


def test_m4_graceful_non_json_does_not_raise(tmp_path: Path) -> None:
    st = _seed_project(tmp_path)
    sm = SettingManager(tmp_path)
    wf = M4CharacterWorkflow(
        tmp_path,
        llm_client=_NonJsonLLM(),
        setting_manager=sm,
        state_machine=st,
    )
    # 非 JSON 响应不应导致 run() 抛出
    result = wf.run()
    assert result is not None

    # 至少 1 个占位角色（M4 模板占位，含必填字段）
    char_files = list((tmp_path / "characters").glob("*.md"))
    assert char_files, "非 JSON 响应下应渲染至少 1 个占位角色"
    content = char_files[0].read_text(encoding="utf-8")
    for kw in ("身份", "核心动机", "弧光", "语言指纹", "关系"):
        assert kw in content, f"占位角色缺衔接字段：{kw}"

    # 下游产物也应正常落盘（不阻断）
    assert (tmp_path / "relations" / "graph.md").exists()
    assert (tmp_path / "foreshadows.md").exists()
    assert (tmp_path / "golden_finger_registration.md").exists()


def test_m4_graceful_non_dict_does_not_raise(tmp_path: Path) -> None:
    """返回 JSON 但顶层是 list（非 dict）也应降级不崩。"""

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
    result = wf.run()
    assert result is not None
    char_files = list((tmp_path / "characters").glob("*.md"))
    assert char_files, "非 dict 响应下应渲染至少 1 个占位角色"
