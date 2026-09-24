# Agent Note: 每本书粒度记录 LLM 提示词全文（Prompt File Capture）

Status: implemented

## Problem

用户需求：把每次发给 LLM 的 chat「输入 + 输出全文」按**每本书**粒度、用一个可配置
开关记录到本地，默认关闭，可接入消费者消费并落盘。调研结论：此前**并无此能力**——
事件基建（`EventBus` / `llm.usage`）只记元数据（model/tokens/latency），既不附带
prompt 原文也不记 response 全文；`LLMClient.chat` 内的 `_notify_llm_event` 在全代码
没有任何调用点。用户要求同时在 Web 界面提供 per-book 开关，并打开正在写的书查看每次
发给 LLM 的真实提示词。

## Decision

在既有 `llm.usage` 唯一收口（`gateway_adapter` 三处 `notify_llm_usage`）之上做**正交
叠加**，不新建第二套事件通道、不改变存量行为，遵守「client 层不 import core/workflows」：

1. **client 层（`agent/client/llm_usage.py`）**：新增进程级布尔 `set_llm_capture_prompts`
   / `llm_capture_prompts()`，默认 False。`gateway_adapter` 三处用量事件**仅当 flag 开启**
   才在 payload 附带 `prompt`（messages 序列化）+ `response`（resp.text）。默认关闭 ⇒
   `events.jsonl` / `trace.jsonl` 内容保持精简、零额外开销。
2. **core 消费者（`core/event_sourcing/prompt_capture.py`）**：新增 `PromptFileConsumer`，
   消费 `llm.usage`；payload 含 `prompt` 才把整条（id/timestamp/meta/prompt/response）
   追加写 `<book>/.state/llmops/prompts.jsonl`（原子、目录自动建、写盘失败不阻断）。
   同模块提供 per-book 开关读写 `<book>/.state/llmops.json::capture_prompts`（默认 false）。
3. **装配（`core/event_sourcing/llm_wiring.py::wire_prompt_capture`）**：`AgentService.__init__`
   在 `wire_llm_event_hook` 后按当前书读开关：开 ⇒ `set_llm_capture_prompts(True)` +
   注册 `PromptFileConsumer`；关 ⇒ 复位 flag + 卸载该消费者（避免切换书籍残留状态）。
4. **Web**：`web/state.py` 加读写/读取近期记录辅助；`web/app.py` 加 3 个 API
   （GET/POST 开关、GET 记录）；`project.html` 加「提示词全文捕获」卡片（开关 + 已捕获
   数 + 最近提示词查看）。

## Alternatives considered

### Why not 新增独立的 `llm.chat_prompt` 事件类型？
会引入第二套事件通道与额外的 consumer 接线，且与既有 `llm.usage` 唯一收口重复触发；
叠加在现有收口上复用同一 hook/EventBus 链路，语义更收敛、默认零成本。

### Why not 把开关存进 `.env`？
`.env` 是进程级、跨书共享的，无法表达「每本书各自开关」；`.state/llmops.json` 随书
落地、天然 per-book。

## Consequences

- **默认行为不变**：未开启的书籍不出现在 prompts.jsonl，events/trace 内容不膨胀。
- **有界污染**：开启时 events.jsonl 会含 prompt 原文（复用同一条 `llm.usage` 事件），
  这是设计内取舍——消费/归档由 `PromptFileConsumer` 落到独立文件，事件流其余消费者
  不受影响。
- **进程级 flag 是单值**：进程内同时多书、且开关不一致时存在覆盖竞态。现状每本命令
  由 daemon 拉起独立 CLI 子进程、一次服务一册，故可接受；切换书籍需重新走
  `wire_prompt_capture`（AgentService 每次重建即重新装配）。
- **历史不可回溯**：开关开启前每次调用的 prompt 原文未记录，只能从开启后的下一次
  调用起可读。

## Risks

- `prompt` 序列化可能含非 JSON 对象 → `_messages_to_text` 降级 `str`，异常不外抛。
- 超大 prompt 长写文件有磁盘占用风险 → 由 per-book 开关人工控制，默认关闭。