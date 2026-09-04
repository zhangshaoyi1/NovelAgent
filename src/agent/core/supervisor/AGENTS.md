# AGENTS.md - core/supervisor/ 长小说监督体系

## 职责

提供长篇小说写作过程的监督能力。

## 核心模块

| 文件 | 导出 | 作用 |
|------|------|------|
| `supervisor.py` | `SupervisorPlugin`, `SupervisorRegistry`, `SupervisorEngine`, `SupervisionReport`, `SupervisionIssue` | 监督引擎 |
| `dimensions.py` | `PlotProgressChecker`, `LanguageGuardChecker`, `StyleDriftChecker`, `TropePayoffChecker` | 各维度检查器 |

## 依赖规则

- 依赖 base、client、story
- 通过延迟导入避免循环依赖