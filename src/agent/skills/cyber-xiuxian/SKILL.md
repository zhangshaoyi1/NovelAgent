---
name: cyber-xiuxian
label: 赛博修仙

version: 0.1.0
type: genre
description: 赛博修仙（现代修仙）（赛博修仙题材包 - 现代/赛博设定的修真体系（宗门KPI/渡劫保险/灵石贷款））
commands: []
hooks:
  - load_genre_template
  - agent.core.quality.scoring.quality_checker.register_genre_rules
dependencies: []
independent: false
fatigue_words:
  - 灵石贷款到账提示音响起
  - 本季度宗门KPI又没达标
  - 赛博义体与经脉完美融合
  - 数据流中浮现出古老法诀
  - 渡劫保险理赔成功
pacing_rules:
  - 三章内必须有明确反馈：修为/KPI/灵石资产/义体升级（赛博修仙为打工-修炼-还贷循环）
  - 修为突破需绑定灵石经济或科技资源代价，禁止无源突破
---

# cyber-xiuxian Genre Skill · 赛博修仙（现代修仙）题材包

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
