"""支线压力曲线同步（缺口 B，2026-09-08）。

背景（novels/五灵破归档 实证，见 .workbuddy/memory/2026-09-08.md 21:00 条目）：
``MainlineOrchestrator`` 的推进裁决取 ``upper = min(压力曲线上界, 预算 cap)``
（``workflows/pipeline/mainline.py``）。压力曲线写在各 ``sublines/<sid>/subline.md``
里是**规划期一次性生成、此后静态不变**的；而分线预算由 LLM 主编每 ``replan_window``
章动态重规划。两者不一致时 ``min()`` 只在一个方向生效：

- 预算**削减** → cap 更小 → 立即生效；
- 预算**扩张** → 曲线上界更小 → **永远不生效**（S04 给 370 实际只写 320）。

长期看这是**棘轮效应**：每轮重规划只能把支线越切越短，且 LLM 拿不到"未兑现"
的反馈，下一轮继续在被钳制的值上规划，误差单调累积。

不只是上界问题：压力曲线的四阶段（铺垫/冲突/高潮/舒缓）直接驱动 M5 的张力等级
判定（``m5_write_chapter._determine_pressure_stage``）。预算改了而曲线没改，
Writer 会在**错误的张力阶段**写章（例如按新预算应在高潮段，曲线仍指向铺垫段）。

本模块提供「预算 → 压力曲线」的单向同步：预算重规划落盘后，按新区间等比缩放
各阶段长度并重写 ``subline.md`` 的「剧集压力曲线」表，使两个数据源始终同口径。

设计原则：
- **纯函数优先**：``rebuild_curve_section`` 只做字符串变换，便于单测。
- **G3 降级**：同步失败只告警，绝不阻断写章（调用方负责 try/except）。
- **不改语义**：只改章节区间数字，阶段名与张力等级原样保留。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable, Optional

CURVE_SECTION = "剧集压力曲线"

_ROW_RE = re.compile(
    r"^\|\s*(?P<stage>[^|]+?)\s*\|\s*(?P<lo>\d+)\s*[-~]\s*(?P<hi>\d+)\s*\|\s*(?P<tension>[^|]*?)\s*\|?\s*$"
)


def parse_curve_rows(section_text: str) -> list[dict[str, object]]:
    """解析压力曲线表的数据行。

    Args:
        section_text: ``## 剧集压力曲线`` 段落内容（不含标题行）。

    Returns:
        每阶段一行：``{"stage": 阶段名, "lo": 起始章, "hi": 结束章, "tension": 张力}``。
        表头/分隔行/无法解析的行被忽略。
    """
    rows: list[dict[str, object]] = []
    for line in section_text.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        m = _ROW_RE.match(line)
        if not m:
            continue
        lo, hi = int(m.group("lo")), int(m.group("hi"))
        if hi < lo:
            continue
        rows.append(
            {
                "stage": m.group("stage"),
                "lo": lo,
                "hi": hi,
                "tension": m.group("tension"),
            }
        )
    return rows


def _scale_lengths(lengths: list[int], total: int) -> list[int]:
    """把 lengths 按比例缩放到总和恰为 total（最大余数法，保证不丢章）。

    Args:
        lengths: 原各段长度（均 ≥ 0）。
        total: 缩放后的目标总长（≥ 段数，保证每段至少 1 章）。

    Returns:
        新长度列表，``sum(result) == total``（lengths 全 0 时退化均分）。
    """
    n = len(lengths)
    if n == 0 or total <= 0:
        return []
    src_total = sum(lengths)
    if src_total <= 0:  # 原表全零长 → 均分
        base, rem = divmod(total, n)
        return [base + (1 if i < rem else 0) for i in range(n)]
    if total < n:  # 目标比段数还小 → 每段至少 1 章，多余的给首段
        out = [1] * n
        out[0] += total - n
        return out

    raw = [total * ln / src_total for ln in lengths]
    floors = [int(x) for x in raw]
    deficit = total - sum(floors)
    order = sorted(
        range(n), key=lambda i: (raw[i] - floors[i], lengths[i]), reverse=True
    )
    for k in range(deficit):
        floors[order[k % n]] += 1
    return floors


def rebuild_curve_section(
    section_text: str, start: int, end: int
) -> Optional[str]:
    """把压力曲线表的区间整体重映射到 ``[start, end]``，返回新段落文本。

    原表各阶段的**相对长度比例**保持不变（铺垫/冲突/高潮/舒缓的结构意图被保留），
    张力等级列原样保留。表头行与非数据行原样保留。

    Args:
        section_text: 原段落文本（含表头/分隔行）。
        start: 新起始章号（≥1）。
        end: 新结束章号（≥ start）。

    Returns:
        新段落文本；无有效数据行或区间非法时返回 None（调用方应跳过同步）。
    """
    if start < 1 or end < start:
        return None
    rows = parse_curve_rows(section_text)
    if not rows:
        return None

    lengths = [int(r["hi"]) - int(r["lo"]) + 1 for r in rows]
    new_lengths = _scale_lengths(lengths, end - start + 1)

    new_rows: dict[int, tuple[int, int]] = {}
    cursor = start
    for i, ln in enumerate(new_lengths):
        lo = cursor
        hi = cursor + ln - 1
        if i == len(new_lengths) - 1:  # 末段对齐上界，吸收舍入残差
            hi = end
        new_rows[i] = (lo, hi)
        cursor = hi + 1

    row_idx = 0
    out_lines: list[str] = []
    for line in section_text.splitlines():
        stripped = line.strip()
        m = _ROW_RE.match(stripped) if stripped.startswith("|") else None
        if m and row_idx in new_rows and int(m.group("hi")) >= int(m.group("lo")):
            lo, hi = new_rows[row_idx]
            row_idx += 1
            indent = line[: len(line) - len(line.lstrip())]
            out_lines.append(
                f"{indent}| {m.group('stage')} | {lo}-{hi} | {m.group('tension')} |"
            )
        else:
            out_lines.append(line)
            if m:
                row_idx += 1
    return "\n".join(out_lines)


def _replace_section(content: str, section_name: str, new_body: str) -> Optional[str]:
    """替换 ``## <section_name>`` 段落正文，保持原有分隔空行风格。"""
    pattern = rf"(## {re.escape(section_name)}\s*\n)(.*?)(?=\n## |\Z)"
    m = re.search(pattern, content, re.DOTALL)
    if not m:
        return None
    return content[: m.start(2)] + new_body + content[m.end(2) :]


