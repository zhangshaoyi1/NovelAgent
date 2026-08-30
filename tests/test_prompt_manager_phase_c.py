"""阶段 C（提示词管理收尾）单测。

覆盖三块：
- C2 题材参数化：world.<genre>.md 覆盖选择、system 段 ``{{ genre or "网文" }}``
  注入与空值降级、``first_genre_label`` id->中文标签解析
- C3 热重载：后台 TTL watcher 对 md 变化的感知 + stop 幂等
- C1 删除断言：``agent.prompts`` 已删除，旧常量不得回流（防止有人把
  prompts.py 拷回来绕过 md 单一真源）
"""

from __future__ import annotations

import os
import time

import pytest

from agent.core.infra.prompt_manager import PromptManager, pm
from agent.core.registry.genre_pack import first_genre_label


# ============================================================
# C2 题材参数化
# ============================================================
def test_world_override_xiuxian_selected() -> None:
    """genre=xiuxian 时应选中 world.xiuxian.md（修仙人设显式化）。"""
    p = pm.get("m1.world", genre="xiuxian")
    assert "修仙小说世界观设计师" in p.system


def test_world_override_funeral_selected() -> None:
    """既有殡葬覆盖不受影响（回归保护）。"""
    p = pm.get("m1.world", genre="funeral")
    assert "殡葬" in p.system


def test_world_base_genre_inject_and_fallback() -> None:
    """基础版 world.md 已参数化：注入标签 / 空 genre 降级为 网文。"""
    p = pm.get("m1.world", genre=None)
    assert "{{ genre" in p.system
    assert "规则怪谈" in p.render_system(genre="规则怪谈")
    assert "网文" in p.render_system(genre="")


@pytest.mark.parametrize(
    "name,tail",
    [
        ("m2.discuss", "小说创作顾问"),
        ("m3.outline", "小说大纲设计师"),
        ("m4.character", "小说人物设计师"),
        ("m14.architecture", "小说架构师"),
        ("m14.iterate", "小说架构师"),
        ("m5.generate", "小说写手"),
    ],
)
def test_system_prompts_genre_parameterized(name: str, tail: str) -> None:
    """6 个 system 提示词：修仙标签注入 + 空 genre 降级，两态都不含裸「修仙硬编码」。"""
    s = pm.get(name).render_system(genre="修仙")
    assert "修仙" in s.split("，")[0] and tail in s
    e = pm.get(name).render_system(genre="")
    assert "网文" in e.split("，")[0]


def test_first_genre_label_mapping() -> None:
    """id -> 中文显示名；未注册题材回退 id；空 genres 返回空串。"""
    assert first_genre_label({"genres": ["xiuxian"]}) == "修仙"
    assert first_genre_label({"genres": ["infinite-flow"]}) == "无限流"
    assert first_genre_label({"genres": ["no-such-genre"]}) == "no-such-genre"
    assert first_genre_label({}) == ""


# ============================================================
# C3 热重载（临时根实测，不污染真实 prompts/）
# ============================================================
def test_watcher_hot_reload(tmp_path) -> None:
    d = tmp_path / "m1"
    d.mkdir()
    f = d / "demo.md"
    f.write_text(
        "---\nname: m1.demo\nversion: 1\n---\n\n# system\nV1\n",
        encoding="utf-8",
    )
    pmb = PromptManager(root=tmp_path, watch_ttl=0.2)
    assert pmb.get("m1.demo").system == "V1"
    pmb.start_watcher()
    try:
        time.sleep(0.1)
        f.write_text(
            "---\nname: m1.demo\nversion: 2\n---\n\n# system\nV2\n",
            encoding="utf-8",
        )
        # 规避 Windows 文件系统 mtime 粒度：强制把 mtime 前移
        os.utime(f, (time.time() + 2, time.time() + 2))
        deadline = time.time() + 5
        while time.time() < deadline and pmb.get("m1.demo").system != "V2":
            time.sleep(0.1)
        assert pmb.get("m1.demo").system == "V2"
    finally:
        pmb.stop_watcher()
    pmb.stop_watcher()  # 幂等


def test_list_prompts_enumerates_md() -> None:
    rows = pm.list_prompts()
    names = {r["name"] for r in rows}
    assert {"m1.world", "m5.generate", "m14.iterate"} <= names
    assert all(r["source"] == "md" for r in rows)
    assert all(r["mtime"] > 0 for r in rows)


# ============================================================
# C1 删除断言：agent.prompts 旧常量不得回流
# ============================================================
@pytest.mark.parametrize(
    "attr",
    [
        "M1_SYSTEM_PROMPT",
        "M12_CONFLICT_SYSTEM_PROMPT",
        "M12_CONFLICT_USER_TEMPLATE",
        "E_LEARN_EXTRACT_SYSTEM_PROMPT",
        "format_rag_context",
    ],
)
def test_agent_prompts_module_cleaned(attr: str) -> None:
    """prompts.py 已删除；agent.prompts 仅是 md 容器命名空间，不得再有旧常量/助手。"""
    import agent.prompts as prompts_ns

    assert not hasattr(prompts_ns, attr)
