---
name: g8.ending_instruction
version: 1
stage: G8
purpose: 结尾指令
description: 结尾指令（由 prompts.py 迁移，单一真源）
validation:
  not_empty: true
  on_fail: retry
---

# user


# 结局阶段指令
当前已进入结局阶段，请在本章内：① 推进并回收主线伏笔（{{ subline_id }}）；② 收束进行中支线（已走完：{{ mainline }}）；③ 向架构结局『{{ ending }}』收敛。
