---
name: learning-imitation
version: 1.0.0
type: style
label: 三阶段学习仿写
description: |
  借鉴 DeepWrite 的三阶段学习仿写能力：拆素材（material_split）→ 学剧情（plot_learning）→ 学文风（style_learning），
  输出「六槽位」可执行技法资产（gimmick/character/pacing/intro/plot_refine/draft_excerpt）。
  产出先写预览区（.state/learning/preview/），确认后才进入技法资产库（.state/learning/library.json），未确认不入库。
---

# learning-imitation：三阶段学习仿写

对标 DeepWrite `learning-imitation/*.txt`，把「看爆款 → 学写法」从凭感觉变成确定性的三阶段流程。

## 三阶段入口

| 阶段 | 提示词资源 | 职责 |
|------|-----------|------|
| 拆素材 | `material_split.txt` | 逐篇拆设计/细化，跨样本提炼共性（≥2 篇同现算「共性」，单篇标「变体」） |
| 学剧情 | `plot_learning.txt` | 产出 plot_design_skill（宏观骨架）+ plot_refine_skill（微观落地） |
| 学文风 | `style_learning.txt` | 产出可执行写手规则（情绪→笔墨密度/句式/对话/动作/转场/收束） |

## 六槽位产出

三阶段收敛为统一「六槽位」模板（`six_slots.tmpl.j2`）：

```
gimmick         骨髓钩子（一句话卖点）
character       人物塑造手法（对话/动作/反应标签）
pacing          节奏模板（情节钩子密度/情绪曲线）
intro           开篇捕获模板（前 N 句/前千字）
plot_refine     情节深化手法（反转/伏笔/铺垫算子）
draft_excerpt   示例片段（带标注的仿写样例）
```

配套 `quality_rules.tmpl.j2`：规则+模板+速查表+自检清单结构。

## 落盘安全（先预览后确认）

- 产出**先写预览区** `.state/learning/preview/`（临时，不入账）。
- 由用户 `/learn confirm`（或 workflow 内显式确认）后才写入技法资产库
  `.state/learning/library.json`。
- 未确认 → 不入库；预览区可随时清空。

## 共性判定

- 样本 ≥ 2 篇共同出现才算「共性」；单篇出现标为「变体」。
- 代码层用入参样本数约束 derive 逻辑，避免把单篇偏好误当市场共性。

## 边界声明

本 skill 为**外部技法学习**，不直接改动书稿；learnings.json（m17_learn 既有提炼）与本技法资产库并存互不覆盖。