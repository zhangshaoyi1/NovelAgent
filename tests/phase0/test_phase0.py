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

from agent.core.structured_output import (
    StructuredOutputError,
    extract_json,
    pydantic_to_json_schema,
)
from agent.core.tools import registry
from agent.core.tools.builtins import set_project_context


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
