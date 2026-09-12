---
name: history-tech
label: 历史科技

version: 0.1.0
type: genre
description: 历史科技文题材包 - 穿越/架空历史/种田科技流/工业攀科技/朝堂权谋（知识差金手指/基建兴国体系）
commands: []
hooks:
  - load_genre_template
  - agent.core.quality.scoring.quality_checker.register_genre_rules
dependencies: []
independent: false
fatigue_words:
  - 众人如听天书
  - 此乃惊世骇俗之论
  - 主公大才
  - 众将拜服
  - 亲手搓出了玻璃
pacing_rules:
  - 三章内必须有明确反馈：科技落地/地位提升/势力扩张/危机化解（历史科技为攀科技-变现-升级生产力循环，纯权谋水戏不可连续超过 3 章）
  - 科技解锁必须匹配时代基础（材料/工艺/能源链条），禁止无中生有凭空造物
---

# History-tech Genre Skill · 历史科技文题材包

## 提供能力

### 1. world.md 模板片段
见 `world-template.md`：时代背景、科技树阶段表、势力格局等。

### 2. 爽点套路库
见 `tropes.md`：技惊四座、点石成金、朝堂论战、练兵强军、基建兴国等套路模板。

### 3. 术语表
见 `terms.md`：科技树、工坊、钱粮兵、朝堂、科举军制等。

### 4. 冲突/对决模板
见 `combat-template.md`（本题材含战争与朝堂两套对抗模板）：布局 → 交锋 → 变数 → 定局。

### 5. 金手指模板
现代知识体系、记忆库、系统面板等类型 + 成长/代价/上限登记模板。

### 6. 题材层质量规则
见 `quality-rules.md`：
- 科技解锁合理性校验
- 科技变现闭环
- 权谋动机与智商底线
- 历史逻辑自洽

## 加载方式

题材包由主 Agent 在 M1 配置阶段自动加载（用户选择历史/历史科技题材时）。
