"""M9 题材扩展机制单元测试

覆盖：
- GenreManifest / GenrePack 数据模型
- load_genre_manifest / load_genre_pack 加载器
- GenrePackRegistry：列举/加载/缓存/查询/subagent-mcp 接口
- 内置修仙题材包（xiuxian）完整性
- CLI 命令注册
"""

from __future__ import annotations

from pathlib import Path

import frontmatter
import pytest

from agent.core.genre_pack import (
    GenreManifest,
    GenrePack,
    GenrePackRegistry,
    load_genre_manifest,
    load_genre_pack,
)


# ============================================================
# 夹具：构造测试用题材包目录
# ============================================================
@pytest.fixture
def fake_skills_dir(tmp_path: Path) -> Path:
    """构造含 2 个题材包 + 1 个非题材 skill 的目录"""
    skills = tmp_path / "skills"
    skills.mkdir()

    # 题材包 1: xiuxian
    xiuxian = skills / "xiuxian"
    xiuxian.mkdir()
    (xiuxian / "SKILL.md").write_text(
        frontmatter.dumps(
            frontmatter.Post(
                "# 修仙题材包\n\n内容。",
                name="xiuxian",
                version="0.2.0",
                type="genre",
                description="修仙题材包",
                hooks=["m1_config.load_genre_template"],
                dependencies=[],
                independent=False,
            )
        ),
        encoding="utf-8",
    )
    (xiuxian / "world-template.md").write_text(
        "# 总设定集模板\n\n## 境界体系\n\n炼气→筑基→金丹→元婴\n\n## 势力框架\n\n三大宗门",
        encoding="utf-8",
    )
    (xiuxian / "tropes.md").write_text("# 爽点套路\n\n- 打脸\n- 装逼\n- 逆袭", encoding="utf-8")
    (xiuxian / "terms.md").write_text("# 术语表\n\n- 灵根\n- 丹药\n- 法器", encoding="utf-8")
    (xiuxian / "combat-template.md").write_text("# 战斗模板\n\n试探→胶着→转折→决胜", encoding="utf-8")
    (xiuxian / "quality-rules.md").write_text("# 质量规则\n\n每 N 章境界推进", encoding="utf-8")

    # 题材包 2: romance（只有 SKILL.md，无片段文件）
    romance = skills / "romance"
    romance.mkdir()
    (romance / "SKILL.md").write_text(
        frontmatter.dumps(
            frontmatter.Post(
                "# 言情题材包",
                name="romance",
                version="0.1.0",
                type="genre",
                description="言情题材包",
            )
        ),
        encoding="utf-8",
    )

    # 非 genre skill: bookworm（type≠genre，应被 list_genres 跳过）
    bookworm = skills / "bookworm"
    bookworm.mkdir()
    (bookworm / "SKILL.md").write_text(
        frontmatter.dumps(
            frontmatter.Post(
                "# 书虫 Skill",
                name="bookworm",
                version="0.1.0",
                type="skill",  # 非 genre
                description="书虫测评",
            )
        ),
        encoding="utf-8",
    )

    return skills


# ============================================================
# GenreManifest / GenrePack 数据模型
# ============================================================
class TestGenreManifest:
    def test_basic(self) -> None:
        m = GenreManifest(name="xiuxian", version="0.1.0")
        assert m.name == "xiuxian"
        assert m.genre_id == "xiuxian"
        assert m.hooks == []
        assert m.dependencies == []

    def test_with_fields(self) -> None:
        m = GenreManifest(
            name="romance",
            version="0.2.0",
            description="言情",
            hooks=["m1.load"],
            dependencies=["core"],
            independent=True,
        )
        assert m.description == "言情"
        assert m.hooks == ["m1.load"]
        assert m.independent is True


