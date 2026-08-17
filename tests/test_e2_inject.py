"""E2 题材动态注入（运行时套路加载到 M5 写作）单元测试

覆盖：
- GenrePackRegistry.load_trope 提取套路片段（精确 + 模糊匹配）
- 缺失套路抛出 ValueError 并附带可用列表
- M5._collect_injected_tropes 在注入为空时返回 ""
- M5._collect_injected_tropes 注入套路后返回非空文本
- M5 生成时把套路拼入 system prompt
- M5 生成成功后自动清除注入（运行时不残留；存于独立 injected_tropes.json）
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from agent.core.injected_trope_store import InjectedTropeStore
from agent.core.llm_client import LLMClient, LLMResponse
from agent.workflows.m5_write_chapter import M5WriteChapterWorkflow

from tests.conftest import (
    CHAPTER_TEXT,
    QUALITY_PASS,
    _build_minimal_project,
    _build_mock_llm,
)


def _build_capturing_llm(
    chapter_text: str = CHAPTER_TEXT,
    quality_report: dict | None = None,
) -> MagicMock:
    """记录 chat_creative 收到的 messages，便于断言 system prompt 注入"""
    import json as _json

    llm = MagicMock(spec=LLMClient)
    captured: list[list[dict]] = []
    qr = quality_report or QUALITY_PASS

    def creative_side_effect(*args, **kwargs):
        messages = kwargs.get("messages") or (args[0] if args else [])
        captured.append(messages)
        return LLMResponse(
            text=chapter_text,
            raw={},
            usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        )

    def utility_side_effect(*args, **kwargs):
        return LLMResponse(
            text=_json.dumps(qr, ensure_ascii=False),
            raw={},
            usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        )

    llm.chat_creative.side_effect = creative_side_effect
    llm.chat_utility.side_effect = utility_side_effect
    llm.captured_messages = captured  # type: ignore[attr-defined]
    return llm


# ============================================================
# GenrePackRegistry.load_trope
# ============================================================
class TestLoadTrope:
    def test_load_trope_exact(self) -> None:
        from agent.core.genre_pack import GenrePackRegistry

        registry = GenrePackRegistry()
        trope = registry.load_trope("xiuxian", "逆袭")
        assert trope.genre == "xiuxian"
        assert "逆袭" in trope.name
        assert trope.text.strip() != ""

    def test_load_trope_fuzzy(self) -> None:
        from agent.core.genre_pack import GenrePackRegistry

        registry = GenrePackRegistry()
        # "绝境逆袭" 应模糊匹配到 "逆袭" 段落
        trope = registry.load_trope("xiuxian", "绝境逆袭")
        assert trope.text.strip() != ""

    def test_load_trope_missing_raises(self) -> None:
        from agent.core.genre_pack import GenrePackRegistry

        registry = GenrePackRegistry()
        try:
            registry.load_trope("xiuxian", "不存在的套路")
            raise AssertionError("期望 ValueError")
        except ValueError as e:
            assert "可用套路" in str(e)


# ============================================================
# M5._collect_injected_tropes（注入存于独立 .state/injected_tropes.json）
# ============================================================
class TestCollectInjectedTropes:
    def test_empty_when_no_injection(self, tmp_path: Path) -> None:
        d = _build_minimal_project(tmp_path)
        InjectedTropeStore(d).clear()  # 确保无注入
        wf = M5WriteChapterWorkflow(project_dir=d, llm_client=_build_mock_llm())
        ctx = wf._load_context()
        assert wf._collect_injected_tropes(ctx) == ""

    def test_returns_text_when_injected(self, tmp_path: Path) -> None:
        d = _build_minimal_project(tmp_path)
        InjectedTropeStore(d).set(["逆袭"])
        wf = M5WriteChapterWorkflow(project_dir=d, llm_client=_build_mock_llm())
        ctx = wf._load_context()
        text = wf._collect_injected_tropes(ctx)
        assert "逆袭" in text
        assert text.strip() != ""

    def test_collect_injected_tropes_bad_name_warns(self, tmp_path: Path) -> None:
        d = _build_minimal_project(tmp_path)
        InjectedTropeStore(d).set(["不存在的套路"])
        wf = M5WriteChapterWorkflow(project_dir=d, llm_client=_build_mock_llm())
        ctx = wf._load_context()
        # 注入失败应返回 ""（不阻断写作），仅告警
        assert wf._collect_injected_tropes(ctx) == ""


# ============================================================
# 集成：注入到 system prompt + 生成后清除
# ============================================================
class TestInjectIntegration:
    def test_trope_injected_into_system_prompt(self, tmp_path: Path) -> None:
        d = _build_minimal_project(tmp_path)
        InjectedTropeStore(d).set(["逆袭"])

        llm = _build_capturing_llm()
        wf = M5WriteChapterWorkflow(project_dir=d, llm_client=llm)
        # 跳过 E3 门禁（无 arbiter 时 pre_validate 自动跳过）
        wf.run()

        # 第一次 chat_creative 的 system prompt 应包含注入套路
        first_messages = llm.captured_messages[0]
        system_content = first_messages[0]["content"]
        assert "逆袭" in system_content
        assert "注入套路" in system_content

    def test_injected_tropes_cleared_after_run(self, tmp_path: Path) -> None:
        d = _build_minimal_project(tmp_path)
        store = InjectedTropeStore(d)
        store.set(["逆袭"])
        assert store.get() == ["逆袭"]

        wf = M5WriteChapterWorkflow(project_dir=d, llm_client=_build_mock_llm())
        wf.run()

        # 生成后运行时注入应被清除
        assert InjectedTropeStore(d).get() == []
