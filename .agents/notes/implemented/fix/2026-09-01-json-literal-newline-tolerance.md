# Agent Note: JSON 字符串值内字面换行容错修复

Status: implemented

## Problem

创作类模型（高阶温度 creative）在 Agentic 写章时，常在 JSON 信封的
`draft` 字符串里输出**真实换行**（未转义的 `\n`）。严格 JSON 规定字符串内
不允许字面换行，导致 `parse_llm_json` 的所有 `json.loads` 策略全部失败。

后果（ch237 实证）：

* 完整章节稿在**结构化输出解析层被丢弃**，从未进入门禁与好稿兜底候选；

* 模型被迫反复重新生成（每轮白费约 60s），修订预算被耗尽；

* 兜底机制（best-draft-track）只能看到 stub，误判全为短稿，整章作废。

## Decision

在 `agent/base/utils.py` 的 `parse_llm_json` 入口处，先做一次容错预pass
`_escape_control_in_strings(text)`：仅当处于双引号字符串**内部**时，把字面
换行/回车/Tab 转义为 `\n`/`\r`/`\t`。

约束保证：

* 字符串外的换行/Tab 是合法 JSON 空白，原样保留；

* 既有的 `\n` 转义对（反斜杠+n）不二次破坏；

* 该函数是无副作用的幂等文本改写，全库 `extract_json`/`parse_llm_json`
  统一受益。

## Alternatives considered

* 在兜底候选层额外捕获解析失败原文：能救具体稿，但绕开根因，且修更脆。

* 降低温度根治格式：可缓解但仍是「从生成端降低概率」，无法覆盖偶发。

* 只处理已知 draft 字段：需侵入 schema，通用性差。

## Consequences

* 完整稿现在能顺利通过 `extract_json`，到达门禁与好稿兜底候选；

* 因解析失败导致的重复生成与修订预算浪费显著下降；

* 门禁按 CJK 口径校验不变——若草稿真不足下限（如 ch237 约 2380 CJK），
  仍会被正确拒绝（兜底不写短章），这是预期保护而非回归。

* 已加回归单测 `test_extract_json_draft_with_literal_newlines`（phase0）。