def sync_subline_curve(
    project_dir: str | Path, subline_id: str, start: int, end: int
) -> Optional[str]:
    """把单条支线的压力曲线区间同步到 ``[start, end]``（原子写）。

    Args:
        project_dir: 小说项目目录。
        subline_id: 支线 ID（目录名），如 ``S01_宗门改革``。
        start: 该支线在全书的新起始章号。
        end: 该支线在全书的新结束章号。

    Returns:
        成功返回说明字符串；无曲线表或无需变更返回 None；失败抛异常（由调用方降级）。
    """
    path = Path(project_dir) / "sublines" / subline_id / "subline.md"
    if not path.exists():
        return None
    content = path.read_text(encoding="utf-8")
    section = _extract(content, CURVE_SECTION)
    if not section:
        return None
    new_section = rebuild_curve_section(section, start, end)
    if new_section is None or new_section == section:
        return None
    new_content = _replace_section(content, CURVE_SECTION, new_section)
    if new_content is None or new_content == content:
        return None
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(new_content, encoding="utf-8")
    tmp.replace(path)
    return f"{subline_id} 压力曲线同步为 {start}-{end}"


def sync_pressure_curves(
    project_dir: str | Path, share: dict[str, int], sublines: Iterable[str]
) -> list[str]:
    """按分线预算把全部支线的压力曲线同步为累计区间。

    ``share`` 是**单支线份额**，需换算成全书累计区间后写入（与
    ``MainlineOrchestrator._cap_for`` 的累计口径一致）。

    Args:
        project_dir: 小说项目目录。
        share: 支线 ID → 该支线章数（份额）。
        sublines: 支线有序列表（S01→S0n），决定区间先后。

    Returns:
        实际发生的变更说明列表（空 = 无变更）。单条失败不影响其余（逐个 try）。
    """
    notes: list[str] = []
    cursor = 1
    for sid in sublines:
        n = int(share.get(sid) or 0)
        if n <= 0:
            continue
        start, end = cursor, cursor + n - 1
        cursor = end + 1
        try:
            note = sync_subline_curve(project_dir, sid, start, end)
        except Exception as e:  # noqa: BLE001 - 单条失败不影响其余支线
            try:
                from agent.core.infra.degrade import degrade

                degrade(
                    "subline_curve.sync",
                    f"{sid} 压力曲线同步失败，该支线沿用旧曲线（其余支线继续）",
                    e,
                )
            except Exception:  # noqa: BLE001 - degrade 自身失败不得阻断同步循环
                pass  # noqa: SILENT_DEGRADE
            notes.append(f"{sid} 压力曲线同步失败：{e}")
            continue
        if note:
            notes.append(note)
    return notes


def _extract(content: str, section_name: str) -> str:
    """与 ``workflows/pipeline/mainline._extract_section`` 同逻辑（避免跨层依赖）。"""
    m = re.search(rf"## {re.escape(section_name)}\s*\n(.*?)(?=\n## |\Z)", content, re.DOTALL)
    return m.group(1).strip() if m else ""
