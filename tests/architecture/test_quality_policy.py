"""F-11 质量策略（quality_policy）单元测试

覆盖：全局默认 / 项目加载与回退 / 保存合并 / profile 预设（boost 层裁剪）。
"""

from __future__ import annotations

import json
from pathlib import Path

from agent.core.quality.policy import (
    DEFAULT_QUALITY_POLICY,
    PROFILE_PRESETS,
    apply_profile,
    load_quality_policy,
    save_quality_policy,
)


def test_default_policy_shape() -> None:
    """全局默认结构完整（strict_review 默认开，与 write 一致）。"""
    assert DEFAULT_QUALITY_POLICY["strict_review"] is True
    assert DEFAULT_QUALITY_POLICY["quality_profile"] == "standard"
    assert "guardrails" in DEFAULT_QUALITY_POLICY
    assert "golden_three" in DEFAULT_QUALITY_POLICY
    assert "cost" in DEFAULT_QUALITY_POLICY


def test_load_missing_returns_default(tmp_path: Path) -> None:
    """项目无配置文件时回退全局默认。"""
    policy = load_quality_policy(tmp_path)
    assert policy["strict_review"] is True
    assert policy["quality_profile"] == "standard"


def test_save_and_load_roundtrip(tmp_path: Path) -> None:
    """保存后读取一致；部分更新与默认合并。"""
    path = save_quality_policy(tmp_path, {"strict_review": False})
    assert path.exists()
    policy = load_quality_policy(tmp_path)
    assert policy["strict_review"] is False
    # 未提供的键保留默认
    assert policy["cost"]["tier"] == "balanced"
    # 嵌套覆盖
    save_quality_policy(tmp_path, {"cost": {"tier": "economy"}})
    policy = load_quality_policy(tmp_path)
    assert policy["cost"]["tier"] == "economy"
    assert policy["cost"]["auto_downgrade"] is True


def test_profile_light_disables_d_review() -> None:
    """light 档裁剪 boost 层（strict_review=False）——减 token 的落点。"""
    light = apply_profile({"quality_profile": "light"})
    assert light["strict_review"] is False
    assert PROFILE_PRESETS["light"]["strict_review"] is False


def test_profile_explicit_wins_over_preset() -> None:
    """显式配置优先于 profile 预设（profile 只补缺省）。"""
    p = apply_profile({"quality_profile": "light", "strict_review": True})
    assert p["strict_review"] is True


def test_corrupt_policy_returns_default(tmp_path: Path) -> None:
    """损坏的配置文件回退全局默认（读失败降级不阻断）。"""
    f = tmp_path / ".state" / "quality_policy.json"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("{ 这不是 JSON", encoding="utf-8")
    policy = load_quality_policy(tmp_path)
    assert policy["strict_review"] is True
