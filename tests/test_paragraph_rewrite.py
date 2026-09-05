"""P1-7 段落级局部重写（core/quality/rewrite/paragraph_rewriter.py + CLI 注册）。

覆盖：
- split_paragraphs / locate：序号定位、片段唯一定位、越界/未命中/歧义报错；
- plan：离线 dry-run（不调 LLM），方案含上下文窗口；章标题行不计入段落序号；
- rewrite：fake LLM 下仅替换目标段、其余段原样、标题保留、diff 非空、备份生成；
- 确认闸口：confirm_fn=False 不调 LLM 不落盘；
- LLM 失败/空输出优雅降级（保留原段）；
- diff 输出格式（unified diff 头）；
- CLI 命令注册（rewrite_paragraph 可被发现）。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import frontmatter
import pytest

from agent.core.quality.rewrite.paragraph_rewriter import (
    ParagraphRewriter,
    make_diff,
    split_paragraphs,
)


BODY = (
    "# 第 3 章 · 夜探\n\n"
    "第一段：林寻推门而入，院中无人。\n\n"
    "第二段：太虚镜微微发烫，镜面泛起乱码。\n\n"
    "第三段：他蹲下身，指尖触到一撮焦黑的灰。\n\n"
    "第四段：远处传来更夫的梆子声。"
)


def _make_project(tmp_path: Path) -> Path:
    d = tmp_path / "p"
    (d / "chapters").mkdir(parents=True)
    (d / "world.md").write_text("---\ntitle: 书\n---\n# 世界\n", encoding="utf-8")
    post = frontmatter.Post(BODY, chapter=3, title="夜探")
    (d / "chapters" / "ch003.md").write_text(frontmatter.dumps(post), encoding="utf-8")
    return d


def _fake_llm(text: str = "重写后的第二段：镜面乱码骤然清晰。", *, fail: bool = False) -> MagicMock:
    llm = MagicMock()
    if fail:
        llm.chat.side_effect = RuntimeError("network down")
    else:
        llm.chat.return_value = SimpleNamespace(text=text)
    return llm


# ---------------------------------------------------------------- 拆分与定位
def test_split_paragraphs_ignores_heading_and_blanks() -> None:
    body = "# 第 1 章 · 标题\n\n段一。\n\n\n段二。\n"
    paras = split_paragraphs(body.split("\n", 1)[1])
    assert paras == ["段一。", "段二。"]


def test_locate_by_index_and_snippet(tmp_path: Path) -> None:
    d = _make_project(tmp_path)
    rw = ParagraphRewriter(d)
    assert rw.locate(3, "2") == 2
    assert rw.locate(3, "指尖触到一撮焦黑的灰") == 3


def test_locate_errors(tmp_path: Path) -> None:
    d = _make_project(tmp_path)
    rw = ParagraphRewriter(d)
    with pytest.raises(ValueError, match="越界"):
        rw.locate(3, "99")
    with pytest.raises(ValueError, match="未在正文中命中"):
        rw.locate(3, "不存在的片段")
    with pytest.raises(ValueError, match="命中 2 处|命中多"):
        rw.locate(3, "的")


# ---------------------------------------------------------------- plan
def test_plan_offline_with_context_window(tmp_path: Path) -> None:
    d = _make_project(tmp_path)
    rw = ParagraphRewriter(d)  # 无 LLM 客户端 → 证明 plan 不依赖 LLM
    scheme = rw.plan(3, "2", "收紧节奏")
    assert scheme["paragraph_index"] == 2
    assert scheme["old_paragraph"].startswith("第二段")
    assert scheme["context_before"].startswith("第一段")
    assert scheme["context_after"].startswith("第三段")
    assert scheme["instruction"] == "收紧节奏"


def test_plan_heading_not_counted_as_paragraph(tmp_path: Path) -> None:
    d = _make_project(tmp_path)
    rw = ParagraphRewriter(d)
    scheme = rw.plan(3, "1", "改")
    assert not scheme["old_paragraph"].startswith("#")


# ---------------------------------------------------------------- rewrite
def test_rewrite_replaces_only_target_paragraph(tmp_path: Path) -> None:
    d = _make_project(tmp_path)
    rw = ParagraphRewriter(d, llm_client=_fake_llm())
    res = rw.rewrite(3, "2", "收紧节奏")

    assert res.applied and res.llm_used and res.diff_text  # diff 非空且为 unified 格式
    assert res.diff_text.splitlines()[0].startswith("--- ")
    new_post = frontmatter.load(d / "chapters" / "ch003.md")
    body = new_post.content
    assert "重写后的第二段" in body
    assert "第一段：林寻推门而入" in body          # 其余段原样
    assert "第四段：远处传来更夫的梆子声" in body
    assert body.startswith("# 第 3 章 · 夜探")     # 标题保留
    assert res.backup_file is not None and res.backup_file.exists()


def test_rewrite_confirm_rejected_no_llm_no_write(tmp_path: Path) -> None:
    d = _make_project(tmp_path)
    llm = _fake_llm()
    rw = ParagraphRewriter(d, llm_client=llm)
    before = (d / "chapters" / "ch003.md").read_text(encoding="utf-8")

    res = rw.rewrite(3, "2", "改", confirm_fn=lambda scheme: False)

    assert res.applied is False and res.error == "confirm_rejected"
    llm.chat.assert_not_called()
    assert (d / "chapters" / "ch003.md").read_text(encoding="utf-8") == before


def test_rewrite_llm_failure_degrades(tmp_path: Path) -> None:
    d = _make_project(tmp_path)
    rw = ParagraphRewriter(d, llm_client=_fake_llm(fail=True))
    res = rw.rewrite(3, "2", "改")
    assert res.applied is False and res.llm_used is False and res.error == "llm_unavailable"
    body = frontmatter.load(d / "chapters" / "ch003.md").content
    assert "第二段：太虚镜微微发烫" in body  # 原段保留


def test_rewrite_llm_empty_output_degrades(tmp_path: Path) -> None:
    d = _make_project(tmp_path)
    rw = ParagraphRewriter(d, llm_client=_fake_llm(text="  \n "))
    res = rw.rewrite(3, "2", "改")
    assert res.applied is False and res.error == "llm_empty_output"


# ---------------------------------------------------------------- diff
def test_make_diff_format() -> None:
    diff = make_diff("旧\n", "新\n")
    assert diff.startswith("--- 旧段落")
    assert "+++ 新段落" in diff
    assert make_diff("同\n", "同\n") == ""


# ---------------------------------------------------------------- CLI 注册
def test_cli_command_registered() -> None:
    """与 test_m13_foreshadow 同口径：`agent.cli` 模块属性暴露命令函数。"""
    from agent import cli as cli_module

    assert callable(getattr(cli_module, "rewrite_paragraph", None))
