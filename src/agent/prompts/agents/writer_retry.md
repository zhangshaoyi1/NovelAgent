---
name: agents.writer_retry
version: 1
purpose: 自主写章 Agent JSON 重试硬约束
description: 首次 JSON 解析失败后追加的纯 JSON 强制指令（G4 / M14 约定：解析失败重试一次）
---

# system

【输出格式硬约束】上一条输出没能被解析为 JSON，此条必须只输出一个合法 JSON 对象。禁止任何解释文字、禁止 ```json 代码围栏、禁止把章节正文直接作为纯文本输出。JSON 结构如下（字段名必须逐字一致）：
{"think": "简短思考", "action": "finish 或 tool_call", "tool": null, "args": {}, "draft": "完整章节正文或工具参数"}
若 action 为 tool_call，则填 tool/args 并留空 draft；若为 finish，则 draft 填完整章节正文。
