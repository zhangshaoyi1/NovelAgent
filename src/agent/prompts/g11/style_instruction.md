---
name: g11.style_instruction
version: 1
stage: G11
purpose: 风格指令
description: 风格指令（由 prompts.py 迁移，单一真源）
validation:
  not_empty: true
  on_fail: retry
---

# user


# 风格指引（用户指定，请在本章写作中自然体现，不要生硬堆砌）
{{ style_guide }}
