"""提示词缺口修复 + 续写补字测试（2026-09-10）。

背景（五灵破归档 4 次回滚 + 3 次过短失败的复盘）：
- 缺口1：agentic 主路径任务模板无角色生死/时间线硬约束（character_constraints）
  → Writer 长期看不到角色状态真源 → 设定一致/人设稳定反复不达标
- 缺口2：RAG 召回 12 片但渲染只取 3 片×120 字≈360 字，证据近乎全丢
- 缺口3：思考型模型思考 token 吃输出预算 → 间歇性过短章节，重写同样过短
  → 整章放弃（ch25/ch155 实证）；续写补字把「重写」降级为「追加」
"""

from pathlib import Path
from types import SimpleNamespace

from rich.console import Console

from agent.core.infra.prompt_manager import pm
from agent.workflows.writing.agentic_write import AgenticWriteWorkflow


def _min_ctx(style_guide: str = "", character_constraints: str = "",
             rag_chunks: list | None = None) -> dict:
    """最小 ctx（对齐 test_g11_style._min_ctx，另加本次修复的字段）。"""
    wi = {
        "title": "测试之书",
        "tone": "热血",
        "pov": "第三人称",
        "rhythm": "快节奏",
        "chapter_length": "2000",
        "info_density": "中等",
        "banned_elements": "",
        "synopsis": "简介",
        "realm_system": "境界",
        "golden_finger_info": "金手指",
    }
    return {
        "world_info": wi,
        "chapter_num": 3,
        "subline_id": "S01",
        "subline_name": "支线一",
        "subline_goal": "目标",
        "pressure_stage": "发展",
        "tension_level": "中",
        "route_node_id": "N1",
        "route_milestone": "里程碑",
        "route_main_title": "主线",
        "route_main_result": "结果",
        "route_main_growth": "成长",
        "characters_info": "角色信息",
        "relations_info": "关系网",
        "foreshadow_task": "伏笔任务",
        "prev_chapter_summary": "前情提要",
        "style_guide": style_guide,
        "character_constraints": character_constraints,
        "rag_context": rag_chunks or [],
    }


def _chunk(text: str, chapter: int = 0, kind: str = "character") -> SimpleNamespace:
    return SimpleNamespace(
        source=f"characters/c{chapter}.md", chapter_num=chapter, kind=kind, text=text
    )


def _wf() -> "AgenticWriteWorkflow":
    from tests.test_g11_style import _CaptureLLM

    return AgenticWriteWorkflow(Path("."), llm_client=_CaptureLLM())


# ---------------------------------------------------------------- 缺口1：硬约束注入
def test_build_task_injects_character_constraints() -> None:
    """ctx 带 character_constraints → 任务追加 g.character_state_constraint 渲染段。"""
    wf = _wf()
    task = wf._build_task(_min_ctx(character_constraints="林惊澜：已死亡（第 40 章），不得复活"))
    expected = pm.get("g.character_state_constraint").render_user(
        character_constraints="林惊澜：已死亡（第 40 章），不得复活"
    )
    assert expected in task
    assert "林惊澜" in task


def test_build_task_no_constraints_no_section() -> None:
    """ctx 无 character_constraints → 不追加该段（空串不渲染占位符）。"""
    wf = _wf()
    task = wf._build_task(_min_ctx())
    assert pm.get("g.character_state_constraint").render_user(character_constraints="") not in task \
        or "character_constraints" not in task


# ---------------------------------------------------------------- 缺口2：RAG 渲染放宽
def test_build_task_rag_renders_8_chunks_300_chars() -> None:
    """8 片×300 字内全部进任务：第 8 片可见、超 120 字的文本不再被截到 120。"""
    chunks = [_chunk(f"事实{i}" * 10, chapter=i) for i in range(7)]
    chunks.append(_chunk("长" * 250, chapter=98))  # 第 8 片：250 字长文本不截断
    chunks.append(_chunk("事实9" * 10, chapter=99))  # 第 9 片应被丢弃
    task = _wf()._build_task(_min_ctx(rag_chunks=chunks))
    assert "事实6" in task  # 第 7 片保留
    assert "长" * 200 in task  # 第 8 片 250 字文本未截断到 120
    # 第 9 片丢弃：其唯一标识（chapter=99 的 source）不出现
    assert task.count("c99.md") == 0


