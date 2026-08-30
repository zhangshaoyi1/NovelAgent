---
name: g8.ending_fallback_instruction
version: 1
stage: G8
purpose: 结尾兜底指令
description: 结尾兜底指令（由 prompts.py 迁移，单一真源）
validation:
  not_empty: true
  on_fail: retry
---

# user


# 结局阶段指令（收尾）
当前已进入结局阶段，请在本章内：① 推进并回收主线伏笔；② 收束进行中支线；③ 完成收尾，不留新开的故事线。
