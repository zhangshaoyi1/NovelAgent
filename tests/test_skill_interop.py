"""P2-9 Skill 标准互通（core/registry/skill_registry.py 宽容解析）。

覆盖：
- 标准 AgentSkills/ClawHub 风格 SKILL.md（含 whenToUse/allowed-tools/metadata/license/
  openclaw 等专有字段）可正常发现加载，专有字段忽略不报错；
- description 缺失时回退 whenToUse / when_to_use；
- 缺 name 仍然拒绝。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent.core.registry.skill_registry import SkillRegistry


STANDARD_SKILL_MD = """---
name: story-long-write
description:
whenToUse: 长篇网文写作流程（扫榜→拆文→商业化写作）
version: 0.6.7
license: MIT
allowed-tools:
  - Read
  - Write
metadata:
  openclaw:
    source: https://example.com/skill
    primaryEnv: STORY_API_KEY
---

# 长篇写作操作手册

按 Phase/场景加载 references/。
"""


def _make_registry(tmp_path: Path, skill_md: str) -> SkillRegistry:
    d = tmp_path / "skills" / "story-long-write"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(skill_md, encoding="utf-8")
    return SkillRegistry(skills_dir=tmp_path / "skills")


def test_standard_agentskill_frontmatter_loads(tmp_path: Path) -> None:
    reg = _make_registry(tmp_path, STANDARD_SKILL_MD)
    infos = reg.list_skills()
    assert len(infos) == 1
    info = infos[0]
    assert info.name == "story-long-write"
    assert info.version == "0.6.7"
    # description 缺失时回退 whenToUse
    assert "长篇网文写作流程" in info.description
    # 专有字段（allowed-tools/metadata/license/openclaw）忽略不报错
    assert info.type == ""


def test_when_to_use_snake_case_fallback(tmp_path: Path) -> None:
    md = STANDARD_SKILL_MD.replace("whenToUse:", "when_to_use:")
    reg = _make_registry(tmp_path, md)
    assert "长篇网文写作流程" in reg.list_skills()[0].description


def test_missing_name_still_rejected(tmp_path: Path) -> None:
    md = "---\ndescription: 无名 skill\n---\n正文\n"
    reg = _make_registry(tmp_path, md)
    assert reg.list_skills() == []  # 发现阶段静默跳过（降级不阻断）
