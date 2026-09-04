---
name: m5.craft_instruction
version: 1
stage: M5
purpose: 写作技法知识注入模板（按需加载，控制 token）
description: 把按压力阶段/前3章选择的写作技法知识包装为写章参考；技法知识本体在 prompts/methods/
validation:
  not_empty: true
  on_fail: warn
---

# system
本章附带了写作技法参考。请在写作中**自然运用**这些技法（选型、节奏、结构），但：
1. 不要生硬堆砌，不套模板腔；
2. 技法名称/术语（如"章尾钩子13式"）不得出现在正文里，用情节自然呈现；
3. 若技法与本项目既有设定/风格冲突，以风格配置与设定优先。

# user
【写作技法参考（按本章特性选载，供参考运用）】
{{ craft_guide }}
