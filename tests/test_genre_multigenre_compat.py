"""多题材兼容回归测试（2026-08-21）

背景：多题材重构后 world.md 元数据只写 ``genres``（列表），而 m2/m3/m4/m5/m14 的
上下文构建与 T-3 质检 / E2 套路注入仍读单数 ``genre`` 字段 → 恒空、功能静默失效。
本测试覆盖修复后的三条链路：

1. ``first_genre`` 公共 helper（多题材取主题材 / 兼容旧 genre 单值）
2. ``_extract_world_info`` 输出 genres 列表（T-3 数据源）
3. ``_collect_injected_tropes`` 多题材遍历找套路（E2）
4. ``inject-genre`` CLI 默认题材从 genres 遍历（不再固定回退 xiuxian）
"""
from __future__ import annotations

from pathlib import Path

import pytest

from agent.core.registry.genre_pack import first_genre
from agent.workflows.m5_write_chapter import M5WriteChapterWorkflow
from agent.cli.commands import inject_genre as inject_genre_cmd


# ---------- 假对象 ----------
class FakeTrope:
    name = "绝境逆袭"
    text = "套路正文：绝境中反击"


class FakePack:
    quality_rules = "题材层质量规则：不得机械复制套路"


class FakeRegistry:
    """xiuxian 无该套路、wuxia 有——验证多题材遍历能命中非主题材"""

    def load(self, g: str):
        if g in ("xiuxian", "wuxia"):
            return FakePack()
        raise ValueError(f"题材包不存在：{g}")

    def load_trope(self, g: str, name: str):
        if g == "wuxia" and name == "绝境逆袭":
            return FakeTrope()
        raise ValueError(f"套路不存在：{name} in {g}")


class FakeStore:
    def __init__(self, names: list[str] | None = None):
        self._names = list(names or [])

    def get(self) -> list[str]:
        return self._names

    def add(self, name: str) -> list[str]:
        self._names.append(name)
        return self._names


class FakeSM:
    def __init__(self, metadata: dict):
        self._metadata = metadata

    def load_world(self) -> dict:
        return {"metadata": self._metadata}


def make_wf(store_names: list[str], metadata: dict) -> M5WriteChapterWorkflow:
    from rich.console import Console

    wf = object.__new__(M5WriteChapterWorkflow)
    wf._genre_registry = FakeRegistry()
    wf._injected_store = FakeStore(store_names)
    wf.console = Console()
    wf.sm = FakeSM(metadata)
    return wf


# ---------- 1. first_genre ----------
class TestFirstGenre:
    def test_genres_list_takes_first(self):
        assert first_genre({"genres": ["xiuxian", "wuxia"]}) == "xiuxian"

    def test_legacy_single_genre_field(self):
        assert first_genre({"genre": "wuxia"}) == "wuxia"

    def test_genres_precedence_over_legacy(self):
        assert first_genre({"genres": ["kehuan"], "genre": "wuxia"}) == "kehuan"

    def test_empty_metadata(self):
        assert first_genre({}) == ""


# ---------- 2. _extract_world_info ----------
class TestExtractWorldInfo:
    def test_multigenre_metadata_exposes_genres_list(self):
        wf = make_wf([], {})
        world_data = {
            "metadata": {"title": "T", "genres": ["xiuxian", "wuxia"]},
            "content": "## 故事简介\n简介\n## 境界体系\n境界\n## 金手指登记\n金手指\n",
        }
        wi = wf._extract_world_info(world_data)
        assert wi["genres"] == ["xiuxian", "wuxia"]
        assert wi["genre"] == "xiuxian"  # 主题材（兼容旧消费者）

    def test_legacy_single_genre_fallback(self):
        wf = make_wf([], {})
        world_data = {
            "metadata": {"title": "T", "genre": "wuxia"},
            "content": "## 故事简介\n简介\n## 境界体系\n境界\n## 金手指登记\n金手指\n",
        }
        wi = wf._extract_world_info(world_data)
        assert wi["genres"] == []
        assert wi["genre"] == "wuxia"