class TestGenrePack:
    def test_basic(self) -> None:
        m = GenreManifest(name="xiuxian")
        pack = GenrePack(manifest=m)
        assert pack.name == "xiuxian"
        assert pack.genre_id == "xiuxian"
        assert pack.world_template == ""

    def test_get_world_template_section(self) -> None:
        m = GenreManifest(name="xiuxian")
        pack = GenrePack(
            manifest=m,
            world_template=(
                "# 总设定集\n\n"
                "## 境界体系\n\n炼气→筑基→金丹\n\n"
                "## 势力框架\n\n三大宗门"
            ),
        )
        section = pack.get_world_template_section("境界体系")
        assert "炼气" in section
        assert "筑基" in section
        # 不应包含下一个 section
        assert "势力框架" not in section

    def test_get_world_template_section_not_found(self) -> None:
        m = GenreManifest(name="x")
        pack = GenrePack(manifest=m, world_template="## 境界体系\n\n内容")
        assert pack.get_world_template_section("不存在") == ""

    def test_get_world_template_section_empty(self) -> None:
        m = GenreManifest(name="x")
        pack = GenrePack(manifest=m)
        assert pack.get_world_template_section("任何") == ""

    def test_to_dict(self) -> None:
        m = GenreManifest(
            name="xiuxian",
            version="0.1.0",
            description="修仙",
            hooks=["h1"],
            dependencies=["d1"],
        )
        pack = GenrePack(
            manifest=m,
            world_template="x",
            tropes="y",
            terms="z",
        )
        d = pack.to_dict()
        assert d["name"] == "xiuxian"
        assert d["version"] == "0.1.0"
        assert d["description"] == "修仙"
        assert d["has_world_template"] is True
        assert d["has_tropes"] is True
        assert d["has_terms"] is True
        assert d["has_combat_template"] is False
        assert d["has_quality_rules"] is False
        assert d["hooks"] == ["h1"]
        assert d["dependencies"] == ["d1"]


# ============================================================
# 加载器
# ============================================================
class TestLoadGenreManifest:
    def test_load_success(self, fake_skills_dir: Path) -> None:
        m = load_genre_manifest(fake_skills_dir / "xiuxian")
        assert m.name == "xiuxian"
        assert m.version == "0.2.0"
        assert m.description == "修仙题材包"
        assert "m1_config.load_genre_template" in m.hooks

    def test_load_missing_skill_md(self, tmp_path: Path) -> None:
        d = tmp_path / "empty"
        d.mkdir()
        with pytest.raises(FileNotFoundError):
            load_genre_manifest(d)

    def test_load_missing_name(self, tmp_path: Path) -> None:
        d = tmp_path / "bad"
        d.mkdir()
        (d / "SKILL.md").write_text(
            frontmatter.dumps(frontmatter.Post("# Bad", version="0.1.0", type="genre")),
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="name"):
            load_genre_manifest(d)

    def test_load_wrong_type(self, fake_skills_dir: Path) -> None:
        with pytest.raises(ValueError, match="type"):
            load_genre_manifest(fake_skills_dir / "bookworm")

    def test_load_empty_type_ok(self, tmp_path: Path) -> None:
        """type 字段为空时应允许（向后兼容）"""
        d = tmp_path / "no_type"
        d.mkdir()
        (d / "SKILL.md").write_text(
            frontmatter.dumps(frontmatter.Post("# X", name="x")),
            encoding="utf-8",
        )
        m = load_genre_manifest(d)
        assert m.name == "x"


class TestLoadGenrePack:
    def test_load_full_pack(self, fake_skills_dir: Path) -> None:
        pack = load_genre_pack(fake_skills_dir / "xiuxian")
        assert pack.name == "xiuxian"
        assert "境界体系" in pack.world_template
        assert "打脸" in pack.tropes
        assert "灵根" in pack.terms
        assert "试探" in pack.combat_template
        assert "境界推进" in pack.quality_rules

    def test_load_pack_missing_optional_files(self, fake_skills_dir: Path) -> None:
        """romance 只有 SKILL.md，片段文件应为空字符串"""
        pack = load_genre_pack(fake_skills_dir / "romance")
        assert pack.name == "romance"
        assert pack.world_template == ""
        assert pack.tropes == ""
        assert pack.terms == ""
        assert pack.combat_template == ""
        assert pack.quality_rules == ""


