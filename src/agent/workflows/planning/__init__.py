"""planning/ 目录：写作规划阶段（M1-M4）"""
from .m1_config import M1ConfigWorkflow, M1Input, M1Result
from .m2_discuss import M2DiscussWorkflow, M2Input, M2Result
from .m3_outline import M3OutlineWorkflow, M3Result
from .m4_character import M4CharacterWorkflow, M4Result

__all__ = [
    "M1ConfigWorkflow", "M1Input", "M1Result",
    "M2DiscussWorkflow", "M2Input", "M2Result",
    "M3OutlineWorkflow", "M3Result",
    "M4CharacterWorkflow", "M4Result",
]