# ---------- 3. _collect_injected_tropes（E2 多题材遍历） ----------
class TestCollectInjectedTropes:
    def test_finds_trope_in_secondary_genre(self):
        """套路在第二题材（wuxia）——多题材遍历必须命中，而非只在主题材找"""
        wf = make_wf(
            ["绝境逆袭"],
            {"genres": ["xiuxian", "wuxia"]},
        )
        ctx = {"world_info": wf._extract_world_info({"metadata": {"genres": ["xiuxian", "wuxia"]}, "content": ""})}
        out = wf._collect_injected_tropes(ctx)
        assert "绝境逆袭" in out
        assert "套路正文" in out

    def test_not_found_returns_empty_without_crash(self):
        wf = make_wf(["不存在的套路"], {"genres": ["xiuxian"]})
        ctx = {"world_info": {"genres": ["xiuxian"]}}
        assert wf._collect_injected_tropes(ctx) == ""

    def test_legacy_world_info_genre_fallback(self):
        wf = make_wf(["绝境逆袭"], {"genre": "wuxia"})
        # 旧项目：world_info 只有 genre 单值（无 genres 键）
        ctx = {"world_info": {"genre": "wuxia"}}
        out = wf._collect_injected_tropes(ctx)
        assert "绝境逆袭" in out


# ---------- 4. inject-genre CLI 默认题材遍历 ----------
class TestInjectGenreCli:
    def test_no_genre_flag_searches_all_declared_genres(self, tmp_path: Path, monkeypatch):
        pdir = tmp_path / "proj"
        pdir.mkdir(parents=True)
        (pdir / "world.md").write_text(
            "---\ngenres: ['xiuxian', 'wuxia']\n---\n# world\n", encoding="utf-8"
        )

        calls: list[str] = []
        store = FakeStore()

        class _FakeSM:
            def __init__(self, _path):
                self._path = _path

            def load_world(self) -> dict:
                return {"metadata": {"genres": ["xiuxian", "wuxia"]}}

        monkeypatch.setattr("agent.core.registry.genre_pack.GenrePackRegistry", FakeRegistry)
        monkeypatch.setattr("agent.core.story.injected_trope_store.InjectedTropeStore", lambda _p: store)
        monkeypatch.setattr("agent.core.story.setting_manager.SettingManager", _FakeSM)
        monkeypatch.setattr(
            "agent.cli.commands.inject_genre.enforce_gate", lambda *a, **k: None
        )

        # 不传 --genre：应从 genres 列表遍历命中 wuxia 的套路（不再固定回退 xiuxian）
        inject_genre_cmd.inject_genre(
            name="绝境逆袭", genre="", clear=False, project_dir=str(pdir)
        )
        assert store.get() == ["绝境逆袭"]

    def test_explicit_genre_still_wins(self, tmp_path: Path, monkeypatch):
        pdir = tmp_path / "proj"
        pdir.mkdir(parents=True)
        (pdir / "world.md").write_text(
            "---\ngenres: ['xiuxian']\n---\n# world\n", encoding="utf-8"
        )
        store = FakeStore()

        monkeypatch.setattr("agent.core.registry.genre_pack.GenrePackRegistry", FakeRegistry)
        monkeypatch.setattr("agent.core.story.injected_trope_store.InjectedTropeStore", lambda _p: store)
        monkeypatch.setattr("agent.core.story.setting_manager.SettingManager", lambda _p: FakeSM({"genres": ["xiuxian"]}))
        monkeypatch.setattr(
            "agent.cli.commands.inject_genre.enforce_gate", lambda *a, **k: None
        )

        inject_genre_cmd.inject_genre(
            name="绝境逆袭", genre="wuxia", clear=False, project_dir=str(pdir)
        )
        assert store.get() == ["绝境逆袭"]

    def test_not_found_in_any_genre_exits_1(self, tmp_path: Path, monkeypatch):
        pdir = tmp_path / "proj"
        pdir.mkdir(parents=True)
        (pdir / "world.md").write_text(
            "---\ngenres: ['xiuxian']\n---\n# world\n", encoding="utf-8"
        )

        monkeypatch.setattr("agent.core.registry.genre_pack.GenrePackRegistry", FakeRegistry)
        monkeypatch.setattr("agent.core.story.injected_trope_store.InjectedTropeStore", lambda _p: FakeStore())
        monkeypatch.setattr("agent.core.story.setting_manager.SettingManager", lambda _p: FakeSM({"genres": ["xiuxian"]}))
        monkeypatch.setattr(
            "agent.cli.commands.inject_genre.enforce_gate", lambda *a, **k: None
        )

        from typer import Exit

        with pytest.raises(Exit) as ei:
            inject_genre_cmd.inject_genre(
                name="不存在的套路", genre="", clear=False, project_dir=str(pdir)
            )
        assert ei.value.exit_code == 1
