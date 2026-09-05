"""P0-4 最简记忆包判据（memory/memory_pack.py + MemoryLayer/ConsolidatedMemory 接线）。

覆盖：
- classify_memory_type：三类命中 / 未知类型丢弃 / 中文关键词容错；
- minimal_memory_pack：只保留三类权威信息、保持原序；
- prune_recent_changes：不超限原样、超限折叠为摘要行 + 最近 N 条；
- ConsolidatedMemory.update：角色变更列表自动折叠到上限；
- MemoryLayer.recall_minimal：过滤非权威类型后截断；
- prompts/m5/generate.md：包含"最简记忆包"判据语句（防提示词漂移）。
"""

from __future__ import annotations

from pathlib import Path

from agent.memory.base import MemoryEntry
from agent.memory.consolidated import ConsolidatedMemory
from agent.memory.memory_pack import (
    MAX_RECENT_CHANGES,
    CAUSAL_HISTORY,
    CURRENT_STATE,
    WORLD_CONSTRAINT,
    classify_memory_type,
    minimal_memory_pack,
    prune_recent_changes,
)
from agent.memory.semantic import SemanticMemory


# ---------------------------------------------------------------- classify
def test_classify_maps_three_keep_categories() -> None:
    assert classify_memory_type("character") == CURRENT_STATE
    assert classify_memory_type("fact") == CAUSAL_HISTORY
    assert classify_memory_type("chapter_fact") == CAUSAL_HISTORY
    assert classify_memory_type("golden_finger") == WORLD_CONSTRAINT
    assert classify_memory_type("setting") == WORLD_CONSTRAINT


def test_classify_drops_unknown_types() -> None:
    assert classify_memory_type("brainstorm") == ""
    assert classify_memory_type("feedback") == ""
    assert classify_memory_type("") == ""


def test_classify_chinese_keyword_fallback() -> None:
    assert classify_memory_type("角色状态") == CURRENT_STATE
    assert classify_memory_type("世界设定") == WORLD_CONSTRAINT
    assert classify_memory_type("剧情因果") == CAUSAL_HISTORY


# ---------------------------------------------------------------- pack
def test_minimal_memory_pack_keeps_only_authoritative() -> None:
    items = [
        MemoryEntry(id="1", type="fact", text="主角断臂"),
        MemoryEntry(id="2", type="brainstorm", text="脑暴：也许可以加个师妹"),
        MemoryEntry(id="3", type="character", text="周伯已故"),
        MemoryEntry(id="4", type="setting", text="金手指每月只能用一次"),
    ]
    out = minimal_memory_pack(items)
    assert [it.id for it in out] == ["1", "3", "4"]  # 原序、丢脑暴


# ---------------------------------------------------------------- prune
def test_prune_recent_changes_within_limit_untouched() -> None:
    changes = [f"变更{i}" for i in range(5)]
    out = prune_recent_changes(changes, MAX_RECENT_CHANGES)
    assert out is changes  # 不超限：原样返回


def test_prune_recent_changes_folds_overflow() -> None:
    changes = [f"第{i}章变更" for i in range(MAX_RECENT_CHANGES + 5)]
    out = prune_recent_changes(changes)
    assert len(out) == MAX_RECENT_CHANGES + 1  # 摘要行 + 最近 N 条
    summary = out[0]
    assert "已合并" in summary and "第0章变更" in summary
    assert list(out[1:]) == changes[-MAX_RECENT_CHANGES:]  # 最近 N 条完整保留且保序


# ------------------------------------------------- ConsolidatedMemory 接线
def test_consolidated_prunes_character_changes_on_update(tmp_path: Path) -> None:
    cm = ConsolidatedMemory(tmp_path)
    long_changes = [f"第{i}章：状态推进" for i in range(MAX_RECENT_CHANGES + 8)]
    cm.update(characters=[{"name": "主角", "changes": long_changes}])
    stored = cm.get("characters")[0]["changes"]
    assert len(stored) == MAX_RECENT_CHANGES + 1
    assert "已合并" in stored[0]


# ------------------------------------------------- MemoryLayer.recall_minimal
def test_recall_minimal_filters_and_truncates(tmp_path: Path) -> None:
    from agent.memory.layer import MemoryLayer

    layer = MemoryLayer(tmp_path)
    layer.remember("主角断臂", type="fact")
    layer.remember("脑暴杂记", type="brainstorm")
    layer.remember("周伯已故", type="character")
    hits = layer.recall_minimal("主角 断臂 周伯", top_k=2)
    texts = [e.text for e, _s in hits]
    assert texts == ["主角断臂", "周伯已故"]  # 脑暴被过滤、截断生效


def test_recall_minimal_semantic_entry_roundtrip(tmp_path: Path) -> None:
    """SemanticMemory 端到端：写入 → 最简包召回 → 结果仍是 MemoryEntry。"""
    sm = SemanticMemory(tmp_path)
    sm.add("金手指每月只能用一次", type="setting")
    sm.add("随手记", type="note")
    from agent.memory.memory_pack import minimal_memory_pack

    hits = sm.retrieve("金手指 限制", top_k=5)
    packed = minimal_memory_pack((e for e, _s in hits))
    assert len(packed) == 1
    assert packed[0].type == "setting"


# ---------------------------------------------------------------- 提示词防漂移
def test_generate_prompt_contains_memory_pack_criterion() -> None:
    import agent
    from agent.core.infra.prompt_manager import pm

    prompt_path = Path(agent.__file__).parent / "prompts" / "m5" / "generate.md"
    content = prompt_path.read_text(encoding="utf-8")
    assert "最简记忆包" in content
    assert "不得自行虚构补全" in content
    # PromptManager 侧也能正常加载（渲染不报错）
    prompt = pm.get("m5.generate")
    assert "最简记忆包" in prompt.render_system(genre="都市")
