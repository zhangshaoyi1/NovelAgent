# AGENTS.md - core/registry/ 扩展机制注册表层

## 职责

管理所有可扩展模块的注册与发现。

## 核心模块

| 文件 | 导出 | 作用 |
|------|------|------|
| `skill_registry.py` | `SkillRegistry`, `SkillInfo`, `SkillProvider`, `get_skill_registry` | Skill 注册表 |
| `genre_pack.py` | `GenrePackRegistry`, `GenrePack`, `GenreManifest`, `Trope` | 题材包注册表 |
| `genre_merger.py` | `GenreMerger`, `MergeConflict` | 多题材合并器 |

## 依赖规则

- 依赖 base（BaseRegistry）
- 不依赖其他 core 子包