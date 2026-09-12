---
name: metaphysics
label: 玄学鉴宝

version: 0.1.0
type: genre
description: 玄学鉴宝/神医风水（玄学鉴宝题材包 - 玄学/鉴宝/神医/风水/相术体系）
commands: []
hooks:
  - load_genre_template
  - agent.core.quality.scoring.quality_checker.register_genre_rules
dependencies: []
independent: false
fatigue_words:
  - 眼中精光一闪
  - 这物件有大隐隐于市的气息
  - 祖上传下来的规矩
  - 风水轮流转
  - 气机流转不息
pacing_rules:
  - 三章内必须有明确反馈：捡漏/打眼/治病/风水局/鉴宝结论（玄学鉴宝为看货-断真假-变现循环）
  - 玄学手段需有师承或金手指登记，禁止凭空开天眼
---

# metaphysics Genre Skill · 玄学鉴宝/神医风水题材包

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
