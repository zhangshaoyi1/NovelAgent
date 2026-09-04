"""Phase 0 离线单元测试（无需网络 / API key）

覆盖：
- 结构化输出：pydantic→json_schema、extract_json 回退
- Tool 注册表：内置工具注册、MCP manifest、call 包装异常
- 内置工具：count_words / rag_retrieve / get_setting / quality_check / export_chapters
  （均在无索引、无章节的临时项目下验证，不触发网络）
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from pydantic import BaseModel

from agent.core.base.structured_output import (
    StructuredOutputError,
    extract_json,
    pydantic_to_json_schema,
)
from agent.core.tools import registry
from agent.core.tools.builtins import set_project_context
# D-I（2026-08-29）：export_chapters 工具已迁至 workflows 层注册，
# 导入 m11_export 以触发 @tool 注册（core 不再依赖 workflows）。
from agent.workflows.evaluation.m11_export import export_chapters  # noqa: F401, E501


# ---------------------------------------------------------------------------
# 结构化输出
# ---------------------------------------------------------------------------
class _ChapterMeta(BaseModel):
    chapter_title: str
    word_count: int
    passed: bool


def test_pydantic_to_json_schema_adds_required_and_closed():
    schema = pydantic_to_json_schema(_ChapterMeta)
    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    # 所有属性应进入 required（strict 友好）
    assert set(schema["required"]) == {"chapter_title", "word_count", "passed"}
    assert "properties" in schema


def test_extract_json_plain():
    assert extract_json('{"a": 1}') == {"a": 1}


def test_extract_json_fenced():
    text = '好的，这是结果：\n```json\n{"a": 1, "b": "x"}\n```\n完毕'
    assert extract_json(text) == {"a": 1, "b": "x"}


def test_extract_json_trailing_text():
    text = '{"a":1} 这是模型在 JSON 后追加的解释文字'
    assert extract_json(text) == {"a": 1}


def test_extract_json_invalid_raises():
    import pytest

    with pytest.raises(StructuredOutputError):
        extract_json("完全不是 json 的乱码文本")


def test_extract_json_draft_with_literal_newlines():
    """容错：draft 字符串内部含**真实换行**（创作模型常见未转义换行），必须能解析。

    回归：ch237 的完整稿因 draft 内含字面换行被所有 json.loads 策略拒绝，导致
    完整稿在解析层被反复丢弃后重做。修复后应在不破坏既有转义的前提下转义成功。
    """
    body = "木门被他轻轻推开，\n发出呻吟。石上旧痕\\n保持。"  # 真实换行 + 既有\\n转义
    raw = '{"think":"x","action":"finish","tool":null,"args":{},"draft":"# 第237章\n' + body + '"}'
    data = extract_json(raw)
    draft = data["draft"]
    # 真实换行最终应保留为换行（经转义修复 + json.loads 解码）
    assert draft.count("\n") == 3, draft
    # 不应残留字面 \\n 双重转义伪影，既有文字完整
    assert "\\n" not in draft, draft
    assert "保持" in draft


def test_extract_json_prose_prefix_then_envelope():
    """加固：创作模型先吐一段纯文本规划（内含杂散 ``{`` / ``"``），随后才是
    合法 JSON 信封，且信封 draft 内还含**真实换行**。

    回归：ch237/ch238 实证——散文前置使策略 3/4 首定位落错起点而整体失败。
    加固（逐「{」扫描候选 + 候选级转义）后应定位到信封并成功解析。
    """
    # 散文里有 ASCII 引号和杂散 ``{``，会让旧策略的括号/字符串状态错位
    prose = '先写规划：考虑"戏剧冲突"和后续 {\n需要保留悬念再吐信封：\n'
    body = "# 第238章\n旧债如雪覆残局\n\n木屋的门被轻轻推开。石上旧痕\\n保持。"  # 真实换行 + 既有 \\n 转义
    env = '{"think":"x","action":"finish","tool":null,"args":{},"draft":"' + body + '"}'
    data = extract_json(prose + env)
    assert data["action"] == "finish"
    assert data["draft"].startswith("# 第238章")
    assert "旧债如雪覆残局" in data["draft"]
    assert "保持" in data["draft"]
    # 真实换行保留、无双重转义伪影
    assert "\\n" not in data["draft"]


# ---------------------------------------------------------------------------
# Tool 注册表
# ---------------------------------------------------------------------------
def test_builtin_tools_registered():
    names = set(registry.names())
    expected = {
        "rag_retrieve",
        "get_setting",
        "count_words",
        "quality_check",
        "foreshadow_read",
        "export_chapters",
    }
    assert expected.issubset(names)


def test_tool_manifest_is_mcp_shaped():
    for t in registry.list():
        m = t.to_mcp_manifest()
        assert {"name", "description", "inputSchema"} <= set(m.keys())


def test_registry_call_unknown_returns_failure():
    res = registry.call("does_not_exist", foo=1)
    assert res.success is False
    assert "未知工具" in (res.error or "")


def test_tool_exception_wrapped():
    # count_words 若传非字符串会抛异常，应被包装为失败而非上抛
    res = registry.call("count_words", text=12345)  # type: ignore[arg-type]
    assert res.success is False
    assert res.error


# ---------------------------------------------------------------------------
# 内置工具（临时项目，离线）
# ---------------------------------------------------------------------------
def _make_empty_project() -> Path:
    d = Path(tempfile.mkdtemp())
    set_project_context(d)
    return d


def test_count_words():
    res = registry.call("count_words", text="hello世界abc")
    assert res.success
    assert res.data["total_chars"] == 10  # h e l l o(5) 世界(2) a b c(3) = 10
    assert res.data["cjk_chars"] == 2


def test_rag_retrieve_empty_project():
    _make_empty_project()
    res = registry.call("rag_retrieve", query="第一章 开头", top_k=3)
    assert res.success
    assert res.data == []  # 无索引 → 空列表（不阻断）


def test_get_setting_world_empty():
    d = _make_empty_project()
    res = registry.call("get_setting", kind="world")
    assert res.success
    assert res.data["exists"] is False
    # 写一个 world.md 后可读
    (d / "world.md").write_text(
        "---\ntitle: 测试书\n---\n\n这是一个测试世界观。", encoding="utf-8"
    )
    res2 = registry.call("get_setting", kind="world")
    assert res2.data["exists"] is True
    assert res2.data["metadata"].get("title") == "测试书"


def test_quality_check_offline():
    _make_empty_project()
    text = "第一章。夜色深沉，他握紧了手中的剑。\n" * 5
    res = registry.call("quality_check", chapter_text=text)
    assert res.success
    assert isinstance(res.data["passed"], bool)
    assert isinstance(res.data["issues"], list)


def test_export_chapters_empty():
    _make_empty_project()
    res = registry.call("export_chapters", fmt="txt")
    assert res.success is True  # 工具调用本身成功（被包装）
    assert res.data["success"] is False  # 内部导出结果为"无章节"
    assert "无可导出" in res.data["message"]
