# registry/ — 扩展机制注册表

## 职责
提供技能（Skill）和题材包（GenrePack）的统一注册与发现机制。

## 包含文件
| 文件 | 职责 |
|------|------|
| `skill_registry.py` | 统一 Skill 注册表（SkillRegistry, SkillInfo, SkillProvider） |
| `genre_pack.py` | 题材包注册表（GenrePackRegistry, GenrePack, GenreManifest, Trope） |
| `genre_merger.py` | 题材包合并器（GenreMerger - 多题材包模板合并） |

## 依赖规则
- 依赖 base/（BaseRegistry 基类）
- 不依赖业务层

## 被依赖
- workflows/ (M2-M5 工作流通过 first_genre 获取题材)
- cli/ (命令注册)