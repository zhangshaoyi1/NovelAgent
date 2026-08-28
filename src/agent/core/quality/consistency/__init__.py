"""设定一致性子包（consistency/）

覆盖设定更新、写作前、章节产出后的设定/时间线/关系网/金手指/境界一致性：
- 一致性校验器（ConsistencyChecker + 内置冲突规则集，checker.py）
- 冲突仲裁服务（ConflictArbiter 检测新设定与现有设定的冲突，conflict_service.py）

依赖规则：checker 经 lazy import 复用同包的 conflict_service，不依赖其他 sibling 子包。
"""

from agent.core.quality.consistency.checker import (
    CheckTrigger,
    ConsistencyChecker,
    ConsistencyReport,
    Severity,
)
from agent.core.quality.consistency.conflict_service import (
    Conflict,
    ConflictArbiter,
    ConflictReport,
)

__all__ = [
    "ConflictArbiter",
    "ConflictReport",
    "Conflict",
    "ConsistencyChecker",
    "CheckTrigger",
    "ConsistencyReport",
    "Severity",
]