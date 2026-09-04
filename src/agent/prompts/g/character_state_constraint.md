---
name: g.character_state_constraint
version: 1
stage: G
purpose: 角色状态约束
description: 角色状态约束（由 prompts.py 迁移，单一真源）
validation:
  not_empty: true
  on_fail: retry
---

# user


# 角色状态硬约束（来自角色档案 characters/*.md，本章正文绝对不可违背）
{{ character_constraints }}
