---
name: wuxia
version: 0.1.0
type: genre
description: 武侠题材包 - 武功境界/江湖势力/爽点套路/术语表/战斗模板/题材层质量规则
commands: []
hooks:
  - agent.workflows.m1_config.load_genre_template
  - agent.core.quality_checker.register_genre_rules
dependencies: []
independent: false
---

# Wuxia Genre Skill · 武侠题材包

## 提供能力

### 1. world.md 模板片段
见 `world-template.md`：武功境界体系、内力/招式/兵器体系、江湖势力框架等。

### 2. 爽点套路库
见 `tropes.md`：比武打脸、奇遇传功、恩怨清算、仗剑行侠、红颜知己等套路模板。

### 3. 术语表
见 `terms.md`：内力、招式、轻功、暗器、毒术、门派、江湖切口等。

### 4. 战斗模板
见 `combat-template.md`：起手 → 交锋 → 变招 → 决胜，重招式技巧与内力博弈。

### 5. 金手指模板
奇遇、绝世武功、神兵利器、毒免疫、重生等类型 + 成长/代价/上限登记模板。

### 6. 题材层质量规则
见 `quality-rules.md`：
- 每 N 章武功或江湖地位推进
- 战斗章节走专属模板
- 境界对应战力表校验
- 金手指使用符合登记

## 加载方式

题材包由主 Agent 在 M1 配置阶段自动加载（用户选择武侠题材时）。
无需手动 /load-skill。
