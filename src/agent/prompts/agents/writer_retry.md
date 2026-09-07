---
name: agents.writer_retry
version: 1
purpose: 自主写章 Agent JSON 重试硬约束
description: 首次 JSON 解析失败后追加的纯 JSON 强制指令（G4 / M14 约定：解析失败重试一次）
---

# system


【输出格式硬约束】上一次输出不满足 JSON Schema 校验（可能是无法解析，或解析成功但缺必填字段）。此条必须只输出一个合法 JSON 对象：禁止任何解释文字、禁止 ```json 代码围栏、禁止把章节正文直接作为纯文本输出。必填字段一个都不能少，JSON 结构如下（字段名必须逐字一致）：
{"think": "简短思考", "action": "finish 或 tool_call", "tool": null, "args": {}, "draft": "完整章节正文或工具参数"}

关键规则（上次最常见的失败就是漏掉这些）：
1. "action" 是必填字段，值只能是 "finish" 或 "tool_call"——只输出 {"think","tool","args"} 而漏掉 "action" 是无效的；
2. 若 action 为 tool_call：填 tool/args，draft 置为 null；
3. 若 action 为 finish：draft 填完整章节正文，tool/args 置空。
