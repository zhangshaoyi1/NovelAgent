"""故事领域模型层

职责：封装小说创作的核心领域逻辑，管理所有叙事元素。
- 设定集管理器：world.md / subline.md / 角色档案 的读写
- 伏笔管理器：伏笔的埋设、回收、跟踪
- 关系网管理器：角色/势力/地点/物品的关系图谱
- 章节文件 Helper：章节文件的读取、排序、去 frontmatter
- 快照与回滚：设定/关系/章节的版本管理与回滚
- 证据链：章节内容与设定的引用追溯
- 高潮曲线：章节紧张度评估与弧级规划
- 追读力账本（Pacing）：钩子债/伏笔债的跟踪
- 爽点剧本：章节级爽点类型与情绪目标配置
- 注入套路存储：注入套路库存储
- 学习存储：长期记忆与用户偏好沉淀
- 世界观 Schema：世界观结构定义
- 写作方法风格：叙事方法模板
- 设计哲学：产品理念文案

依赖规则：依赖 base、client，不依赖 quality/engine 等其他业务包。
"""

from agent.core.story.setting_manager import SettingManager
from agent.core.story.foreshadow_manager import (
    ForeshadowManager,
    ForeshadowState,
)
from agent.core.story.relation_manager import RelationManager
from agent.core.story.chapters import (
    strip_frontmatter,
    list_chapter_files,
    take_chapter_files,
    iter_chapter_texts,
    read_chapters_text,
)
from agent.core.story.snapshot_manager import SnapshotManager, ResumeBriefing
from agent.core.story.evidence_chain import EvidenceRef, EvidenceChain
from agent.core.story.tension_curve import (
    TensionCurveManager,
    TensionScore,
    ArcPlan,
    ArcPhase,
)
from agent.core.story.pacing_store import PacingStore, Debt
from agent.core.story.payoff_script import build_payoff_script, load_payoff_script, chapter_payoff
from agent.core.story.injected_trope_store import InjectedTropeStore
from agent.core.story.learning_store import LearningStore
from agent.core.story.foresight import (
    derive_status,
    mark_committed,
    ForesightBeat,
    ForesightStore,
    ForesightThread,
)
from agent.core.story.timeline import (
    find_event,
    placements_for_chapter,
    StoryEvent,
    NarrativePlacement,
    Timeline,
)
from agent.core.story.method_style import load_style_guide, load_method_text
from agent.core.story.meta.worldbuilding_schema import (
    IcebergField,
    IcebergDimension,
    IcebergGroup,
    get_iceberg,
    total_fields,
    summary,
)
from agent.core.story.meta.philosophy import (
    TAGLINE,
    OPENING,
    POSITIONING,
    PILLARS,
    CLOSING,
    Pillar,
    render_text,
    render_markdown,
    get_philosophy,
)

__all__ = [
    "SettingManager",
    "ForeshadowManager",
    "ForeshadowState",
    "RelationManager",
    "WorldNode",
    "WorldEdge",
    "WorldGraph",
    "strip_frontmatter",
    "list_chapter_files",
    "take_chapter_files",
    "iter_chapter_texts",
    "read_chapters_text",
    "SnapshotManager",
    "ResumeBriefing",
    "EvidenceRef",
    "EvidenceChain",
    "TensionCurveManager",
    "TensionScore",
    "ArcPlan",
    "ArcPhase",
    "RhythmAlert",
    "PacingStore",
    "Debt",
    "build_payoff_script",
    "load_payoff_script",
    "chapter_payoff",
    "InjectedTropeStore",
    "LearningStore",
    "load_style_guide",
    "load_method_text",
    "_strip_template_title",
    "derive_status",
    "mark_committed",
    "ForesightBeat",
    "ForesightStore",
    "ForesightThread",
    "find_event",
    "placements_for_chapter",
    "StoryEvent",
    "NarrativePlacement",
    "Timeline",
    "IcebergField",
    "IcebergDimension",
    "IcebergGroup",
    "get_iceberg",
    "total_fields",
    "summary",
    "TAGLINE",
    "OPENING",
    "POSITIONING",
    "Pillar",
    "PILLARS",
    "render_text",
    "render_markdown",
    "get_philosophy",
    "CLOSING",
]