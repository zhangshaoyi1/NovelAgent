# AI 味检测与压制流水线

from agent.core.anti_ai.detector import (
    AILikenessDetector,
    LexicalChecker,
    SyntacticChecker,
    SemanticChecker,
    StatisticalChecker,
    DetectionResult,
    AIFlavorScanner,
    AIFlavorReport,
    AI_FLAVOR_LIGHT,
    AI_FLAVOR_MEDIUM,
    AI_FLAVOR_HEAVY,
    BANNED_WORDS_PRIMARY,
    BANNED_WORDS_SECONDARY,
)
from agent.core.anti_ai.post_processor import (
    PostProcessor,
    StylisticNoiseInjector,
    DialogueDifferentiator,
    AIismCleaner,
    ProcessingResult,
)
from agent.core.anti_ai.rewriter import (
    DeslopRewriter,
    DeslopResult,
)

__all__ = [
    "AILikenessDetector", "LexicalChecker", "SyntacticChecker",
    "SemanticChecker", "StatisticalChecker", "DetectionResult",
    "AIFlavorScanner", "AIFlavorReport",
    "AI_FLAVOR_LIGHT", "AI_FLAVOR_MEDIUM", "AI_FLAVOR_HEAVY",
    "BANNED_WORDS_PRIMARY", "BANNED_WORDS_SECONDARY",
    "PostProcessor", "StylisticNoiseInjector",
    "DialogueDifferentiator", "AIismCleaner", "ProcessingResult",
    "DeslopRewriter", "DeslopResult",
]
