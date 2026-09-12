---
name: sci-fi
label: 科幻

version: 0.1.0
type: genre
description: 硬核科幻（硬核科幻题材包 - 硬核设定/星际文明/赛博朋克/高智叙事体系）
commands: []
hooks:
  - load_genre_template
  - agent.core.quality.scoring.quality_checker.register_genre_rules
dependencies: []
independent: false
fatigue_words:
  - 曲率引擎轰鸣着点亮
  - 量子纠缠超光速传递信息
  - 这是文明的降维打击
  - 星际战舰的阴影笼罩星球
  - 数据在视网膜投影中流淌
pacing_rules:
  - 三章内必须有明确反馈：技术突破/危机化解/文明进程/谜题解开（科幻为探索-突破-应用循环）
  - 硬设定需自洽，科技突破需有理论依据铺垫，禁止魔法化科技
---

# sci-fi Genre Skill · 硬核科幻题材包

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
