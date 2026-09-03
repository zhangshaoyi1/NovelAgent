# Agent Note: Writer 提示词注入具体字数强约束

Status: implemented

## Problem

Agentic Writer（`agent/agents/writer_agent.py`）的系统提示 `_WRITER_BASE` 中，字数
要求仅有相对描述：

> 「目标字数×0.8 到 目标字数×1.2」之间

**但没有把具体数值注入**。模型只知道一个抽象区间，不了解本章实际下限/上限数字，
因而容易产出偏短正文。ch241 实证：Writer 三轮提交 2398 / 1635 / 2669 字，全部低于
下限 2400 字，修订预算耗尽后判「反复产出过短章节」放弃落盘。

旧式 M5 workflow 模板 `prompts/m5/generate.md`（L35-36）用 Jinja 渲染了
`chapter_length*0.8 ~ *1.2` 的**具体数字**，所以 M5 路径字数达标更好；Agentic
路径反而缺失，造成两条路径行为不一致。

## Decision

在 `WriterAgent` 新增 `_word_budget(ctx)`（同 `_min_len_from_ctx` 口径，读
`world_info.chapter_length` / `chapter_length`），解析出本章【下限、上限】；并在
`_draft` / `_draft_async` 通过新的 `_system_prompt()` 组装提示词时，把这组**具体
数字**作为强约束追加进 system prompt：

> 1. 【字数硬性约束】本章正文的中文字数**必须**在 {min}-{max} 字（中值约 X 字）。
>    哪一片段偏短就加工哪一片段，务必写足下限再 commit，禁止用『伏笔/悬念一句带过』
>    压缩篇幅。

`run` / `run_async` 每次起草与修订都把 min/max 传入，保证首稿即受具体字数约束。

未知目标字数时返回 `(None, None)`，此时不追加硬性约束（保留原相对描述，门禁仍兜底）。

## Alternatives considered

* 对 `_WRITER_BASE` 做 `.format()`：不可行——该常量含大量既有 `{}`/`}`（JSON 信封
  示例），直接 format 会破坏。故采用「常量 + 追加子句」而非模板化。

* 依赖 `count_words` 工具自检后模型自觉扩写：依赖模型主动性，不稳定；把硬数字放进
  提示更直接。

* 复刻 Jinja 模板进 Agentic：把 m5.generate 的 user 侧渲染用于 Agentic 反而耦合
  两套主线；本轮只补 system 侧硬约束，改动最小。

## Consequences

* Agentic Writer 首稿即知本章字数区间（如 2400-3600），字数产出应更贴近目标；

* 与 M5 workflow 的提示词口径趋向一致，两条路径字数约束对齐；

* 门禁（`resolve_min/max_cjk_words`）逻辑不变，硬拦截与提示词强约束互为双保险；

* talk 路径 light tier 仍可不带 ctx（无硬数字注入）回退，不破坏既有离线测试；

* 风险：提示词尾部增大 token；用 `//` 取中值展示，纯显示用，不参与判定。

