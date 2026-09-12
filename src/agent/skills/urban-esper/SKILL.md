---
name: urban-esper
label: 都市异能

version: 0.1.0
type: genre
description: 都市脑洞/异能（都市异能题材包 - 异能觉醒/反套路/鉴宝读心/都市爽文体系）
commands: []
hooks:
  - load_genre_template
  - agent.core.quality.scoring.quality_checker.register_genre_rules
dependencies: []
independent: false
fatigue_words:
  - 异能觉醒的刹那
  - 瞳孔中闪过一缕金光
  - 普通人根本无法想象
  - 都市的霓虹灯下暗流涌动
  - 读心术告诉他真相
pacing_rules:
  - 三章内必须有明确反馈：异能升级/鉴宝/打脸/危机化解（都市异能为觉醒-运用-变现循环）
  - 异能使用需有代价与成长上限登记，禁止无限制万能异能
---

# urban-esper Genre Skill · 都市脑洞/异能题材包

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
