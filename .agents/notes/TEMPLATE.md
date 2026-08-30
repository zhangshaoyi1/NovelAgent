# Agent Note: <标题>
Status: proposed

> **使用说明**
> - 路径：`.agents/notes/{lifecycle}/{class}/yyyy-mm-dd-topic-title.md`
>   - `lifecycle`：`proposed/`（实施前评审）· `implemented/`（已交付）· `rejected/`（否决）
>   - `class`：`architecture` / `feature` / `bug-fix` / `simplification` / `process` / `testing`
>   - 文件名日期 = 主题**首次提出**时间（git 历史为准）
> - 头部两行严格为 `# Agent Note: <标题>` + `Status: <status>`，状态与生命周期文件夹一致
> - 架构变更 / 依赖方向 / 跨包契约变更**必须**补充 Agent Note（见 AGENTS.md）
> - implemented 的 `## Decision` 用现在时态，与代码同步更新（路径、名称、结构等事实）
> - 移动到另一 lifecycle 时同步更新 `Status:` 行与骨架

## Problem

（为什么改：现状问题与动机。写法上不依赖解决方案即可独立成文。）

## Proposal

（proposed 用将来时态描述拟议变更：计划、迁移步骤、待解决问题。）

（implemented 改为：）

## Decision

（现在时态描述已交付的现实；可自由组织独特技术章节，如包拓扑、协议约定、schema。）

## Alternatives considered

（必填。每个真实替代方案 + 落选原因，用 `### Why not <X>?` 子节或加粗引导段落。
不记录被否决的方案，就是在邀请反复争论。）

## Acceptance criteria（proposed 用）

## Risks（proposed 用）

## Consequences（implemented 用）

（权衡的代价与收益。）
