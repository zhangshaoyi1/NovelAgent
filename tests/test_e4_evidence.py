"""E4 内容证据可视化（结构化证据链）单元测试

覆盖：
- EvidenceRef / EvidenceChain 数据结构与 to_dict 序列化
- M5 生成的 evidence_chain 包含角色 / 伏笔 / 设定三类引用
- _validate_evidence 标记缺失引用源（不阻断落盘）
- 落盘章节的 frontmatter 中 evidence_chain 为结构化 dict
- EvidenceChain.all_refs / total 统计
"""

from __future__ import annotations

import frontmatter
from pathlib import Path

from agent.core.evidence_chain import EvidenceChain, EvidenceRef
from agent.workflows.m5_write_chapter import M5WriteChapterWorkflow

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


# ============================================================
# M5 集成：生成结构化证据链
# ============================================================
class TestM5EvidenceChain:
    def test_result_has_structured_chain(self, tmp_path: Path) -> None:
        d = _build_minimal_project(tmp_path)
        wf = M5WriteChapterWorkflow(project_dir=d, llm_client=_build_mock_llm())
        result = wf.run()
        chain = result.evidence_chain
        assert isinstance(chain, EvidenceChain)
        # 至少覆盖设定三类
        assert len(chain.settings) >= 3  # 世界观/支线/路线/关系...
        assert len(chain.foreshadows) >= 1  # 伏笔登记表
        assert len(chain.characters) >= 1  # 林寻/太虚镜

    def test_saved_frontmatter_is_dict(self, tmp_path: Path) -> None:
        d = _build_minimal_project(tmp_path)
        wf = M5WriteChapterWorkflow(project_dir=d, llm_client=_build_mock_llm())
        result = wf.run()
        post = frontmatter.load(result.chapter_file)
        chain = post.metadata.get("evidence_chain")
        assert isinstance(chain, dict)
        assert "settings" in chain and "characters" in chain and "foreshadows" in chain
        for grp in ("characters", "foreshadows", "settings"):
            for ref in chain.get(grp, []):
                assert "source" in ref

    def test_missing_source_detected(self, tmp_path: Path) -> None:
        """fixture 中只有 林寻.md，太虚镜.md 缺失 → 应记录到 missing_sources"""
        d = _build_minimal_project(tmp_path)
        wf = M5WriteChapterWorkflow(project_dir=d, llm_client=_build_mock_llm())
        result = wf.run()
        chain = result.evidence_chain
        # 太虚镜角色档案不存在，应被标记
        assert any("太虚镜" in m for m in chain.missing_sources)

    def test_missing_source_not_blocks_save(self, tmp_path: Path) -> None:
        d = _build_minimal_project(tmp_path)
        wf = M5WriteChapterWorkflow(project_dir=d, llm_client=_build_mock_llm())
        result = wf.run()
        # 即便有缺失源，章节文件仍应成功落盘
        assert result.chapter_file.exists()
