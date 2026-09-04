# AGENTS.md - quality/guardrails/ 写作门槛与合规

## 职责

覆盖写作进出的「门槛」类校验。

## 核心模块

| 文件 | 作用 |
|------|------|
| `guardrails.py` | 内容安全/形式合规护栏（Guardrails 及其值类型、GateReport、门禁模式） |
| `confirmation.py` | 项目架构确认门禁（is_architecture_confirmed） |
| `fingerprint.py` | 指纹库持久化（load_fingerprints / save_fingerprints） |
| `dup_scan.py` | 全书跨章段落去重扫描（fullbook_dup_scan） |

## 依赖规则

- 仅公用类型与标准库
- 不依赖 sibling 子包与上层