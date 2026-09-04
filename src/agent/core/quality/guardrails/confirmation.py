"""架构确认门禁（T-4 上提 core）

统一门禁入口：判断项目架构是否已确认（architecture.md 的 confirmed 字段）。

此前该逻辑作为 ``M14ArchitectureWorkflow.check_confirmed`` 静态方法存在，导致
M3/M4/M5/M6 必须直接 import 其它 workflow 模块，违背「平级 workflow 只依赖 core」
的架构约定。现上提为独立函数，所有模块统一从本模块引用，切断 workflow 间耦合。

Hook 约定（见 ARCHITECTURE_TASKS_EXT.md 第 7 条）：
    统一使用 ``agent.core.quality.guardrails.is_architecture_confirmed(project_dir)``，
    禁止 workflow 间 ``import m14_architecture``。
"""

from __future__ import annotations

from pathlib import Path

import frontmatter


def is_architecture_confirmed(project_dir: Path | str) -> bool:
    """判断架构是否已确认

    读取 ``<project_dir>/architecture.md`` 的 frontmatter ``confirmed`` 字段。

    Args:
        project_dir: 项目目录

    Returns:
        True 若 architecture.md 存在且 ``confirmed == true``；否则 False。
    """
    arch_file = Path(project_dir) / "architecture.md"
    if not arch_file.exists():
        return False
    post = frontmatter.load(arch_file)
    return bool(post.metadata.get("confirmed", False))
