"""写章防模板注入器（B1）

写章运行时注入"本卷已用手段清单 + 灭门回忆计数"，让 LLM 感知重复并自然轮换，
只约束字数/花式 **不硬删**；当强制换手法时沉淀 learnings（B2）。

设计要点：
- **读前文章节自动统计**（零额外 LLM 成本）：用轻量关键词扫已写章节，估算各冲突手段
  复用次数与灭门回忆次数。
- **只注入动态清单上下文**，不阻断写章；缺章节/读失败 → 返回 "" 降级，不影响主流程。
- 白名单复用 `repair/preferences.md`（若已确认）；未确认则用内置默认手段库。
"""
from __future__ import annotations

import re
from pathlib import Path


# 默认手段库（preferences.md 未确认时的兜底手段清单）
DEFAULT_TROPES = [
    "借刀杀人",
    "设局离间",
    "正面硬刚",
    "交易/出卖",
    "心理战",
    "借势压人",
    "陷阱伏击",
]

# 手段命中正则（轻量估算，非精确；真实肉质判断交给 LLM 精扫）
_TROPE_PATTERNS = {
    "借刀杀人": re.compile(r"借.{0,6}(刀|势|剑|毒).{0,10}(杀人|灭敌|借刀)"),
    "设局离间": re.compile(r"离间|挑拨|反间|制造.{0,6}(嫌隙|猜忌)"),
    "陷阱伏击": re.compile(r"陷阱|伏击|设伏|诱敌深入"),
    "假死": re.compile(r"假死|诈死|装死"),
}

# 灭门回忆正则（与 bad_point_scanner 保持一致口径）
_ANNIHILATION_RECALL_PATTERNS = [
    re.compile(r"灭门"),
    re.compile(r"(当年|从前|昔日|幼年).{0,12}(灭门|满门|全家).{0,6}(惨死|被杀|死|屠)"),
]

_TROPE_CAP = 2      # 单手段允许复用上限
_ANNIHILATION_CAP = 3  # 灭门回忆允许上限


def build_reuse_guard(
    project_dir: str | Path,
    chapter_num: int,
) -> str:
    """生成写章时的防模板注入文本；无章节/读失败 → ""（降级不阻断）。"""
    project_dir = Path(project_dir)
    chapters_dir = project_dir / "chapters"
    if not chapters_dir.exists():
        return ""

    try:
        reuses = _count_tropes(chapters_dir, chapter_num)
        ann_count = _count_annihilation(chapters_dir, chapter_num)
    except Exception:  # noqa: BLE001 - 读失败降级为空
        return ""

    lines: list[str] = []

    # 手段复用提示（只约束，不硬删）
    near = [name for name, n in reuses.items() if n > 0]
    if near:
        topics = "、".join(near)
        lines.append(
            f"本卷此前章节已使用过冲突手段：{topics}。"
            "如本章再采用同类手段，请换用其他切入角度或手法；"
            "若确需复用，请压缩篇幅到最短、或改用不同呈现（规则供参考，人不做硬删）。"
        )

    # 灭门回忆提示
    if ann_count >= _ANNIHILATION_CAP:
        lines.append(
            f"全书灭门回忆已达 {ann_count} 次（已属高频）。本章如无必要，请不要再整段复述灭门记忆；"
            "确需提及则压缩到一两句，不再做完整回忆。"
        )

    return "\n".join(lines)


def _count_tropes(chapters_dir: Path, up_to_chapter: int) -> dict[str, int]:
    counts: dict[str, int] = {k: 0 for k in _TROPE_PATTERNS}
    for path in sorted(chapters_dir.glob("ch[0-9]*.md")):
        num = _chapter_num(path)
        if num >= up_to_chapter:  # 不含本章（本章还没写出来）
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for name, pat in _TROPE_PATTERNS.items():
            counts[name] += len(pat.findall(text))
    return {k: v for k, v in counts.items() if v > 0}


def _count_annihilation(chapters_dir: Path, up_to_chapter: int) -> int:
    total = 0
    for path in sorted(chapters_dir.glob("ch[0-9]*.md")):
        if _chapter_num(path) >= up_to_chapter:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for pat in _ANNIHILATION_RECALL_PATTERNS:
            total += len(pat.findall(text))
    return total


def _chapter_num(path: Path) -> int:
    m = re.search(r"(\d+)", path.stem)
    return int(m.group(1)) if m else 0