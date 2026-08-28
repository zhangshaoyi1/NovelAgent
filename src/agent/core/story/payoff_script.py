"""G12 读者反馈闭环：爽点剧本（B2）+ 情绪轨迹（B3）确定性生成与读取。

约定（见 G12/设计.md §11 共享知识）：
- ``.state/payoff_script.json``：章节级爽点剧本 + 情绪目标（``{"chapters": [{"chapter",
  "payoff_type", "intensity", "emotion", "tension", "note"}], "generated_at"}``）。
- ``build_payoff_script`` 为**纯确定性函数**（同输入恒同输出，可测）：按章节占比映射
  压力阶段（铺垫/发展/高潮/结局），从阶段类型池轮转取爽点类型，强度 1-5、张力 1-5。
- 全三态降级：剧本缺失/损坏/关闭 → 空注入，绝不阻断写章。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

# 压力阶段区间（按章节占比；对齐 m5 _determine_pressure_stage 语义，独立实现避免跨层依赖）
# 铺垫 0-30% / 发展 30-75% / 高潮 75-90% / 结局 90-100%
_STAGE_RANGES = [
    ("铺垫", 0.00, 0.30),
    ("发展", 0.30, 0.75),
    ("高潮", 0.75, 0.90),
    ("结局", 0.90, 1.01),
]

# 爽点类型池（拍板 B1：按压力阶段分配；结局段禁新开线）
PAYOFF_TYPE_POOL = {
    "铺垫": ["危机", "情感", "悬念"],
    "发展": ["成长", "打脸", "揭密"],
    "高潮": ["反转", "打脸", "危机"],
    "结局": ["揭密", "情感", "收束"],
}

# 强度基准（拍板 B2：1-5 钳制）
_PAYOFF_INTENSITY_BASE = {"铺垫": 2, "发展": 3, "高潮": 5, "结局": 4}
_EMOTION_BY_STAGE = {
    "铺垫": ["压抑", "好奇", "期待"],
    "发展": ["爽", "燃", "暖"],
    "高潮": ["燃", "怒", "惊"],
    "结局": ["释然", "温馨", "回甘"],
}


def _stage_of(chapter: int, total: int) -> str:
    """章节 → 压力阶段（按占比）。"""
    ratio = (chapter - 1) / max(1, total)
    for stage, lo, hi in _STAGE_RANGES:
        if lo <= ratio < hi:
            return stage
    return "结局"


def build_payoff_script(total_chapters: int, ending_ratio: float = 0.25) -> list[dict[str, Any]]:
    """确定性生成全书爽点剧本（纯函数：同输入恒同输出）。

    Args:
        total_chapters: 目标章节数（≥1）。
        ending_ratio: 结局段占比（仅影响阶段映射注释，实际区间按 _STAGE_RANGES）。

    Returns:
        [{"chapter", "payoff_type", "intensity", "emotion", "tension", "note"}, ...]
    """
    total = max(1, int(total_chapters))
    chapters: list[dict[str, Any]] = []
    # 每阶段类型轮转指针
    pool_idx: dict[str, int] = {}
    for ch in range(1, total + 1):
        stage = _stage_of(ch, total)
        pool = PAYOFF_TYPE_POOL.get(stage, ["悬念"])
        idx = pool_idx.get(stage, 0)
        payoff_type = pool[idx % len(pool)]
        pool_idx[stage] = idx + 1
        # 强度：阶段基准 + 轮转偏移（-1..+1，钳 1-5），高潮章顶格
        intensity = _PAYOFF_INTENSITY_BASE.get(stage, 3) + (idx % 3) - 1
        intensity = max(1, min(5, intensity))
        # 张力：与强度正相关 + 阶段趋势（铺垫低 → 高潮顶 → 结局回落）
        tension = max(1, min(5, intensity))
        # 情绪标签：阶段池轮转
        emo_pool = _EMOTION_BY_STAGE.get(stage, ["期待"])
        emotion = emo_pool[idx % len(emo_pool)]
        chapters.append(
            {
                "chapter": ch,
                "payoff_type": payoff_type,
                "intensity": intensity,
                "emotion": emotion,
                "tension": tension,
                "note": f"{stage}·第{ch}章·{payoff_type}×{intensity}",
            }
        )
    return chapters


def load_payoff_script(
    project_dir: str | Path, enabled: bool = True
) -> dict[str, Any]:
    """读取爽点剧本（三态降级）。

    Returns:
        {"chapters": [...], "generated_at": ...}；缺失/损坏/关闭 → {"chapters": []}。
    """
    if not enabled:
        return {"chapters": []}
    try:
        f = Path(project_dir) / ".state" / "payoff_script.json"
        if not f.exists():
            return {"chapters": []}
        data = json.loads(f.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("chapters"), list):
            return {"chapters": []}
        return data
    except Exception:  # noqa: BLE001 - 剧本损坏降级为空，不阻断
        return {"chapters": []}


def save_payoff_script(project_dir: str | Path, chapters: list[dict[str, Any]]) -> Path:
    """写入剧本（.state/payoff_script.json，原子写 tmp+replace）。"""
    proj = Path(project_dir)
    state_dir = proj / ".state"
    state_dir.mkdir(parents=True, exist_ok=True)
    target = state_dir / "payoff_script.json"
    data = {"generated_at": datetime.now().isoformat(timespec="seconds"), "chapters": chapters}
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(target)
    return target


def chapter_payoff(script: dict[str, Any], chapter: int) -> tuple[str, str]:
    """取本章爽点剧本与情绪目标（格式化文本）。

    Returns:
        (payoff_task, emotion_target)：无匹配章节/空剧本 → ("", "")。
    """
    try:
        chapters = script.get("chapters") or []
        for item in chapters:
            if int(item.get("chapter", 0)) == int(chapter):
                p = item.get("payoff_type", "")
                i = int(item.get("intensity", 0) or 0)
                emo = item.get("emotion", "")
                t = int(item.get("tension", 0) or 0)
                note = item.get("note", "")
                payoff_task = f"本章爽点：{p}（强度 {i}/5）{('，' + note) if note else ''}"
                emotion_target = f"情绪目标：{emo}（张力 {t}/5）" if emo else ""
                return payoff_task, emotion_target
    except Exception:  # noqa: BLE001 - 剧本解析失败降级为空
        pass
    return "", ""
