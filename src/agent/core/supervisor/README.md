# supervisor/ — 长小说监督体系

## 职责
对长篇小说写作过程进行全局监督，确保全书"不崩"。

## 包含文件
| 文件 | 职责 |
|------|------|
| `dimensions.py` | 监督维度定义 |
| `supervisor.py` | 监督引擎（SupervisorEngine, SupervisionReport） |

## 依赖规则
- 依赖 base/、story/、quality/

## 被依赖
- 长篇小说写作流水线