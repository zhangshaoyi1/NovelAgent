---
name: short-story
version: 1.0.0
type: skill
label: 短篇扫榜与拆文
description: |
  短篇网文外部市场分析知识包。提供扫榜（短篇市场情绪/题材/风口分析）与拆文
  （爆款短篇故事核/结构/情感线/反转/写作手法/共鸣拆解）两类能力及配套领域知识。
  CLI 命令：/short-scan（扫榜）、/short-analyze（拆文）。
---

# short-story：短篇扫榜与拆文

短篇网文外部市场/作品分析知识包（移植自 oh-story-claudecod 的 story-short-scan 与 story-short-analyze）。

- **扫榜（short-scan）**：基于榜单样本或内置市场知识，输出情绪方向、题材候选、风险阈值与验证动作。
- **拆文（short-analyze）**：深度拆解爆款短篇的故事核、结构、情感线、反转设计、写作手法、共鸣层次。

## 参考文件

| 文件 | 何时加载 |
|------|----------|
| `real-market-data.md` | 扫榜时：跨平台写作差异对照表、各平台简介公式、题材爆款公式速查 |
| `output-templates.md` | 拆文时：输出模板、结构速查库、质量门控必填字段 |
| `quality-checklist.md` | 拆文评估时：短篇拆书质量自检清单（虐爽节奏/对话密度/毒点速查） |
| `zhihu-style.md` | 拆知乎盐言故事时：盐言风格特征与检查项 |
| `deconstruction-examples.md` | 校准拆文方法时：3 个完整案例 |

## 边界声明

本知识包为**外部短篇市场/作品分析**，不写入学习库（learnings.json，见 m17_learn）。
扫榜与拆文的产物默认仅输出报告（可选保存到项目 `.state/analyze/`），不修改书稿。

## 语言

- 跟随用户的语言回复，中文回复遵循《中文文案排版指北》。
