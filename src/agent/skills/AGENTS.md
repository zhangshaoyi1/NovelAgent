# AGENTS.md - skills/ Skill 插件层

## 职责

每个 skill 自包含，可独立分发。主 Agent 通过 `/load-skill` 命令加载。

## 可用 Skill

| 目录 | 题材类型 |
|------|----------|
| `apocalypse/` | 末世 |
| `bookworm/` | 书虫评测 |
| `cyber-xiuxian/` | 赛博修仙 |
| `era-farm/` | 年代种田 |
| `female-suspense/` | 女频悬疑 |
| `infinite-flow/` | 无限流 |
| `learning-imitation/` | 学习模仿 |
| `metaphysics/` | 玄学 |
| `nanpin-shuangwen/` | 男频爽文 |
| `novel-quality-remediation/` | 小说质量修复 |
| `rebirth/` | 重生 |
| `sci-fi/` | 科幻 |
| `short-story/` | 短篇故事 |
| `son-in-law/` | 赘婿 |
| `urban-esper/` | 都市异能 |
| `wuxia/` | 武侠 |
| `xiuxian/` | 修仙 |

## 每个 Skill 的典型文件

- `SKILL.md`: Skill 元信息
- `tropes.md`: 套路定义
- `terms.md`: 术语表
- `combat-template.md`: 战斗模板
- `quality-rules.md`: 质量规则
- `world-template.md`: 世界观模板

## 依赖规则

- 各 skill 之间独立，互不依赖
- 通过 `SkillRegistry` 统一管理发现与加载