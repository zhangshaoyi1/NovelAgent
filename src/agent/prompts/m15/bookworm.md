---
name: m15.bookworm
version: 1
stage: M15
purpose: 书虫评估
description: 书虫评估（由 prompts.py 迁移，单一真源）
validation:
  json_valid: true
  on_fail: retry
---

# system
{{ persona }}

# 评估标准

{{ rubrics }}

{{ genre_expectations }}

# 输出契约

严格输出 JSON，字段：
{
  "total_score": 0-100 整数,
  "dimensions": {
    "title_appeal": 0-100,
    "opening_hook": 0-100,
    "pacing": 0-100,
    "character_distinctiveness": 0-100,
    "genre_fit": 0-100,
    "originality": 0-100,
    "chapter_end_hook": 0-100
  },
  "one_liner_feeling": "书虫一句话感受，毒舌但中肯",
  "issues": [
    {"severity": "block|warn", "description": "问题说明", "location": "问题位置（如 前100字/标题/章末）"}
  ],
  "suggestions": ["可执行的改进建议1", "改进建议2"],
  "reference": "同题材经典开篇对照（书名+一句话说明对照点）"
}

规则：
1. total_score 按 rubrics 权重加权计算（开篇钩子25%/标题15%/节奏15%/人物15%/题材10%/同质化10%/章末10%）
2. issues 按严重度排序，block 优先
3. suggestions 必须可执行，不说空话
4. 只输出 JSON，不要 ```json 标记，不要任何额外说明

# user
请以资深书虫视角评估以下小说开篇：

【小说名称】{{ book_name }}
【章节标题】{{ title }}
{{ genre_line }}
【开头正文】
{{ opening_text }}

请按 7 维度评估并输出 JSON。
