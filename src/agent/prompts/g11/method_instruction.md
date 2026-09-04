---
name: g11.method_instruction
version: 1
stage: G11
purpose: 方法指令
description: 方法指令（由 prompts.py 迁移，单一真源）
validation:
  not_empty: true
  on_fail: retry
---

# user


# 写作方法模板（请按此结构方法论组织全书/大纲，不要生硬套用）
{{ method_text }}
