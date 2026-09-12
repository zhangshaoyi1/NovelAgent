---
name: g.setting_canon_constraint
version: 1
stage: G
purpose: 设定台账硬约束
description: 已确立设定 + 已知冲突（写作过程中自动沉淀，正文绝对不可与之矛盾）
validation:
  not_empty: true
  on_fail: retry
---

# user


# 设定硬约束（来自 .state/continuity/setting_canon.json，本章正文绝对不可违背）
{{ setting_constraints }}
