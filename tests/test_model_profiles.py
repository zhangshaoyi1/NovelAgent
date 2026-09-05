"""多模型管理：档案存储 CRUD + ConfigLoader 解析优先级"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent.base import model_profiles
from agent.base.config import ConfigLoader, _build_llm_config_from_env


@pytest.fixture()
def store_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """隔离的档案库文件（不触碰真实 models.json）"""
    p = tmp_path / "models.json"
    monkeypatch.setenv("NOVEL_MODELS_FILE", str(p))
    monkeypatch.delenv("NOVEL_MODEL_PROFILE", raising=False)
    return p


def test_store_empty_when_missing(store_file: Path) -> None:
    """无档案库文件 → 空库 + 无激活档案（降级不阻断）"""
    assert model_profiles.load_store()["profiles"] == []
    assert model_profiles.active_profile() is None
    assert model_profiles.resolve_profile() is None


def test_upsert_and_get(store_file: Path) -> None:
    p = model_profiles.upsert_profile(
        {"name": "GLM 旗舰", "provider": "openai", "base_url": "https://x/v1",
         "api_key": "sk-abc", "model": "glm-4.7", "enable_thinking": False}
    )
    got = model_profiles.get_profile(p["id"])
    assert got["name"] == "GLM 旗舰"
    assert got["model"] == "glm-4.7"
    assert got["enable_thinking"] is False
    assert got["id"].startswith("mp-")
    # 首个档案自动激活
    assert model_profiles.active_profile()["id"] == p["id"]


def test_upsert_update_keeps_id_and_created(store_file: Path) -> None:
    p1 = model_profiles.upsert_profile({"name": "A", "model": "m1"})
    p2 = model_profiles.upsert_profile({"id": p1["id"], "name": "A2", "model": "m2"})
    assert p2["id"] == p1["id"]
    assert p2["created_at"] == p1["created_at"]
    assert len(model_profiles.list_profiles()) == 1
    assert model_profiles.get_profile(p1["id"])["model"] == "m2"


def test_update_keeps_old_key_when_blank(store_file: Path) -> None:
    """编辑时 api_key 留空由 Web 层回填；upsert 本身空值覆盖为空"""
    p = model_profiles.upsert_profile({"name": "A", "model": "m", "api_key": "sk-old"})
    # 模拟 Web 层行为：编辑请求未带 key 时取旧值
    data = {"id": p["id"], "name": "A", "model": "m", "api_key": ""}
    if not data["api_key"]:
        data["api_key"] = model_profiles.get_profile(p["id"])["api_key"]
    p2 = model_profiles.upsert_profile(data)
    assert p2["api_key"] == "sk-old"


def test_activate_and_delete(store_file: Path) -> None:
    a = model_profiles.upsert_profile({"name": "A", "model": "m1"})
    b = model_profiles.upsert_profile({"name": "B", "model": "m2", "enabled": False})
    assert model_profiles.set_active(b["id"]) is False  # 禁用档案不可激活
    assert model_profiles.set_active(a["id"]) is True
    assert model_profiles.delete_profile(a["id"]) is True
    assert model_profiles.active_profile() is None  # 删除激活档案后清空
    assert model_profiles.delete_profile("mp-none") is False


def test_masked_profile(store_file: Path) -> None:
    p = model_profiles.upsert_profile({"name": "A", "model": "m", "api_key": "sk-1234567890abcd"})
    masked = model_profiles.masked_profile(p)
    assert masked["api_key"].startswith("sk-1")
    assert masked["api_key"].endswith("abcd")
    assert "••••" in masked["api_key"]
    assert masked["has_key"] is True


# ===== ConfigLoader 解析优先级 =====


def _patch_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_API_KEY", "sk-env")
    monkeypatch.setenv("LLM_BASE_URL", "https://env.example/v1")
    monkeypatch.setenv("LLM_MODEL_ID", "env-model")
    monkeypatch.setenv("LLM_TIMEOUT", "120")
    monkeypatch.delenv("LLM_ENABLE_THINKING", raising=False)


def test_config_env_only(store_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_env(monkeypatch)
    cfg = _build_llm_config_from_env()
    assert cfg.model == "env-model"
    assert cfg.api_key == "sk-env"
    assert cfg.timeout == 120
    assert cfg.enable_thinking is None


def test_config_active_profile_wins(store_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """激活档案覆盖 .env / 环境变量（界面配置优先于本地 env）"""
    _patch_env(monkeypatch)
    model_profiles.upsert_profile(
        {"name": "P1", "model": "profile-model", "base_url": "https://p1/v1",
         "api_key": "sk-p1", "enable_thinking": False, "timeout": 300}
    )
    cfg = _build_llm_config_from_env()
    assert cfg.model == "profile-model"
    assert cfg.api_key == "sk-p1"
    assert cfg.base_url == "https://p1/v1"
    assert cfg.enable_thinking is False
    assert cfg.timeout == 300


def test_config_profile_fields_fall_back_to_env(store_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """档案未填的字段逐项回退 env 值"""
    _patch_env(monkeypatch)
    model_profiles.upsert_profile({"name": "P1", "model": "profile-model"})
    cfg = _build_llm_config_from_env()
    assert cfg.model == "profile-model"  # 档案值
    assert cfg.api_key == "sk-env"  # 回退 env
    assert cfg.base_url == "https://env.example/v1"
    assert cfg.timeout == 120


def test_config_explicit_profile_beats_active(store_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """NOVEL_MODEL_PROFILE 环境变量（按次指定）优先于激活档案"""
    _patch_env(monkeypatch)
    a = model_profiles.upsert_profile({"name": "A", "model": "model-a"})
    b = model_profiles.upsert_profile({"name": "B", "model": "model-b", "enabled": False})
    # 激活 A；但本次运行显式指定了 B（禁用档案也允许按次使用）
    assert model_profiles.set_active(a["id"]) is True
    monkeypatch.setenv("NOVEL_MODEL_PROFILE", b["id"])
    cfg = _build_llm_config_from_env()
    assert cfg.model == "model-b"
    assert a["id"] != b["id"]


def test_config_ignores_unknown_explicit_profile(store_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """NOVEL_MODEL_PROFILE 指向不存在档案 → 回退激活档案"""
    _patch_env(monkeypatch)
    a = model_profiles.upsert_profile({"name": "A", "model": "model-a"})
    monkeypatch.setenv("NOVEL_MODEL_PROFILE", "mp-not-exist")
    cfg = _build_llm_config_from_env()
    assert cfg.model == "model-a"


def test_store_corrupt_file_degrades(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """损坏的档案库 → 空库降级，配置解析回退 env 不抛异常"""
    p = tmp_path / "models.json"
    p.write_text("{not valid json", encoding="utf-8")
    monkeypatch.setenv("NOVEL_MODELS_FILE", str(p))
    _patch_env(monkeypatch)
    cfg = _build_llm_config_from_env()
    assert cfg.model == "env-model"
