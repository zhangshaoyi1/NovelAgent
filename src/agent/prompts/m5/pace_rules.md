---
name: m5.pace_rules
version: 1
stage: M5
purpose: 写手平权规则（按强度档位分叉）
description: 写作要求第 3-6 条的唯一真源（WriterAgent system 提示装配用）
validation:
  not_empty: true
  on_fail: retry
---

# system
3. {% if pace_relaxed %}前 500 字内给出**可感知的进入点**（人物处境/环境氛围/一个待解的小疑问均可），不必强凑冲突或反差；但禁止用天气、风景空转开场{% else %}前 500 字内出现冲突/悬念/反差之一{% endif %}
4. {% if pace_relaxed %}本章为规划登记的**放松章**：情绪落点只需「承接或蓄力」（余味/舒缓/压抑待发之一即可），不必凑爽/虐/燃/甜/惊这类高强度锚点；但**仍须有一个明确的情绪落点**，不得全章无落点{% else %}本章至少含一个爽/虐/燃/甜/惊锚点{% endif %}
5. {% if pace_relaxed %}章末给出**去向感**即可（下一场景的牵引、一个未解的小疑问、人物明确的下一步动作），不强制悬念/反转/期待{% else %}章末必须有悬念/反转/期待之一{% endif %}
6. {% if pace_relaxed %}场景+动作+环境描写合计 ≥ 15%（对话密集章、内心章据实降低，不得为凑描写而注水）{% else %}场景+动作+环境描写合计 ≥ 30%{% endif %}
