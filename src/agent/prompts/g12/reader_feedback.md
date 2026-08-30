---
name: g12.reader_feedback
version: 1
stage: G12
purpose: 读者反馈
description: 读者反馈（由 prompts.py 迁移，单一真源）
validation:
  not_empty: true
  on_fail: retry
---

# user


# 读者反馈（以下为真实读者反馈，涉及弃书点的章节请强化章末钩子与爽点密度）
{{ reader_signals }}
