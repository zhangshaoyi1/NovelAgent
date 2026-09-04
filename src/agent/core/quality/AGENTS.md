# AGENTS.md - core/quality/ 质量保障层

## 职责

提供从写作前到写作后的全链路质量校验体系。

## 子包结构

| 子包 | 职责 | 依赖 |
|------|------|------|
| `guardrails/` | 写作门槛与合规（内容安全/形式合规护栏、配置、指纹、全书去重扫描、架构确认门禁） | 标准库 |
| `consistency/` | 设定一致性校验与冲突仲裁 | story、client |
| `scoring/` | 通用+题材层质量评分与读者吸引力评分 | story、client |
| `rewrite/` | 反馈驱动的章节改写 | guardrails、story |

## 依赖规则

- 依赖 `base`、`client`、`story`
- 不依赖 `infra`/`engine` 和上层
- `guardrails/` 仅公用类型与标准库，不依赖 sibling 子包