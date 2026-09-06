"""世界观讨论工作流（WorldDiscussWorkflow）单元测试

覆盖：
- world.md 缺失时拒绝讨论
- 讨论追加 world_discussion.md 记录（不驱动状态机）
- --apply 按讨论结论重写 world.md 正文并保留 frontmatter
- 无讨论记录时 apply 拒绝
"""

from __future__ import annotations

import frontmatter
import pytest

from agent.workflows.planning.world_discuss import WorldDiscussWorkflow


def _fake_llm(reply: str):
    class FakeLLM:
        def chat(self, req):
            class R:
                text = reply

            return R()

    return FakeLLM()


def _make_project(tmp_path, world_body: str = "# 世界观\n\n## 力量体系\n炼气。"):
    post = frontmatter.Post(world_body, title="测试书", genre_label="修仙")
    (tmp_path / "world.md").write_text(frontmatter.dumps(post), encoding="utf-8")
    return tmp_path


def test_discuss_requires_world_md(tmp_path):
    w = WorldDiscussWorkflow(tmp_path)
    with pytest.raises(RuntimeError, match="world.md 不存在"):
        w.run(message="聊聊金手指")


def test_discuss_appends_log_without_state_change(tmp_path):
    proj = _make_project(tmp_path)
    w = WorldDiscussWorkflow(proj, llm_client=_fake_llm("建议改为吞噬型体质。"))
    r = w.run(message="金手指太弱了")
    assert "吞噬型体质" in r.agent_reply
    log = w.discussion_file.read_text(encoding="utf-8")
    assert "**作者**：金手指太弱了" in log
    assert "吞噬型体质" in log
    # 不接状态机：讨论前后 state.json 不应被创建
    assert not (proj / ".state" / "state.json").exists()


def test_apply_rewrites_world_md_keeping_frontmatter(tmp_path):
    proj = _make_project(tmp_path)
    w = WorldDiscussWorkflow(proj, llm_client=_fake_llm("先讨论一版"))
    w.run(message="改金手指")
    w2 = WorldDiscussWorkflow(
        proj,
        llm_client=_fake_llm("# 世界观（修订版）\n\n## 力量体系\n吞噬型天赋体质。"),
    )
    r = w2.run(apply=True)
    post = frontmatter.load(r.world_file)
    assert post.metadata.get("title") == "测试书"
    assert "吞噬型天赋体质" in post.content
    # 修订日志写入 frontmatter revision_log
    assert any("世界观讨论" in entry for entry in post.metadata.get("revision_log", []))


def test_apply_without_log_rejected(tmp_path):
    proj = _make_project(tmp_path)
    w = WorldDiscussWorkflow(proj, llm_client=_fake_llm("x"))
    with pytest.raises(RuntimeError, match="尚无讨论记录"):
        w.run(apply=True)


def test_find_stale_realm_refs_detects_leftover_old_realm_names():
    """讨论改写境界体系后，旧境界名残留在故事简介等其它分节应被检出"""
    old = (
        "## 境界体系（冻结）\n\n"
        "1. **炼气**\n2. **筑基**\n3. **金丹**\n\n"
        "## 故事简介\n\n他从筑基起步，越阶斩杀金丹老怪。"
    )
    new = (
        "## 修炼境界体系\n\n"
        "1. **引灵**\n2. **栖气**\n\n"
        "## 故事简介\n\n他从筑基起步，越阶斩杀金丹老怪。"
    )
    stale = WorldDiscussWorkflow._find_stale_realm_refs(old, new)
    assert stale == ["筑基", "金丹"]


def test_find_stale_realm_refs_clean_when_synced():
    """全文已同步为新境界名时，不应误报（避免误伤林凡/五行等非境界词条）"""
    old = (
        "## 境界体系（冻结）\n\n"
        "1. **炼气**\n2. **筑基**\n3. **金丹**\n\n"
        "## 故事简介\n\n林凡天生五行灵根。"
    )
    new = (
        "## 修炼境界体系\n\n"
        "1. **引灵**\n2. **栖气**\n\n"
        "## 故事简介\n\n林凡天生五行灵根，自引灵起步。"
    )
    assert WorldDiscussWorkflow._find_stale_realm_refs(old, new) == []
