"""G12 爽点剧本测试（T7 验收，纯离线零 LLM）。

覆盖（对齐 G12/设计.md §2 / §9 T1-T2）：
- build_payoff_script 确定性（同输入同输出）+ 章节数 + 类型池合法性 + 强度钳制；
- load_payoff_script 三态（存在/缺失/损坏/enabled=False）；
- chapter_payoff 取本章条目（有/无匹配）；
- _build_task 注入【爽点剧本】【情绪目标】段；payoff_enabled=False 不注入。
"""

from __future__ import annotations

from pathlib import Path

from agent.core.payoff_script import (
    PAYOFF_TYPE_POOL,
    build_payoff_script,
    chapter_payoff,
    load_payoff_script,
    save_payoff_script,
)


def _min_ctx(**overrides) -> dict:
    wi = {
        "title": "测试之书", "tone": "热血", "pov": "第三人称", "rhythm": "快",
        "chapter_length": "2000", "info_density": "中", "banned_elements": "",
        "synopsis": "简介", "realm_system": "境界", "golden_finger_info": "金手指",
    }
    ctx = {
        "world_info": wi, "chapter_num": 5, "subline_id": "S01", "subline_name": "支线一",
        "subline_goal": "目标", "pressure_stage": "发展", "tension_level": "中",
        "route_node_id": "N1", "route_milestone": "M", "route_main_title": "主线",
        "route_main_result": "R", "route_main_growth": "G", "characters_info": "C",
        "relations_info": "R", "foreshadow_task": "F", "prev_chapter_summary": "P",
        "payoff_task": "", "emotion_target": "", "reader_signals": [],
    }
    ctx.update(overrides)
    return ctx


# ---------------------------------------------------------------- build_payoff_script
def test_build_deterministic() -> None:
    a = build_payoff_script(30)
    b = build_payoff_script(30)
    assert a == b  # 同输入恒同输出


def test_build_chapter_count_and_fields() -> None:
    items = build_payoff_script(30)
    assert len(items) == 30
    for it in items:
        assert it["chapter"] >= 1 and it["chapter"] <= 30
        assert it["payoff_type"] in sum(PAYOFF_TYPE_POOL.values(), [])
        assert 1 <= it["intensity"] <= 5
        assert 1 <= it["tension"] <= 5
        assert it["emotion"]
        assert it["note"]


def test_build_ending_no_new_lines() -> None:
    """结局段类型池禁新开线（揭密/情感/收束）。"""
    items = build_payoff_script(30)
    ending_ok = {"揭密", "情感", "收束"}
    for it in items:
        if it["chapter"] >= 28:  # 90% 之后
            assert it["payoff_type"] in ending_ok


def test_build_clamp() -> None:
    items = build_payoff_script(2)  # 极小书：全部落铺垫/结局
    for it in items:
        assert 1 <= it["intensity"] <= 5


# ---------------------------------------------------------------- load / save
def test_load_three_states(tmp_path: Path) -> None:
    # 缺失 → 空
    assert load_payoff_script(tmp_path)["chapters"] == []
    # 保存后可读
    items = build_payoff_script(10)
    save_payoff_script(tmp_path, items)
    loaded = load_payoff_script(tmp_path)
    assert len(loaded["chapters"]) == 10
    # 损坏 → 空
    (tmp_path / ".state" / "payoff_script.json").write_text("{not json", encoding="utf-8")
    assert load_payoff_script(tmp_path)["chapters"] == []
    # enabled=False → 空
    save_payoff_script(tmp_path, items)
    assert load_payoff_script(tmp_path, enabled=False)["chapters"] == []


def test_chapter_payoff() -> None:
    script = {"chapters": [{"chapter": 3, "payoff_type": "打脸", "intensity": 4,
                            "emotion": "爽", "tension": 4, "note": "发展·第3章·打脸×4"}]}
    task, emo = chapter_payoff(script, 3)
    assert "打脸" in task and "强度 4/5" in task
    assert "爽" in emo and "张力 4/5" in emo
    # 无匹配章节 → 空
    assert chapter_payoff(script, 9) == ("", "")


# ---------------------------------------------------------------- 注入
def test_build_task_injects_payoff() -> None:
    from agent.workflows.agentic_write import AgenticWriteWorkflow

    wf = AgenticWriteWorkflow(Path("."), llm_client=None)
    task = wf._build_task(_min_ctx(payoff_task="本章爽点：打脸（强度 4/5）", emotion_target="情绪目标：爽（张力 4/5）"))
    assert "# 爽点剧本" in task
    assert "# 情绪目标" in task
    assert "打脸" in task and "爽（张力 4/5）" in task


def test_build_task_no_payoff_byte_identical() -> None:
    from agent.workflows.agentic_write import AgenticWriteWorkflow

    wf = AgenticWriteWorkflow(Path("."), llm_client=None)
    task = wf._build_task(_min_ctx())
    assert "# 爽点剧本" not in task
    assert "# 情绪目标" not in task


def test_generate_chapter_injects_payoff() -> None:
    from agent.workflows.m5_write_chapter import M5WriteChapterWorkflow

    class _LLM:
        def __init__(self) -> None:
            self.messages: list[dict] = []

        def chat_creative(self, messages, *args, **kwargs):
            self.messages = messages
            return type("R", (), {"text": "正文。"})()

    llm = _LLM()
    wf = M5WriteChapterWorkflow(Path("."), llm_client=llm, pre_validate=False)
    wf._generate_chapter(
        _min_ctx(payoff_task="本章爽点：揭密（强度 5/5）", emotion_target="情绪目标：燃（张力 5/5）")
    )
    system = llm.messages[0]["content"]
    assert "# 爽点剧本" in system and "揭密" in system
    assert "# 情绪目标" in system and "燃（张力 5/5）" in system


def test_payoff_disabled_ctx(tmp_path: Path) -> None:
    """payoff_enabled=False（--no-payoff）→ 不读剧本 → ctx 无爽点任务。"""
    from tests.conftest import _build_minimal_project

    from agent.workflows.m5_write_chapter import M5WriteChapterWorkflow

    proj = _build_minimal_project(tmp_path)
    save_payoff_script(proj, build_payoff_script(12))
    wf = M5WriteChapterWorkflow(proj, llm_client=None, pre_validate=False, payoff_enabled=False)
    ctx = wf._load_context()
    assert ctx.get("payoff_task") == ""
    # 对照：默认开 → 有值
    wf2 = M5WriteChapterWorkflow(proj, llm_client=None, pre_validate=False)
    ctx2 = wf2._load_context()
    assert ctx2.get("payoff_task") != ""
