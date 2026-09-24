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

# ★ 金三 / 迷爱看阈值**唯一真源**（纪律 #19）：本文件此前与 evaluator /
#   m5_quality_gate / agentic_pipeline / autowrite 各写一份 60/40，
#   用注释担保一致 = 待爆形态。红线 tests/test_golden_threshold_ssot.py 锁派生关系。
from agent.core.quality.golden_policy import (
    GOLDEN_THREE_FLOOR,
    GOLDEN_THREE_TOTAL,
)

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
        "threshold": GOLDEN_THREE_TOTAL,
        "floor": GOLDEN_THREE_FLOOR,
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


def _policy_int(value: Any, fallback: int) -> int:
    """策略值归一为合法分数（0-100 分制）；非法/越界一律回落（项目策略是用户可编辑边界）。"""
    try:
        n = int(value)
    except (TypeError, ValueError):
        return fallback
    return n if 1 <= n <= 100 else fallback


def golden_three_settings(
    policy: dict[str, Any] | None,
    *,
    cli_threshold: Any = None,
    cli_floor: Any = None,
    cli_gate: bool | None = None,
) -> dict[str, Any]:
    """金三门禁的**生效标定**，唯一解析入口：CLI 显式 > 项目策略 > 全局真源。

    ★ 2026-09-25 接线修复：此前 ``DEFAULT_QUALITY_POLICY["golden_three"]`` 只被
    Web 质量面板**显示**（``web/quality_admin.py``）、**无人消费** ⇒ 用户在
    ``.state/quality_policy.json`` 里改了值会看到「页面显示 55、门禁仍按 60 判」
    的假一致。慢热开篇的书需要各自标定（如灵荒工坊 55/30），而全局真源仍只
    ``golden_policy.py`` 一处（纪律 #19，红线 ``test_golden_threshold_ssot``）。

    Args:
        cli_threshold / cli_floor: CLI 显式值（未传传 ``None``）。
        cli_gate: ``False``=CLI 显式关闭（永远优先）；``None``=未显式，由策略决定。

    Returns:
        ``{"gate": bool, "threshold": int, "floor": int}``。
    """
    section = (policy or {}).get("golden_three") or {}
    if not isinstance(section, dict):
        section = {}
    threshold = _policy_int(
        cli_threshold if cli_threshold is not None else section.get("threshold"),
        GOLDEN_THREE_TOTAL,
    )
    floor = _policy_int(
        cli_floor if cli_floor is not None else section.get("floor"),
        GOLDEN_THREE_FLOOR,
    )
    if cli_gate is False:
        gate = False
    elif cli_gate is True:
        gate = True
    else:
        gate = bool(section.get("gate", True))
    return {"gate": gate, "threshold": threshold, "floor": floor}


def _merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """浅层合并（一层 dict 嵌套按 key 合并）。"""
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = {**out[k], **v}
        else:
            out[k] = v
    return out
