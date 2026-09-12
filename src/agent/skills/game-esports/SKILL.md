---
name: game-esports
label: 游戏电竞

version: 0.1.0
type: genre
description: 游戏电竞题材包 - 网游/电竞/全息游戏（副本开荒/赛事对抗/战队经营/操作流与意识流体系）
display_name: 游戏/电竞
commands: []
hooks:
  - load_genre_template
  - agent.core.quality.scoring.quality_checker.register_genre_rules
dependencies: []
independent: false
fatigue_words:
  - 手速拉满
  - 一套行云流水的连招
  - 弹幕瞬间爆炸
  - 全场观众都站了起来
  - 这波操作直接封神
pacing_rules:
  - 三章内必须有明确反馈：等级/装备/排名/战胜强敌/赛事晋级（游戏文为副本-变强-扬名循环，日常水戏不可连续超过 2 章）
  - 关键对局必须写清胜负手（操作/意识/战术/装备），禁止靠对手失误白给赢
---

# Game-esports Genre Skill · 游戏电竞题材包

## 提供能力

### 1. world.md 模板片段
见 `world-template.md`：游戏世界设定、赛事实力阶梯、战队体系等。

### 2. 爽点套路库
见 `tropes.md`：一鸣惊人、捡漏爆装、天梯登顶、赛事逆转、豪门邀约等套路模板。

### 3. 术语表
见 `terms.md`：操作/意识术语、副本与装备、赛事体系、直播生态等。

### 4. 对抗模板
见 `combat-template.md`：开局 → 相持 → 胜负手 → 收尾，含团战与个人赛双模板。

### 5. 题材层质量规则
见 `quality-rules.md`：
- 对局胜负手校验
- 成长与排名推进
- 版本/规则一致性
- 现实与游戏双线平衡

## 加载方式

题材包由主 Agent 在 M1 配置阶段自动加载（用户选择游戏/电竞题材时）。
