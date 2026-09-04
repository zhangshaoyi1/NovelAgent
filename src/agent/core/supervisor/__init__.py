# 长小说监督体系

from agent.core.supervisor.supervisor import (
    SupervisorPlugin,
    SupervisorRegistry,
    SupervisorEngine,
    SupervisionReport,
    SupervisionIssue,
)
from agent.core.supervisor.dimensions import (
    PlotProgressChecker,
    LanguageGuardChecker,
    StyleDriftChecker,
    TropePayoffChecker,
)

__all__ = [
    "SupervisorPlugin", "SupervisorRegistry", "SupervisorEngine",
    "SupervisionReport", "SupervisionIssue",
    "PlotProgressChecker", "LanguageGuardChecker",
    "StyleDriftChecker", "TropePayoffChecker",
]