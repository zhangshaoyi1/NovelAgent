# AGENTS.md - prompts/ Prompt 配置

## 职责

存放 Prompt 配置文件（markdown 模板 + 元配置），按里程碑/功能域分子目录。

## 目录结构（2026-09-12，20+ 子目录）

| 分组 | 子目录 | 作用 |
|------|--------|------|
| 阶段提示词 | `m1` ~ `m23`、`m_d` | 各里程碑工作流（配置/讨论/大纲/写章/质检/导出…） |
| 门禁/约束 | `g`、`g8`、`g11`、`g12` | 通用约束与门禁提示词（`g/beat_ban.md` 桥段禁用清单、`g/setting_canon_constraint.md` 设定正典约束） |
| 写章 | `m5` | 写章族（`generate.md` 生成、`quality_check.md` 质检、`deslop.md` 净化问题模式目录） |
| 质检 | `quality` | 质量评估提示词（如 `reader_appeal_eval.md` 读者吸引力） |
| 其他 | `e`、`agents`、`budget`、`methods`、`shared` | 评估/智能体/预算/方法/共享片段 |
| 元配置 | `_meta.yaml` | Prompt 元配置 |

## 修改约定

- 提示词随改进批次迭代（P-1~P-12 已落地：质检输出精简、硬约束校验、细纲覆盖校验、deslop 问题模式、事实对照卡、章内情绪节奏、教训进复审重点等），改动需附验收证据
- 改 prompt 不影响已运行进程：换档/改档后必须重启 Web/daemon 进程才生效
