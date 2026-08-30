---
name: g12.emotion_instruction
version: 1
stage: G12
purpose: 情绪指令
description: 情绪指令（由 prompts.py 迁移，单一真源）
validation:
  not_empty: true
  on_fail: retry
---

# user


# 情绪目标（本章节奏与情绪落点）
{{ emotion_target }}
