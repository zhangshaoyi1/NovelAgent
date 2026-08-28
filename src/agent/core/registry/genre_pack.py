"""M9 题材扩展机制 - GenrePack 抽象与注册表

基于 PRD F9.1-F9.4：

F9.1 MVP 内置修仙题材包（skills/xiuxian/）
F9.2 扩展接口：新题材以 skill 形式封装（题材知识库 + 套路模板 + 风格预设 + 题材层质量规则）
F9.3 复杂题材可挂载 subagent / mcp（v2 留接口）
F9.4 题材包声明自己的 world.md 模板片段与质量规则片段

题材包目录结构（SKILL.md 声明 + 多个片段文件）：
    skills/<genre>/
    ├── SKILL.md              # 能力声明（name/version/type=genre/hooks/dependencies）
    ├── world-template.md     # world.md 模板片段（境界体系/力量体系/势力框架）
    ├── tropes.md             # 爽点套路库
    ├── terms.md              # 术语表
    ├── combat-template.md    # 战斗模板
    ├── quality-rules.md      # 题材层质量规则
    └── genre_expectations/   # 可选：子规则片段目录

加载方式：
    - 自动加载：M1 配置阶段根据用户选择的题材自动加载
    - 手动加载：CLI `/load-genre <name>` 或 `/genre-info <name>`
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import frontmatter

from agent.core.base.registry import BaseRegistry


# ============================================================
# 数据模型
# ============================================================
@dataclass
class Trope:
    """从题材包 tropes.md 中提取的单个套路模板（E2 动态注入用）"""

    name: str                      # 套路名（如 "逆袭"）
    text: str                      # 套路模板正文
    genre: str = ""               # 所属题材


@dataclass
class GenreManifest:
    """题材包能力声明（SKILL.md frontmatter）"""

    name: str
    version: str = "0.1.0"
    description: str = ""
    label: str = ""  # 中文显示名（如 "修仙"），缺省回退 name
    hooks: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    independent: bool = False
    skill_dir: Path | None = None

    @property
    def genre_id(self) -> str:
        return self.name

    @property
    def display_name(self) -> str:
        """中文显示名，缺省回退到 name"""
        return self.label or self.name


@dataclass
class GenrePack:
    """题材包（已加载）

    封装一个题材的全部资源：
        - manifest: 能力声明
        - world_template: world.md 模板片段
        - tropes: 爽点套路库
        - terms: 术语表
        - combat_template: 战斗模板
        - quality_rules: 题材层质量规则
    """

    manifest: GenreManifest
    world_template: str = ""
    tropes: str = ""
    terms: str = ""
    combat_template: str = ""
    quality_rules: str = ""

    @property
    def name(self) -> str:
        return self.manifest.name

    @property
    def genre_id(self) -> str:
        return self.manifest.genre_id

    @property
    def skill_dir(self) -> Path | None:
        return self.manifest.skill_dir

    def get_world_template_section(self, section_name: str) -> str:
        """从 world-template.md 提取指定 section

        Args:
            section_name: 段落标题（如 "境界体系"）

        Returns:
            段落内容（不含标题），未找到返回空字符串
        """
        if not self.world_template:
            return ""
        marker = f"## {section_name}"
        if marker not in self.world_template:
            return ""
        seg = self.world_template.split(marker, 1)[1]
        # 截到下一个 ## 或文件末尾
        if "## " in seg:
            seg = seg.split("## ", 1)[0]
        return seg.strip()

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.manifest.version,
            "description": self.manifest.description,
            "has_world_template": bool(self.world_template),
            "has_tropes": bool(self.tropes),
            "has_terms": bool(self.terms),
            "has_combat_template": bool(self.combat_template),
            "has_quality_rules": bool(self.quality_rules),
            "hooks": self.manifest.hooks,
            "dependencies": self.manifest.dependencies,
        }


# ============================================================
# 加载器
# ============================================================
def load_genre_manifest(skill_dir: Path) -> GenreManifest:
    """从 SKILL.md 解析题材包能力声明

    Args:
        skill_dir: 题材包目录（含 SKILL.md）

    Raises:
        FileNotFoundError: SKILL.md 不存在
        ValueError: frontmatter 缺少 name 字段或 type 不是 genre
    """
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        raise FileNotFoundError(f"SKILL.md 不存在: {skill_dir}")
    post = frontmatter.load(skill_md)
    meta = post.metadata

    name = str(meta.get("name", "")).strip()
    if not name:
        raise ValueError(f"SKILL.md 缺少 name 字段: {skill_md}")

    genre_type = str(meta.get("type", "")).strip()
    # type=genre 是题材包；允许空 type（向后兼容）
    if genre_type and genre_type != "genre":
        raise ValueError(
            f"SKILL.md type 不是 genre（实际: {genre_type}）: {skill_md}"
        )

    return GenreManifest(
        name=name,
        version=str(meta.get("version", "0.1.0")),
        description=str(meta.get("description", "")),
        label=str(meta.get("label", "")),
        hooks=list(meta.get("hooks") or []),
        dependencies=list(meta.get("dependencies") or []),
        independent=bool(meta.get("independent", False)),
        skill_dir=skill_dir,
    )


def load_genre_pack(skill_dir: Path) -> GenrePack:
    """加载完整题材包

    Args:
        skill_dir: 题材包目录

    Raises:
        FileNotFoundError: SKILL.md 不存在
        ValueError: 声明格式错误
    """
    manifest = load_genre_manifest(skill_dir)

    def _read(filename: str) -> str:
        f = skill_dir / filename
        if f.exists():
            return f.read_text(encoding="utf-8")
        return ""

    return GenrePack(
        manifest=manifest,
        world_template=_read("world-template.md"),
        tropes=_read("tropes.md"),
        terms=_read("terms.md"),
        combat_template=_read("combat-template.md"),
        quality_rules=_read("quality-rules.md"),
    )


def _normalize_heading(heading: str) -> str:
    """套路标题归一化：去前导序号（如 '1. '）、转小写、去首尾空格"""
    s = heading.strip().lower()
    s = re.sub(r"^\d+[\.\、]\s*", "", s)
    return s.strip()


def first_genre(metadata: dict) -> str:
    """从 world.md 元数据取主题材 id（多题材取列表首个），兼容旧单数 ``genre`` 字段。

    多题材重构后 world.md 元数据只写 ``genres``（列表）；旧项目可能仍为 ``genre``
    单值。所有「读单个题材名」的上下文构建应统一走本函数，避免取到空值。
    """
    genres = metadata.get("genres") or []
    if genres:
        return str(genres[0])
    return str(metadata.get("genre", ""))


def extract_trope_section(tropes_text: str, trope_name: str) -> tuple[str, str]:
    """从 tropes.md 文本中提取指定套路的内容段

    Args:
        tropes_text: 题材包 tropes.md 全文
        trope_name: 套路名（支持模糊匹配，如 "绝境逆袭" 可匹配 "逆袭"）

    Returns:
        (heading, body) —— 匹配到的段落标题与正文

    Raises:
        ValueError: 未找到该套路（附带可用套路列表）
    """
    target = _normalize_heading(trope_name)
    sections: list[tuple[str, str, str]] = []  # (归一标题, 原标题, 正文)
    parts = re.split(r"(?m)^##\s+", tropes_text)
    for seg in parts[1:]:
        lines = seg.splitlines()
        if not lines:
            continue
        heading = lines[0].strip()
        body = "\n".join(lines[1:]).strip()
        sections.append((_normalize_heading(heading), heading, body))

    for norm_h, heading, body in sections:
        if norm_h == target or target in norm_h or norm_h in target:
            return heading, body

    available = [h for _, h, _ in sections]
    raise ValueError(
        f"套路 '{trope_name}' 不在 tropes.md 中。"
        f"可用套路：{', '.join(available) if available else '（空）'}"
    )


# ============================================================
# 注册表
# ============================================================
class GenrePackRegistry(BaseRegistry[GenrePack]):
    """题材包注册表（DeepSeek Harness 风格）

    继承 BaseRegistry，使用统一的 register()/get()/list()/has() 接口。
    管理 skills/ 目录下 type=genre 的题材包自动发现、缓存加载与查询。

    用法：
        registry = GenrePackRegistry()
        # 列出可用题材
        genres = registry.list_genres()  # 基于 BaseRegistry.list()
        # 加载修仙题材包
        pack = registry.load("xiuxian")  # 自动注册到 BaseRegistry
        # 查询已加载的题材包
        pack = registry.get("xiuxian")
    """

    def __init__(self, skills_dir: Path | None = None) -> None:
        super().__init__()
        if skills_dir is None:
            # 默认 agent/skills/
            skills_dir = Path(__file__).resolve().parent.parent / "skills"
        self.skills_dir = Path(skills_dir)
        # 已发现但未注册（未加载）的题材包元信息
        self._discovered: dict[str, GenreManifest] = {}

        # 初始化时扫描一次
        self._discover()

    # ------ 发现 ------

    def _discover(self) -> None:
        """扫描 skills/ 目录，发现所有 type=genre 的题材包"""
        if not self.skills_dir.exists():
            return
        for d in sorted(self.skills_dir.iterdir()):
            if not d.is_dir():
                continue
            skill_md = d / "SKILL.md"
            if not skill_md.exists():
                continue
            try:
                manifest = load_genre_manifest(d)
                self._discovered[manifest.name] = manifest
            except (ValueError, FileNotFoundError):
                # 非 genre 类型（如 bookworm skill）跳过
                continue

    # ------ 列举（基于 BaseRegistry.list() 扩展）------

    def list_genres(self) -> list[str]:
        """列出所有已发现题材包名称（仅扫描 frontmatter，不触发加载）"""
        return list(self._discovered.keys())

    def list_genres_light(self) -> list[dict[str, str]]:
        """渐进式披露：仅读取每个题材包 SKILL.md 的 frontmatter（name/label/description），

        不加载任何完整内容（world-template/tropes/terms 等），成本极低。
        返回 [{id, label, description}]，供 UI 多选与列表展示（中文 label）。

        注意：这是 Web UI 与新建项目表单应优先使用的列举接口。
        不要用 list_available()，它会一次性全量加载所有题材包，成本高。
        """
        result: list[dict[str, str]] = []
        for name, manifest in self._discovered.items():
            result.append(
                {
                    "id": manifest.name,
                    "label": manifest.display_name,
                    "description": manifest.description,
                }
            )
        return result

    def list_available(self) -> list[dict[str, str]]:
        """[已弃用] 列出所有可用题材包的详细信息。

        ⚠️ 此接口会对每个题材包调用 load()，**一次性全量加载所有题材内容**，
        成本高（内存 + 上下文膨胀）。请改用 list_genres_light() 做列举，
        仅对选中题材调用 load()。
        """
        result: list[dict[str, str]] = []
        for name in self.list_genres():
            try:
                pack = self.load(name)
                result.append(
                    {
                        "name": pack.name,
                        "version": pack.manifest.version,
                        "description": pack.manifest.description,
                    }
                )
            except Exception:
                continue
        return result

    # ------ 加载 ------

    def load(self, name: str) -> GenrePack:
        """加载题材包（带缓存，自动注册到 BaseRegistry）

        Args:
            name: 题材包名称

        Raises:
            ValueError: 题材包不存在或格式错误
        """
        existing = self.get(name)
        if existing is not None:
            return existing

        if name not in self._discovered:
            raise ValueError(
                f"题材包不存在: {name}，可用题材: {', '.join(self.list_genres())}"
            )

        manifest = self._discovered[name]
        if not manifest.skill_dir:
            raise ValueError(f"题材包 {name} 缺少 skill_dir")

        pack = load_genre_pack(manifest.skill_dir)
        self.register(name, pack)
        return pack

    def is_loaded(self, name: str) -> bool:
        return self.has(name)

    def load_trope(self, genre: str, trope_name: str) -> Trope:
        """从指定题材包提取单个套路模板（E2 动态注入）

        Args:
            genre: 题材名（如 "xiuxian"）
            trope_name: 套路名（如 "逆袭" / "绝境逆袭"）

        Returns:
            Trope（name + text + genre）

        Raises:
            ValueError: 题材包不存在 / 无 tropes.md / 套路不存在
        """
        pack = self.load(genre)
        if not pack.tropes:
            raise ValueError(f"题材包 '{genre}' 未提供 tropes.md")
        heading, body = extract_trope_section(pack.tropes, trope_name)
        # 去掉套路标题的前导序号（如 "3. "），保留干净的套路名
        clean_name = _normalize_heading(heading) or heading
        return Trope(name=clean_name, text=body, genre=genre)

    def info(self, name: str) -> dict[str, Any]:
        """查询题材包信息"""
        pack = self.load(name)
        return pack.to_dict()

    def clear_cache(self) -> None:
        """清空缓存（清空 BaseRegistry 注册表）"""
        self._registry.clear()

    # ------ F9.3 subagent/mcp 挂载接口（v2 留接口）------
    def mount_subagent(self, genre: str, subagent_config: dict[str, Any]) -> None:
        """挂载 subagent 处理复杂题材（v2 接口）

        Args:
            genre: 题材名
            subagent_config: subagent 配置

        Note:
            v1 仅留接口，不实现具体逻辑。
        """
        raise NotImplementedError("subagent 挂载为 v2 功能，当前版本未实现")

    def mount_mcp(self, genre: str, mcp_config: dict[str, Any]) -> None:
        """挂载 MCP 服务处理复杂题材（v2 接口）

        Args:
            genre: 题材名
            mcp_config: MCP 配置

        Note:
            v1 仅留接口，不实现具体逻辑。
        """
        raise NotImplementedError("MCP 挂载为 v2 功能，当前版本未实现")
