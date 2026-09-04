# AGENTS.md - quality/rewrite/ 反馈改写

## 职责

用户反馈驱动的定向章节重写。

## 核心模块

| 文件 | 作用 |
|------|------|
| `feedback_rewriter.py` | 反馈改写器（FeedbackRewriter / RewriteResult） |

## 依赖规则

- 复用 guardrails/（门禁）与 story/（设定）
- 不依赖其他 sibling 子包