# ============================================================
# GenrePackRegistry
# ============================================================
class TestGenrePackRegistry:
    def test_list_genres(self, fake_skills_dir: Path) -> None:
        registry = GenrePackRegistry(skills_dir=fake_skills_dir)
        genres = registry.list_genres()
        assert "xiuxian" in genres
        assert "romance" in genres
        # bookworm 是 type=skill，不应出现
        assert "bookworm" not in genres

    def test_list_genres_empty_dir(self, tmp_path: Path) -> None:
        registry = GenrePackRegistry(skills_dir=tmp_path / "empty")
        assert registry.list_genres() == []

    def test_list_genres_nonexistent_dir(self, tmp_path: Path) -> None:
        registry = GenrePackRegistry(skills_dir=tmp_path / "missing")
        assert registry.list_genres() == []

    def test_list_available(self, fake_skills_dir: Path) -> None:
        registry = GenrePackRegistry(skills_dir=fake_skills_dir)
        available = registry.list_available()
        names = [g["name"] for g in available]
        assert "xiuxian" in names
        assert "romance" in names
        # 每条都有 version 和 description
        for g in available:
            assert "version" in g
            assert "description" in g

    def test_load_success(self, fake_skills_dir: Path) -> None:
        registry = GenrePackRegistry(skills_dir=fake_skills_dir)
        pack = registry.load("xiuxian")
        assert pack.name == "xiuxian"
        assert "境界体系" in pack.world_template

    def test_load_caches(self, fake_skills_dir: Path) -> None:
        registry = GenrePackRegistry(skills_dir=fake_skills_dir)
        pack1 = registry.load("xiuxian")
        pack2 = registry.load("xiuxian")
        # 同一对象引用（缓存）
        assert pack1 is pack2

    def test_load_not_found(self, fake_skills_dir: Path) -> None:
        registry = GenrePackRegistry(skills_dir=fake_skills_dir)
        with pytest.raises(ValueError, match="题材包不存在"):
            registry.load("unknown")

    def test_is_loaded(self, fake_skills_dir: Path) -> None:
        registry = GenrePackRegistry(skills_dir=fake_skills_dir)
        assert not registry.is_loaded("xiuxian")
        registry.load("xiuxian")
        assert registry.is_loaded("xiuxian")

    def test_get_returns_none_if_not_loaded(self, fake_skills_dir: Path) -> None:
        registry = GenrePackRegistry(skills_dir=fake_skills_dir)
        assert registry.get("xiuxian") is None
        registry.load("xiuxian")
        assert registry.get("xiuxian") is not None

    def test_info(self, fake_skills_dir: Path) -> None:
        registry = GenrePackRegistry(skills_dir=fake_skills_dir)
        info = registry.info("xiuxian")
        assert info["name"] == "xiuxian"
        assert info["has_world_template"] is True
        assert info["has_tropes"] is True

    def test_clear_cache(self, fake_skills_dir: Path) -> None:
        registry = GenrePackRegistry(skills_dir=fake_skills_dir)
        registry.load("xiuxian")
        assert registry.is_loaded("xiuxian")
        registry.clear_cache()
        assert not registry.is_loaded("xiuxian")

    def test_default_skills_dir(self) -> None:
        """不传 skills_dir 时应使用默认 agent/skills/"""
        registry = GenrePackRegistry()
        # 默认目录应存在
        assert registry.skills_dir.exists()
        # 应能找到 xiuxian
        genres = registry.list_genres()
        assert "xiuxian" in genres

    # ------ F9.3 subagent/mcp 接口（v2 留接口）------
    def test_mount_subagent_not_implemented(self, fake_skills_dir: Path) -> None:
        registry = GenrePackRegistry(skills_dir=fake_skills_dir)
        with pytest.raises(NotImplementedError):
            registry.mount_subagent("xiuxian", {})

    def test_mount_mcp_not_implemented(self, fake_skills_dir: Path) -> None:
        registry = GenrePackRegistry(skills_dir=fake_skills_dir)
        with pytest.raises(NotImplementedError):
            registry.mount_mcp("xiuxian", {})


