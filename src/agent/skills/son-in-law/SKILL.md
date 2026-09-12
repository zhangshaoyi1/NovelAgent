---
name: son-in-law
label: 赘婿逆袭

version: 0.1.0
type: genre
description: 赘婿逆袭/战神（赘婿逆袭题材包 - 隐忍赘婿/实力显露/豪门打脸/战神归来）
commands: []
hooks:
  - load_genre_template
  - agent.core.quality.scoring.quality_checker.register_genre_rules
dependencies: []
independent: false
fatigue_words:
  - 一个上门女婿也配
  - 我忍了三年
  - 今天起我不再隐忍
  - 岳母的脸色变了
  - 谁也不知道他的真实身份
pacing_rules:
  - 三章内必须有明确反馈：实力显露/身份铺垫/打脸/家庭地位变化（赘婿为隐忍-蓄力-翻盘循环）
  - 隐忍期不可连续超过 2 章，翻盘底牌需提前铺垫禁止临时开挂
---

# son-in-law Genre Skill · 赘婿逆袭/战神题材包

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
