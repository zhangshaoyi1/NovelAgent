"""Skill 注册表（DeepSeek Harness 风格）

统一管理所有 skill 类型的动态发现、加载与注册：
- 书虫测评型 skill（如 bookworm）：提供 CL 命令，通过 SKILL.md 注册
- 题材包型 skill（如 xiuxian）：通过 GenrePackRegistry 管理，提供写作模板
- 编程性 skill（未来扩展）：通过 `@skill` 装饰器注册

设计理念：
- 自动发现：扫描 `skills/` 目录，读取 SKILL.md 发现所有 skill
- 延迟加载：仅列举时读 frontmatter，加载时读完整内容
- 统一注册表：所有 skill 类型共用一个注册表，消费者按名查询
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from agent.core.base.registry import BaseRegistry


class SkillInfo:
    """Skill 元信息（从 SKILL.md frontmatter 解析）"""

    def __init__(
        self,
        name: str,
        version: str,
        type: str,
        description: str,
        label: str = "",
        commands: list[dict[str, Any]] | None = None,
        skill_dir: Path | None = None,
    ) -> None:
        self.name = name
        self.version = version
        self.type = type  # "genre" | "action" | "review"
        self.description = description
        self.label = label or name
        self.commands = list(commands or [])
        self.skill_dir = skill_dir

    @property
    def command_names(self) -> list[str]:
        return [c.get("name", "") for c in self.commands if c.get("name")]

    @property
    def display_name(self) -> str:
        return self.label or self.name

    def to_dict(self) -> dict[str, str]:
        return {
            "name": self.name,
            "version": self.version,
            "type": self.type,
            "description": self.description,
            "label": self.label,
            "commands": ", ".join(self.command_names),
        }


class SkillProvider:
    """Skill 提供者基类（加载后持有 skill 实例）"""

    def __init__(self, info: SkillInfo) -> None:
        self.info = info

    @property
    def name(self) -> str:
        return self.info.name


class SkillRegistry(BaseRegistry[SkillProvider]):
    """统一 Skill 注册表（全局单例）

    管理所有已发现的 skill 元数据与已加载的 skill 实例。
    """

    def __init__(self, skills_dir: Path | None = None) -> None:
        super().__init__()
        if skills_dir is None:
            skills_dir = Path(__file__).resolve().parent.parent / "skills"
        self.skills_dir = Path(skills_dir)
        # 已发现但未加载的 skill 元信息（name → SkillInfo）
        self._discovered: dict[str, SkillInfo] = {}

        # 初始化时扫描一次
        self._discover()

    # ------ 发现 ------

    def _discover(self) -> None:
        """扫描 skills/ 目录，发现所有 skill"""
        if not self.skills_dir.exists():
            return
        for d in sorted(self.skills_dir.iterdir()):
            if not d.is_dir():
                continue
            skill_md = d / "SKILL.md"
            if not skill_md.exists():
                continue
            try:
                info = self._parse_skill_info(d)
                self._discovered[info.name] = info
            except (ValueError, Exception):
                continue

    def _parse_skill_info(self, skill_dir: Path) -> SkillInfo:
        """从 SKILL.md 解析 skill 元信息"""
        import frontmatter

        skill_md = skill_dir / "SKILL.md"
        post = frontmatter.load(skill_md)
        meta = post.metadata

        name = str(meta.get("name", "")).strip()
        if not name:
            raise ValueError(f"SKILL.md 缺少 name 字段: {skill_md}")

        return SkillInfo(
            name=name,
            version=str(meta.get("version", "0.0.0")),
            type=str(meta.get("type", "")),
            description=str(meta.get("description", "")),
            label=str(meta.get("label", "")),
            commands=list(meta.get("commands", []) or []),
            skill_dir=skill_dir,
        )

    # ------ 列举 ------

    def list_skills(self) -> list[SkillInfo]:
        """列出所有已发现的 skill 元信息"""
        return list(self._discovered.values())

    def list_by_type(self, skill_type: str) -> list[SkillInfo]:
        """按类型筛选"""
        return [s for s in self._discovered.values() if s.type == skill_type]

    def list_genres(self) -> list[SkillInfo]:
        """列出所有题材包 skill"""
        return self.list_by_type("genre")

    def list_action_skills(self) -> list[SkillInfo]:
        """列出所有动作型 skill（如 bookworm）"""
        return [
            s for s in self._discovered.values()
            if s.type in ("action", "review", "")
        ]

    # ------ 查询 ------

    def get_info(self, name: str) -> Optional[SkillInfo]:
        """按名查询 skill 元信息"""
        return self._discovered.get(name)

    def is_discovered(self, name: str) -> bool:
        """检查是否已发现"""
        return name in self._discovered


# 全局实例（懒加载，避免模块导入时目录不存在）
_global_registry: Optional[SkillRegistry] = None


def get_skill_registry() -> SkillRegistry:
    """获取全局 SkillRegistry 单例"""
    global _global_registry
    if _global_registry is None:
        _global_registry = SkillRegistry()
    return _global_registry