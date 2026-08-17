---
name: bookworm
version: 0.1.0
type: evaluator
description: 以资深书虫视角评估小说标题、书名、开头的吸引力
commands:
  - name: bookworm-review
    args:
      - title
      - book_name
      - opening_text
      - genre (optional)
hooks: []
dependencies: []
independent: true
---

# Bookworm Skill · 书虫测评

## 独立性契约

本 skill 设计为**独立可移植**，可被任何 AI 工具加载，不依赖主 Agent 的内部状态。

### 输入契约

```yaml
title: <章节标题或副标题>
book_name: <小说名称>
opening_text: <开头正文，建议前 3000 字或前 3 章>
genre: <题材，可选，如 xiuxian/romance/mystery>
```

### 输出契约

```json
{
  "total_score": 0,
  "dimensions": {
    "title_appeal": 0,
    "opening_hook": 0,
    "pacing": 0,
    "character_distinctiveness": 0,
    "genre_fit": 0,
    "originality": 0,
    "chapter_end_hook": 0
  },
  "one_liner_feeling": "<书虫一句话感受>",
  "issues": [
    {"severity": "block|warn", "description": "", "location": ""}
  ],
  "suggestions": ["<可执行的改进建议>"],
  "reference": "<同题材经典开篇对照>"
}
```

同时输出 Markdown 形式报告。

## 评估维度

| 维度 | 评估点 |
|---|---|
| 标题吸引力 | 记忆点 / 独特性 / 题材信号 / 是否有"想点开"的冲动 |
| 开篇钩子 | 前 100 字是否抓人 / 前 1000 字是否有冲突 / 前 3000 字是否有付费点 |
| 节奏 | 信息密度 / 场景切换 / 是否拖沓 |
| 人物 | 主角是否有辨识度 / 是否有代入感 |
| 题材契合 | 是否满足该题材读者的核心期待 |
| 同质化 | 与市面同类作品的区分度 |
| 章末钩子 | 读完是否想看下一章 |

## 加载方式

```
/load-skill bookworm
/bookworm-review --title "..." --book_name "..." --opening_text "..."
```

## 实现说明

- `persona.md`：书虫人格设定（资深读者、毒舌但专业）
- `rubrics.md`：评估维度与评分标准
- `genre_expectations/`：各题材读者期待
