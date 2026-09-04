"""扩展机制注册表层

职责：管理所有可扩展模块的注册与发现。
- Skill 注册表：统一管理所有 skill 类型的动态发现与加载
- GenrePack 注册表：题材包的定义、加载与查询
- Genre Merger：多题材合并器

依赖规则：依赖 base（BaseRegistry），不依赖其他 core 子包。
"""

from agent.core.registry.skill_registry import (
    SkillRegistry,
    SkillInfo,
    SkillProvider,
    get_skill_registry,
)
from agent.core.registry.genre_pack import (
    GenrePackRegistry,
    GenrePack,
    GenreManifest,
    Trope,
)
from agent.core.registry.genre_merger import GenreMerger, MergeConflict

__all__ = [
    "SkillRegistry",
    "SkillInfo",
    "SkillProvider",
    "get_skill_registry",
    "GenrePackRegistry",
    "GenrePack",
    "GenreManifest",
    "Trope",
    "GenreMerger",
    "MergeConflict",
]