# ============================================================
# 内置修仙题材包完整性
# ============================================================
class TestBuiltinXiuxianPack:
    """验证 agent/skills/xiuxian/ 内置题材包的完整性"""

    @pytest.fixture
    def registry(self) -> GenrePackRegistry:
        return GenrePackRegistry()

    def test_xiuxian_listed(self, registry: GenrePackRegistry) -> None:
        genres = registry.list_genres()
        assert "xiuxian" in genres

    def test_xiuxian_manifest(self, registry: GenrePackRegistry) -> None:
        pack = registry.load("xiuxian")
        assert pack.manifest.name == "xiuxian"
        assert pack.manifest.version
        assert pack.manifest.description

    def test_xiuxian_has_world_template(self, registry: GenrePackRegistry) -> None:
        pack = registry.load("xiuxian")
        assert pack.world_template
        # 应包含境界体系
        assert "境界" in pack.world_template or "炼气" in pack.world_template

    def test_xiuxian_has_tropes(self, registry: GenrePackRegistry) -> None:
        pack = registry.load("xiuxian")
        assert pack.tropes

    def test_xiuxian_has_terms(self, registry: GenrePackRegistry) -> None:
        pack = registry.load("xiuxian")
        assert pack.terms

    def test_xiuxian_has_quality_rules(self, registry: GenrePackRegistry) -> None:
        pack = registry.load("xiuxian")
        assert pack.quality_rules


# ============================================================
# 内置武侠题材包完整性
# ============================================================
class TestBuiltinWuxiaPack:
    """验证 agent/skills/wuxia/ 内置题材包的完整性"""

    @pytest.fixture
    def registry(self) -> GenrePackRegistry:
        return GenrePackRegistry()

    def test_wuxia_listed(self, registry: GenrePackRegistry) -> None:
        genres = registry.list_genres()
        assert "wuxia" in genres

    def test_wuxia_manifest(self, registry: GenrePackRegistry) -> None:
        pack = registry.load("wuxia")
        assert pack.manifest.name == "wuxia"
        assert pack.manifest.version
        assert pack.manifest.description

    def test_wuxia_has_world_template(self, registry: GenrePackRegistry) -> None:
        pack = registry.load("wuxia")
        assert pack.world_template
        # 应包含武功境界体系
        assert "境界" in pack.world_template or "三流" in pack.world_template

    def test_wuxia_has_tropes(self, registry: GenrePackRegistry) -> None:
        pack = registry.load("wuxia")
        assert pack.tropes
        # 应包含比武打脸套路
        assert "打脸" in pack.tropes or "比武" in pack.tropes

    def test_wuxia_has_terms(self, registry: GenrePackRegistry) -> None:
        pack = registry.load("wuxia")
        assert pack.terms
        # 应包含江湖术语
        assert "内力" in pack.terms or "江湖" in pack.terms

    def test_wuxia_has_combat_template(self, registry: GenrePackRegistry) -> None:
        pack = registry.load("wuxia")
        assert pack.combat_template
        # 应包含四段结构
        assert "起手" in pack.combat_template or "交锋" in pack.combat_template

    def test_wuxia_has_quality_rules(self, registry: GenrePackRegistry) -> None:
        pack = registry.load("wuxia")
        assert pack.quality_rules
        # 应包含境界战力校验规则
        assert "境界" in pack.quality_rules or "战力" in pack.quality_rules

    def test_wuxia_and_xiuxian_both_available(self, registry: GenrePackRegistry) -> None:
        """两个题材包应同时可用"""
        genres = registry.list_genres()
        assert "xiuxian" in genres
        assert "wuxia" in genres


# ============================================================
# CLI 命令注册
# ============================================================
class TestCLI:
    def test_cli_commands_registered(self) -> None:
        from agent.cli import app

        names = {c.name or c.callback.__name__ for c in app.registered_commands}
        assert "list_genres" in names
        assert "genre_info" in names
        assert "load_genre" in names
