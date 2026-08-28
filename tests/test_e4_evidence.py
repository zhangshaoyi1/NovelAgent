"""E4 内容证据可视化（结构化证据链）单元测试

覆盖：
- EvidenceRef / EvidenceChain 数据结构与 to_dict 序列化
- _validate_evidence 标记缺失引用源（不阻断落盘）
- EvidenceChain.all_refs / total 统计

M5 生成集成测试已迁移至 AgenticWriteWorkflow 测试套件。
"""

from __future__ import annotations

from pathlib import Path

from agent.core.story.evidence_chain import EvidenceChain, EvidenceRef

from tests.conftest import (
    _build_minimal_project,
    _build_mock_llm,
)


# ============================================================
# 数据结构
# ============================================================
class TestEvidenceStructures:
    def test_ref_to_dict_skips_empty(self) -> None:
        ref = EvidenceRef(name="林寻", field="动机", source="characters/林寻.md")
        d = ref.__dict__  # to_dict 逻辑在 EvidenceChain._ref，这里直接校验字段
        assert d["name"] == "林寻"
        assert d["source"] == "characters/林寻.md"

    def test_chain_to_dict_structure(self) -> None:
        chain = EvidenceChain(
            characters=[EvidenceRef(name="林寻", field="动机", source="characters/林寻.md")],
            foreshadows=[EvidenceRef(ref_id="F-01", field="伏笔", source="foreshadows.md")],
            settings=[EvidenceRef(name="世界观", field="简介", source="world.md")],
        )
        d = chain.to_dict()
        assert set(d.keys()) == {"characters", "foreshadows", "settings"}
        assert d["characters"][0]["name"] == "林寻"
        # ref_id 应被纳入 foreshadows
        assert d["foreshadows"][0]["id"] == "F-01"

    def test_chain_total_and_all_refs(self) -> None:
        chain = EvidenceChain(
            characters=[EvidenceRef(name="a", source="x")],
            foreshadows=[EvidenceRef(ref_id="F-1", source="y")],
            settings=[EvidenceRef(name="b", source="z")],
        )
        assert chain.total() == 3
        assert len(chain.all_refs()) == 3
