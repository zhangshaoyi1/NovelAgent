---
name: apocalypse
label: 末世

version: 0.1.0
type: genre
description: 末世生存/基建（末世生存题材包 - 末世降临/囤货/基地基建/异能丧尸体系）
commands: []
hooks:
  - load_genre_template
  - agent.core.quality.scoring.quality_checker.register_genre_rules
dependencies: []
independent: false
fatigue_words:
  - 末日的残酷超乎想象
  - 丧尸如潮水般涌来
  - 基地里一片死寂
  - 囤积的物资堆成了小山
  - 异能觉醒的刹那
pacing_rules:
  - 三章内必须有明确反馈：物资入账/异能升级/基地建设/危机化解（末世为求生-囤积-基建循环，安全期不可连续超过 3 章）
  - 物资与人口需有具体数目与消耗闭环，禁止无源物资救场
---

# apocalypse Genre Skill · 末世生存/基建题材包

## 提供能力

### 1. world.md 模板片段
见 `world-template.md`：体系/框架模板。

### 2. 爽点套路库
见 `tropes.md`：套路模板。

### 3. 术语表
见 `terms.md`：核心术语。

### 4. 冲突/对决模板
见 `combat-template.md`：四段结构。

### 5. 金手指模板
类型 + 成长/代价/上限登记模板。

### 6. 题材层质量规则
见 `quality-rules.md`：题材专属校验。

## 加载方式

题材包由主 Agent 在 M1 配置阶段自动加载（用户选择本题材时）。
无需手动 /load-skill。