def test_build_task_rag_truncates_beyond_300() -> None:
    """单条超 300 字仍截断到 300（防单条挤占预算）。"""
    long_text = "超" * 500
    task = _wf()._build_task(_min_ctx(rag_chunks=[_chunk(long_text)]))
    assert "超" * 300 in task
    assert "超" * 301 not in task


# ---------------------------------------------------------------- 缺口3：续写补字
def _patch_chat_creative(monkeypatch, pieces: list[str], calls: list[str]) -> None:
    """monkeypatch writer_agent 内部引用的 chat_creative（方法内延迟导入）。"""
    import agent.client.gateway_adapter as gw

    def fake_chat_creative(llm, messages=None, **kwargs):
        user = messages[-1]["content"] if messages else ""
        calls.append(user)
        return pieces.pop(0) if pieces else ""

    monkeypatch.setattr(gw, "chat_creative", fake_chat_creative)


def _agent() -> object:
    from agent.agents.writer_agent import WriterAgent

    return WriterAgent(
        project_dir=".",
        tier="auto",
        decide=lambda m: None,  # 不会被用到：直接测 _top_up_length
        console=Console(),
    )


def test_top_up_merges_continuation(monkeypatch) -> None:
    """过短稿 + 一轮合格续写 → 拼接达标且只调一次。"""
    calls: list[str] = []
    _patch_chat_creative(monkeypatch, ["续" * 250], calls)
    agent = _agent()
    base = "短" * 100
    out = agent._top_up_length(base, target_words=300, min_words=250)
    assert out.startswith(base)
    assert "续" * 10 in out
    assert len(calls) == 1


def test_top_up_skips_tiny_piece(monkeypatch) -> None:
    """续写输出 <200 字视为无效轮，跳过不拼接。"""
    calls: list[str] = []
    _patch_chat_creative(monkeypatch, ["微" * 10, "微" * 10], calls)
    agent = _agent()
    base = "短" * 100
    out = agent._top_up_length(base, target_words=300, min_words=250)
    assert out == base
    assert len(calls) == 2  # 两轮都试了


def test_top_up_stops_at_target(monkeypatch) -> None:
    """达标后不再续写（省 token）。"""
    calls: list[str] = []
    _patch_chat_creative(monkeypatch, ["续" * 400, "续" * 400], calls)
    agent = _agent()
    out = agent._top_up_length("短" * 100, target_words=300, min_words=250)
    assert len(calls) == 1
    assert out.count("续") >= 250


def test_run_fallback_tops_up_before_raise(monkeypatch, tmp_path: Path) -> None:
    """端到端：门禁持续判过短 → 旧逻辑 raise；新逻辑先续写补字，达标后正常返回。"""
    from agent.agents.writer_agent import WriterAgent

    calls: list[str] = []
    _patch_chat_creative(monkeypatch, ["补" * 2000], calls)

    def always_short_gate(text: str, ctx) -> tuple[bool, dict]:
        return False, {
            "overall_pass": False,
            "issues": [{"rule_id": "min_length", "severity": "blocking",
                        "description": "过短"}],
        }

    agent = WriterAgent(
        project_dir=".",
        tier="auto",
        decide=lambda m: None,
        quality_gate=always_short_gate,
        console=Console(),
        targeted_revise=False,  # 聚焦验证兜底补字路径，排除定向修订消耗 mock 片段
    )
    monkeypatch.setattr(
        agent, "_draft", lambda task, critique=None, min_words=None, max_words=None: "稿" * 300
    )
    text, rev, passed = agent.run("写一章", ctx={"chapter_length": "2000"})
    assert "补" * 10 in text  # 续写内容已拼入
    assert passed is False  # 标记未通过，但不放弃落盘
    assert len(calls) == 1
