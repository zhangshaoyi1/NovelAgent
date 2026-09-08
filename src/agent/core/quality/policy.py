"""质量策略配置中心（专家激活统一管理，F-11）

背景：质量门禁/增强审查的激活散落在 CLI 参数（--strict-review/--ai-gate-mode/--cost-tier
等）与各配置文件（guardrails.json / budget.json）中，且 autowrite 与 write 的默认值不一致
（autowrite 默认关闭 D 多维审查，write 默认开启）。

本模块提供**项目级质量策略**（``<project>/.state/quality_policy.json``）+ **全局默认模板**
（代码内置常量）：
- 新增项目未配置时回退全局默认；
- Web ``/quality`` 配置页读写本项目策略；
- CLI 显式参数 > 项目策略 > 全局默认 > 代码内置默认。

激活分层（专家激活体系收口）：
- ``core``  核心线：每章必跑，不裁剪（Writer / Editor / 九项质检 / Guardrails / 字数等确定性检查）
- ``boost`` 增强层：按质量档位裁剪（D 多维审查、deslop 深度、技法/风格注入）——**减 token 的落点**
- ``review`` 成书层：批次/手动（七维终审、迷爱看、黄金三章、多视角评审、Supervisor）

质量档位（profile）只作用于 boost 层，核心线始终执行 → 「减 token 且不影响质量」：
- ``light``    ：关 D 多维审查（每章省 1 次 LLM 调用，约省 20-30% 写章 token）
- ``standard`` ：D 多维开（默认）
- ``strict``   ：全开
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------- 全局默认
DEFAULT_QUALITY_POLICY: dict[str, Any] = {
    "quality_profile": "standard",  # light / standard / strict
    "strict_review": True,          # boost：D 多维审查（爽点/OOC/连贯/追读）
    "guardrails": {
        "gate_mode": "block",       # advisory / block（AI味/注水门禁模式）
        "ai_flavor_severity": "warn",  # warn / error
    },
    "golden_three": {
        "gate": True,
        "threshold": 60,
        "floor": 40,
    },
    "deslop": {"enabled": True},
    "cost": {
        "tier": "balanced",
        "auto_downgrade": True,
        "budget_margin": 1.0,
    },
}

# profile 预设：只覆盖 boost 层可裁剪项（核心线永不裁剪）
PROFILE_PRESETS: dict[str, dict[str, Any]] = {
    "light": {"strict_review": False},
    "standard": {"strict_review": True},
    "strict": {"strict_review": True},
}

POLICY_FILE = ".state/quality_policy.json"


def load_quality_policy(project_dir: str | Path) -> dict[str, Any]:
    """读项目质量策略；缺失/损坏回退全局默认（读失败降级不阻断）。"""
    path = Path(project_dir) / POLICY_FILE
    try:
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return _merge(DEFAULT_QUALITY_POLICY, data)
    except (OSError, ValueError):
        pass  # noqa: SILENT_DEGRADE - 策略读取失败回退全局默认
    return dict(DEFAULT_QUALITY_POLICY)


def save_quality_policy(project_dir: str | Path, policy: dict[str, Any]) -> Path:
    """写项目质量策略（合并默认，保证结构完整）。"""
    path = Path(project_dir) / POLICY_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    merged = _merge(DEFAULT_QUALITY_POLICY, policy)
    path.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def apply_profile(policy: dict[str, Any]) -> dict[str, Any]:
    """把 quality_profile 预设覆盖到 boost 层（显式配置的项优先）。"""
    out = dict(policy)
    profile = str(policy.get("quality_profile") or "standard")
    preset = PROFILE_PRESETS.get(profile, {})
    for k, v in preset.items():
        out.setdefault(k, v)  # 显式配置优先，profile 只补缺省
    return out


def _merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """浅层合并（一层 dict 嵌套按 key 合并）。"""
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = {**out[k], **v}
        else:
            out[k] = v
    return out
