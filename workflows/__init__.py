"""工作流模块

每个功能模块（M1/M2/M14/M3/M4/M5 等）对应一个工作流文件。
工作流由 WorkflowOrchestrator 编排。
"""

# 工作流注册表：command -> workflow_id
# TODO: 在各 workflow 实现后注册
WORKFLOW_REGISTRY: dict[str, str] = {
    "/start": "m1_config",
    "/discuss": "m2_discuss",
    "/confirm-architecture": "m14_architecture",
    "/outline": "m3_outline",
    "/design-characters": "m4_character",
    "/write": "m5_write_chapter",
}
