"""质量策略 Web 管理（/quality 配置页 + /team 激活总览的数据源）

- 策略读写：委托 core/quality/policy.py（项目级 quality_policy.json + 全局默认）
- 激活总览：core/boost/review 三层专家激活状态（供 /team 页展示，只读）
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agent.core.quality.policy import (
    DEFAULT_QUALITY_POLICY,
    PROFILE_PRESETS,
    apply_profile,
    load_quality_policy,
    save_quality_policy,
)


def resolve_project(name_or_path: str) -> Path | None:
    """把项目名/路径解析为项目目录；无法定位返回 None（前端提示切换空间）。"""
    from agent.web import workspace

    root = workspace.active_root()
    raw = name_or_path.strip().strip('"')
    p = Path(raw)
    if p.is_absolute() and p.exists():
        return p
    cand = root / raw
    return cand if cand.exists() else None


def get_policy(project: str) -> dict[str, Any]:
    """读项目策略（含全局默认模板与档位说明，供表单回显）。"""
    pdir = resolve_project(project)
    if pdir is None:
        return {"ok": False, "message": f"无法定位项目：{project}", "policy": None}
    policy = load_quality_policy(pdir)
    return {
        "ok": True,
        "project": str(pdir),
        "policy": policy,
        "profile_presets": {k: v for k, v in PROFILE_PRESETS.items()},
        "default": DEFAULT_QUALITY_POLICY,
    }


def save_policy(project: str, data: dict[str, Any]) -> dict[str, Any]:
    """保存项目策略（部分更新，合并默认；非法字段忽略）。"""
    pdir = resolve_project(project)
    if pdir is None:
        return {"ok": False, "message": f"无法定位项目：{project}"}
    current = load_quality_policy(pdir)
    # 仅接受已知键，避免注入任意字段
    allowed = {"quality_profile", "strict_review", "guardrails", "golden_three", "deslop", "cost"}
    patch = {k: v for k, v in data.items() if k in allowed and v is not None}
    if "quality_profile" in patch and patch["quality_profile"] not in PROFILE_PRESETS:
        return {"ok": False, "message": f"quality_profile 非法：{patch['quality_profile']}（light/standard/strict）"}
    merged = {**current, **patch}
    save_quality_policy(pdir, merged)
    return {"ok": True, "project": str(pdir), "policy": load_quality_policy(pdir)}


def activation_summary(project: str) -> dict[str, Any]:
    """激活总览：core/boost/review 三层专家状态 + 当前档位（只读）。"""
    pdir = resolve_project(project)
    if pdir is None:
        return {"ok": False, "message": f"无法定位项目：{project}"}
    policy = apply_profile(load_quality_policy(pdir))
    profile = str(policy.get("quality_profile") or "standard")

    layers = {
        "core": [
            {"id": "writer", "name": "写手（WriterAgent）", "active": True, "note": "每章必跑"},
            {"id": "editor", "name": "主编（EditorAgent 一致性硬门禁）", "active": True, "note": "每章必跑"},
            {"id": "quality9", "name": "九项写章质检", "active": True, "note": "每章必跑"},
            {"id": "guardrails", "name": "AI味/注水护栏（确定性）", "active": True, "note": "每章必跑"},
            {"id": "wordcount", "name": "字数/去重确定性门禁", "active": True, "note": "每章必跑"},
        ],
        "boost": [
            {
                "id": "d_review",
                "name": "D 多维审查（爽点/OOC/连贯/追读）",
                "active": bool(policy.get("strict_review", True)),
                "note": "light 档关闭（每章省 1 次 LLM 调用）" if not policy.get("strict_review", True) else "每章执行",
            },
            {
                "id": "deslop",
                "name": "去AI味改写",
                "active": bool((policy.get("deslop") or {}).get("enabled", True)),
                "note": "质量通过后落盘前",
            },
            {"id": "genre_pack", "name": "题材包注入", "active": "按题材", "note": "world.md genre 决定"},
            {"id": "style", "name": "风格模仿（style.md）", "active": "有则注入", "note": "G11"},
            {"id": "payoff", "name": "爽点剧本/情绪目标", "active": "有则注入", "note": "G12"},
            {"id": "craft", "name": "写作技法库（钩子/开篇/爽点）", "active": "按需注入", "note": "methods/"},
        ],
        "review": [
            {"id": "evaluator", "name": "七维终审", "active": "评测时", "note": "autowrite 批次结束"},
            {"id": "appeal", "name": "迷爱看六维", "active": "评测时", "note": "appeal_gate"},
            {
                "id": "golden3",
                "name": "黄金三章评分",
                "active": bool((policy.get("golden_three") or {}).get("gate", True)),
                "note": f"threshold {(policy.get('golden_three') or {}).get('threshold', 60)}",
            },
            {"id": "review_multi", "name": "多视角对抗评审", "active": "手动", "note": "review-book"},
            {"id": "supervisor", "name": "监督体系（Supervisor）", "active": "事件驱动", "note": "M26"},
        ],
    }
    return {"ok": True, "project": str(pdir), "profile": profile, "layers": layers}
