"""A3 反馈→定向改写 —— 离线测试

用 conftest.make_project 搭建含章节的项目，注入假 LLM（MagicMock spec=LLMClient），
覆盖：成功重写 / 上下文锚点透传 / BLOCK 门禁拦截 / advisory 放行带告警 /
LLM 失败优雅降级 / 缺章报错 / 偏好沉淀 / AgentService 接线。
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import frontmatter
import pytest

from agent.core.llm_client import LLMClient, LLMResponse
from agent.core.feedback_rewriter import FeedbackRewriter, RewriteResult
from agent.service.agent_service import AgentService


# ============================================================
# 假 LLM
# ============================================================
def _fake_llm(rewritten_text: str, *, raise_error: bool = False) -> MagicMock:
    llm = MagicMock(spec=LLMClient)
    if raise_error:
        llm.chat_creative.side_effect = RuntimeError("network down")
    else:
        llm.chat_creative.return_value = LLMResponse(
            text=rewritten_text,
            raw={},
            usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        )
    return llm


REWRITTEN = "（重写版）林寻咬破舌尖，精血沁入镜面，这一章被精准收紧了节奏。"


# ============================================================
# 测试
# ============================================================
def test_rewrite_success(tmp_path):
    d = __import__("tests.conftest", fromlist=["make_project"]).make_project(tmp_path, n_chapters=3)
    rewriter = FeedbackRewriter(d, llm_client=_fake_llm(REWRITTEN))
    res = rewriter.rewrite(2, "节奏太慢，删水")

    assert isinstance(res, RewriteResult)
    assert res.llm_used is True
    assert res.blocked is False
    assert res.guardrail_passed is True
    assert res.new_text == REWRITTEN
    assert res.new_word_count == len(REWRITTEN.replace("\n", "").replace(" ", ""))
    # 落盘
    ch_file = d / "chapters" / "ch002.md"
    assert REWRITTEN in ch_file.read_text(encoding="utf-8")
    # 备份
    assert res.backup_file is not None and res.backup_file.exists()
    # frontmatter 改写痕迹
    post = frontmatter.load(ch_file)
    assert post.metadata.get("revision_count") == 1
    assert post.metadata.get("last_rewrite_feedback") == "节奏太慢，删水"


def test_rewrite_context_anchors_passed(tmp_path):
    d = __import__("tests.conftest", fromlist=["make_project"]).make_project(tmp_path, n_chapters=3)
    llm = _fake_llm(REWRITTEN)
    rewriter = FeedbackRewriter(d, llm_client=llm)
    rewriter.rewrite(2, "主角太蠢，补动机")

    # chat_creative 被调用，且 prompt 含上下文锚点（上一章尾 / 下一章头 / 题材）
    assert llm.chat_creative.call_count == 1
    kwargs = llm.chat_creative.call_args.kwargs
    user = kwargs["messages"][1]["content"]
    assert "上一章" in user or "衔接上文" in user
    assert "衔接下文" in user
    assert "题材" in user


def test_rewrite_block_gate_rejects(tmp_path):
    d = __import__("tests.conftest", fromlist=["make_project"]).make_project(tmp_path, n_chapters=3)
    # 重写产物含默认合规词 {{ → BLOCK 应拦截
    bad = "{{leak}}" + REWRITTEN
    rewriter = FeedbackRewriter(d, llm_client=_fake_llm(bad))
    res = rewriter.rewrite(2, "测试", gate_mode="block")

    assert res.blocked is True
    assert res.llm_used is True
    assert res.guardrail_passed is False
    assert res.backup_file is None
    # 原章未被改写
    ch_file = d / "chapters" / "ch002.md"
    assert "{{leak}}" not in ch_file.read_text(encoding="utf-8")


def test_rewrite_advisory_allows_with_warning(tmp_path):
    d = __import__("tests.conftest", fromlist=["make_project"]).make_project(tmp_path, n_chapters=3)
    bad = "{{leak}}" + REWRITTEN
    rewriter = FeedbackRewriter(d, llm_client=_fake_llm(bad))
    res = rewriter.rewrite(2, "测试", gate_mode="advisory")

    assert res.blocked is False
    assert res.guardrail_passed is False  # advisory：带告警仍落盘
    ch_file = d / "chapters" / "ch002.md"
    assert "{{leak}}" in ch_file.read_text(encoding="utf-8")


def test_rewrite_llm_failure_degrade(tmp_path):
    d = __import__("tests.conftest", fromlist=["make_project"]).make_project(tmp_path, n_chapters=3)
    ch_file = d / "chapters" / "ch002.md"
    original = frontmatter.load(ch_file).content.strip()
    rewriter = FeedbackRewriter(d, llm_client=_fake_llm("", raise_error=True))
    res = rewriter.rewrite(2, "反馈")

    assert res.llm_used is False
    assert res.error != ""
    assert res.new_text == original  # 回退原章
    # 原章未被改动
    post = frontmatter.load(ch_file)
    assert post.metadata.get("revision_count", 0) == 0


def test_rewrite_missing_chapter(tmp_path):
    d = __import__("tests.conftest", fromlist=["make_project"]).make_project(tmp_path, n_chapters=3)
    rewriter = FeedbackRewriter(d, llm_client=_fake_llm(REWRITTEN))
    with pytest.raises(FileNotFoundError):
        rewriter.rewrite(99, "反馈")


def test_rewrite_records_learning(tmp_path):
    d = __import__("tests.conftest", fromlist=["make_project"]).make_project(tmp_path, n_chapters=3)
    rewriter = FeedbackRewriter(d, llm_client=_fake_llm(REWRITTEN))
    rewriter.rewrite(2, "感情戏不够")

    from agent.core.learning_store import LearningStore

    items = LearningStore(d).load()
    fb = [x for x in items if x.category == "feedback_rewrite"]
    assert len(fb) == 1
    assert "感情戏不够" in fb[0].text


def test_service_rewrite_chapter_wires(tmp_path):
    d = __import__("tests.conftest", fromlist=["make_project"]).make_project(tmp_path, n_chapters=3)
    svc = AgentService(project_dir=d, console=__import__("rich.console", fromlist=["Console"]).Console())
    # 注入假 LLM 到 traced_llm（AgentService 用其构造 FeedbackRewriter）
    svc.traced_llm = _fake_llm(REWRITTEN)

    out = svc.rewrite_chapter(2, "节奏太慢")
    assert out["rewritten"] is True
    assert out["chapter"] == 2
    assert REWRITTEN in (d / "chapters" / "ch002.md").read_text(encoding="utf-8")
