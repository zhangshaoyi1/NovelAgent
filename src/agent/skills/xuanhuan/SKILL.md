---
name: xuanhuan
label: 玄幻

version: 0.1.0
type: genre
description: 玄幻题材包 - 高武/异世界大陆/斗气魔法体系/血脉觉醒/热血升级（对标起点男频第一大类）
display_name: 东方玄幻/异界大陆
commands: []
hooks:
  - load_genre_template
  - agent.core.quality.scoring.quality_checker.register_genre_rules
dependencies: []
independent: false
fatigue_words:
  - 全场哗然
  - 空气仿佛凝固了
  - 这怎么可能
  - 蝼蚁也敢猖狂
  - 血脉之力喷涌而出
pacing_rules:
  - 三章内必有明确反馈：升级/打脸/夺宝/新地图解锁（玄幻为练级-试炼-扬名循环，闭关苦修不可连续超过 2 章）
  - 等级压制必须可感知：跨大境界挑战需绑定血脉/神器/双修体系等明确金手指，禁止无源越级
---

# Xuanhuan Genre Skill · 玄幻题材包

## 提供能力

### 1. world.md 模板片段
见 `world-template.md`：等阶体系、力量体系、大陆势力框架等。

### 2. 爽点套路库
见 `tropes.md`：打脸、试炼夺魁、血脉觉醒、夺宝、扬名立万等套路模板。

### 3. 术语表
见 `terms.md`：斗气/源力、功法武技、血脉、神兵、宗门学院、秘境试炼等。

### 4. 战斗模板
见 `combat-template.md`：蓄势 → 对轰 → 爆种 → 定局，含招式对撞与气氛渲染。

### 5. 金手指模板
神器、血脉、双生武魂、老爷爷传承、吞噬进化等类型 + 成长/代价/上限登记模板。

### 6. 题材层质量规则
见 `quality-rules.md`：
- 每 N 章等阶或战力推进
- 战斗章节走专属模板
- 等阶战力表校验
- 金手指使用符合登记

## 加载方式

题材包由主 Agent 在 M1 配置阶段自动加载（用户选择玄幻题材时）。
无需手动 /load-skill。
