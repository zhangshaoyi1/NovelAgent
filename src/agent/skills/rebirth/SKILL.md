---
name: rebirth
label: 重生

version: 0.1.0
type: genre
description: 重生复仇（重生复仇题材包 - 重生/先知/弥补遗憾/逆袭体系）
commands: []
hooks:
  - load_genre_template
  - agent.core.quality.scoring.quality_checker.register_genre_rules
dependencies: []
independent: false
fatigue_words:
  - 上一世的种种涌上心头
  - 这一世她定要讨回来
  - 前世的记忆清晰如昨
  - 仇人的脸在脑海中一闪而过
  - 重活一世的意义
pacing_rules:
  - 三章内必须有明确反馈：复仇进度/先知变现/关系改写/危机化解（重生为先知-布局-兑现循环）
  - 先知优势需随蝴蝶效应递减，禁止全程全知碾压
---

# rebirth Genre Skill · 重生复仇题材包

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
