---
name: xiuxian
label: 修仙

version: 0.1.0
type: genre
description: 修仙题材包 - 境界体系/爽点套路/术语表/战斗模板/题材层质量规则
commands: []
hooks:
  - agent.workflows.planning.m1_config.load_genre_template
  - agent.core.quality.scoring.quality_checker.register_genre_rules
dependencies: []
independent: false
# P2-2.3 试点（对标 inkos 题材包规则引擎）：可确定性执行的题材级规则
fatigue_words:
  - 灵气如潮水般涌入丹田
  - 境界碾压
  - 长老们纷纷侧目
  - 天骄之名传遍整个修真界
  - 一声龙吟般的长啸
pacing_rules:
  - 三章内必有明确反馈：打脸/收益兑现/信息反转/地位变化（修仙为夺宝-闭关-出关循环，闭关不可连续超过 2 章）
  - 境界突破必须绑定具体代价或机缘，禁止无源突破
---

# Xiuxian Genre Skill · 修仙题材包

## 提供能力

### 1. world.md 模板片段
见 `world-template.md`：境界体系、力量体系、势力框架等。

### 2. 爽点套路库
见 `tropes.md`：打脸、装逼、逆袭、夺宝、悟道等套路模板。

### 3. 术语表
见 `terms.md`：功法、灵根、丹药、法器、阵法、宗门、秘境、天劫等。

### 4. 战斗模板
见 `combat-template.md`：试探 → 胶着 → 转折 → 决胜，含智斗元素。

### 5. 金手指模板
系统、血脉、传承、神器、重生等类型 + 成长/代价/上限登记模板。

### 6. 题材层质量规则
见 `quality-rules.md`：
- 每 N 章境界或战斗进度推进
- 战斗章节走专属模板
- 境界对应战力表校验
- 金手指使用符合登记

## 加载方式

题材包由主 Agent 在 M1 配置阶段自动加载（用户选择修仙题材时）。
无需手动 /load-skill。
