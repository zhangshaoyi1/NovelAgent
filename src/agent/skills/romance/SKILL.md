---
name: romance
label: 女频言情

version: 0.1.0
type: genre
description: 女频言情题材包 - 甜宠/古言/现言/穿越（情感线推进/心动名场面/双向奔赴/误会与和解体系）
commands: []
hooks:
  - load_genre_template
  - agent.core.quality.scoring.quality_checker.register_genre_rules
dependencies: []
independent: false
fatigue_words:
  - 心脏漏跳了一拍
  - 他的眸色深了深
  - 空气里弥漫着暧昧的气息
  - 耳根悄悄红了
  - 指尖轻轻颤了颤
pacing_rules:
  - 三章内必须有情感线推进：心动瞬间/关系升级/误会加深/身世线索（言情为相遇-拉扯-定情循环，冷战/分离不可连续超过 3 章）
  - 男主女主的每次关键互动必须服务情感弧线，禁止与主线无关的水戏
---

# Romance Genre Skill · 女频言情题材包

## 提供能力

### 1. world.md 模板片段
见 `world-template.md`：时代背景框架、感情线阶段表、人物关系模板等。

### 2. 情感爽点套路库
见 `tropes.md`：心动名场面、吃醋、双向奔赴、误会与和解、身份揭晓等套路模板。

### 3. 术语表
见 `terms.md`：CP 概念、名场面类型、身份设定、古言礼制用语等。

### 4. 冲突/对峙模板
见 `combat-template.md`（本题材为情感对峙模板）：试探 → 激化 → 摊牌 → 余波。

### 5. 题材层质量规则
见 `quality-rules.md`：
- 每 N 章情感线推进
- 感情节奏与降温惩罚
- 人设一致性校验
- 误会不得超期不解

## 加载方式

题材包由主 Agent 在 M1 配置阶段自动加载（用户选择女频言情题材时）。
可与 `era-farm`（年代背景）、`rebirth`（重生先知）等包通过 /merge-genres 叠加。
