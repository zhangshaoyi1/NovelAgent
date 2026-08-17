---
name: female-suspense
version: 0.1.0
type: genre
description: 女性悬疑/无CP（女性悬疑题材包 - 女主探案/心理惊悚/反转复仇/清醒反杀（无CP））
display_name: 女性悬疑/无CP
commands: []
hooks:
  - agent.workflows.m1_config.load_genre_template
  - agent.core.quality_checker.register_genre_rules
dependencies: []
independent: false
---

# female-suspense Genre Skill · 女性悬疑/无CP题材包

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
