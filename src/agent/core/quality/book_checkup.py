"""全书体检（Book Checkup · 纯规则离线指标）

单章质检（事实卡 / beat_ban / D 维审查）都只有"本章 + 相邻 2 章"视野，查不出
跨章结构性问题——2026-09-13《灵荒炉火》读者差评复盘（项目文档/优化/
20260913_灵荒炉火差评复盘.md）实证：35 章 pressure_stage 全为"铺垫"、配角 35 章
零成长、章末钩子句式全书重复，单章质检全绿。

本模块提供全书视野的确定性体检指标，零 LLM 成本：

  1. pressure_stage 连续同值章数（铺垫超长 = 钩子不交割的结构性风险）
  2. 伏笔账龄（"已埋"超过预期回收点 / "未埋"但埋设位置已过 = 悬念期货逾期）
  3. 配角连续出场无成长风险（连续 N 章被提及，工具人风险）
  4. 境界推进速率（突破事件间隔 = 战力停滞检测）
  5. 章末钩子句式重复度（近重复结尾聚类 = 读者可归纳的公式化钩子）
  6. 章节字数分布（抖动与超短章）

只读：绝不修改任何项目文件。失败显性化——单项指标解析失败会记入
``degraded``，不会被解读为通过。
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

#: pressure_stage 连续同值章数上限（超过即告警）
DEFAULT_STAGE_STREAK_LIMIT = 8
#: 配角连续出场章数上限（超过即工具人风险告警）
DEFAULT_CHAR_STREAK_LIMIT = 10
#: 章末钩子近重复判定相似度阈值
DEFAULT_HOOK_SIMILARITY = 0.85
#: 伏笔账龄宽限章数（预期回收点之后仍可容忍的章数）
DEFAULT_FORESHADOW_GRACE = 10
#: 超短章阈值（正文字符数）
DEFAULT_MIN_CHAPTER_CHARS = 1500

#: 突破/进阶事件关键词（确定性匹配，不做语义判断）
_ADVANCE_RE = re.compile(
    r"(突破到|突破至|晋入|晋升为|晋升|踏入|进阶|结丹成功|筑基成功|凝聚金丹|突破瓶颈)"
)

_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.S)
_CH_NUM_RE = re.compile(r"ch(\d+)")
_CH_FIELD_RE = re.compile(r"ch(\d+)")
_ROW_SPLIT_RE = re.compile(r"\s*\|\s*")


def _load_chapters(project_dir: Path) -> list[dict[str, Any]]:
    """按章号升序加载章节 frontmatter 元数据与正文。

    返回项含 chapter / pressure_stage / route_node / word_count / body /
    last_line / path；frontmatter 缺失或解析失败的章节以 ``meta_error``
    标记进入结果，显性化而非静默跳过。
    """
    chapters: list[dict[str, Any]] = []
    chapters_dir = project_dir / "chapters"
    if not chapters_dir.is_dir():
        return chapters
    for path in sorted(chapters_dir.glob("ch*.md")):
        raw = path.read_text(encoding="utf-8", errors="replace")
        m = _FRONTMATTER_RE.match(raw)
        num = _CH_NUM_RE.search(path.stem)
        item: dict[str, Any] = {
            "chapter": int(num.group(1)) if num else 0,
            "path": path.name,
            "pressure_stage": None,
            "route_node": None,
            "word_count": None,
            "meta_error": None,
            "body": "",
        }
        if m:
            try:
                import frontmatter as fm_mod

                post = fm_mod.loads(raw)
                item["pressure_stage"] = post.metadata.get("pressure_stage")
                item["route_node"] = post.metadata.get("route_node")
                item["word_count"] = post.metadata.get("word_count")
                item["body"] = (post.content or "").strip()
            except Exception as e:  # noqa: SILENT_DEGRADE - 异常记入 meta_error/degraded 显性上报，非静默吞掉
                item["meta_error"] = f"frontmatter 解析失败: {e}"
        else:
            item["meta_error"] = "缺少 frontmatter"
            item["body"] = raw.strip()
        chapters.append(item)
    chapters.sort(key=lambda c: c["chapter"])
    return chapters


def _longest_streaks(values: list[Any]) -> list[dict[str, Any]]:
    """返回所有"连续同值区间"（value / start / end / length），按长度降序。"""
    streaks: list[dict[str, Any]] = []
    i = 0
    n = len(values)
    while i < n:
        j = i
        while j + 1 < n and values[j + 1] == values[i]:
            j += 1
        streaks.append(
            {
                "value": values[i],
                "start": i,
                "end": j,
                "length": j - i + 1,
            }
        )
        i = j + 1
    streaks.sort(key=lambda s: -s["length"])
    return streaks


def check_stage_streak(
    chapters: list[dict[str, Any]], limit: int
) -> dict[str, Any]:
    """指标 1：pressure_stage 连续同值章数。"""
    stages = [c.get("pressure_stage") for c in chapters]
    streaks = [s for s in _longest_streaks(stages) if s["length"] > limit]
    return {
        "metric": "stage_streak",
        "label": "pressure_stage 连续同值",
        "limit": limit,
        "violations": [
            {
                "value": s["value"] or "（空）",
                "chapters": f"ch{chapters[s['start']]['chapter']:03d}"
                f"-ch{chapters[s['end']]['chapter']:03d}",
                "length": s["length"],
            }
            for s in streaks
        ],
    }


def check_foreshadow_aging(
    project_dir: Path, chapters: list[dict[str, Any]], grace: int
) -> dict[str, Any]:
    """指标 2：伏笔账龄。

    - 状态"已埋"且当前章数已越过预期回收点 + 宽限 → 逾期未兑付；
    - 状态"未埋"但登记的埋设位置章号 <= 当前章数 → 该埋未埋（登记与执行脱节）。
    """
    current_chapter = chapters[-1]["chapter"] if chapters else 0
    overdue: list[dict[str, str]] = []
    unburied: list[dict[str, str]] = []
    parse_errors: list[str] = []

    foreshadow_file = project_dir / "foreshadows.md"
    if not foreshadow_file.is_file():
        return {
            "metric": "foreshadow_aging",
            "label": "伏笔账龄",
            "current_chapter": current_chapter,
            "grace": grace,
            "overdue": [],
            "unburied": [],
            "parse_errors": ["foreshadows.md 不存在"],
        }
    for line in foreshadow_file.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line.startswith("|") or "---" in line:
            continue
        cells = [c.strip() for c in _ROW_SPLIT_RE.split(line.strip("|"))]
        if len(cells) < 5 or cells[0] in ("ID", ""):
            continue
        fid, status = cells[0], cells[4]
        expect_m = _CH_FIELD_RE.search(cells[3] or "")
        bury_m = _CH_FIELD_RE.search(cells[2] or "")
        if expect_m is None and bury_m is None:
            parse_errors.append(f"{fid}: 无法从登记行解析章号（{line[:60]}…）")
            continue
        if status == "已埋" and expect_m is not None:
            expected = int(expect_m.group(1))
            if current_chapter > expected + grace:
                overdue.append(
                    {
                        "id": fid,
                        "expected": f"ch{expected:03d}",
                        "detail": f"已到 ch{current_chapter:03d} 仍未回收（宽限 {grace} 章）",
                    }
                )
        elif status == "未埋" and bury_m is not None:
            planned = int(bury_m.group(1))
            if current_chapter >= planned:
                unburied.append(
                    {
                        "id": fid,
                        "planned": f"ch{planned:03d}",
                        "detail": f"已到 ch{current_chapter:03d} 仍为未埋",
                    }
                )
    return {
        "metric": "foreshadow_aging",
        "label": "伏笔账龄",
        "current_chapter": current_chapter,
        "grace": grace,
        "overdue": overdue,
        "unburied": unburied,
        "parse_errors": parse_errors,
    }


def _load_character_names(project_dir: Path) -> list[tuple[str, str]]:
    """从 characters/*.md 取 (角色名, role)；role 取自 frontmatter，缺省 "support"。

    缺 frontmatter 的角色档案按配角处理（显性化交给 meta 检查，不在此处报错）。
    """
    chars_dir = project_dir / "characters"
    if not chars_dir.is_dir():
        return []
    result: list[tuple[str, str]] = []
    for p in sorted(chars_dir.glob("*.md")):
        role = "support"
        m = _FRONTMATTER_RE.match(p.read_text(encoding="utf-8", errors="replace"))
        if m and re.search(r"^\s*role\s*:\s*[\"']?protagonist", m.group(1), re.M):
            role = "protagonist"
        result.append((p.stem, role))
    return result


def check_character_stagnation(
    project_dir: Path, chapters: list[dict[str, Any]], limit: int
) -> dict[str, Any]:
    """指标 3：配角连续出场章数（工具人风险）。

    以"角色名在正文中连续被提及的章数"为代理指标——纯规则无法判断"成长"，
    但超长连续出场叠加零状态变化登记，即差评中的"外置扬声器"画像。
    主角（frontmatter ``role: protagonist``）不在本指标范围。
    """
    names = _load_character_names(project_dir)
    streaks: list[dict[str, Any]] = []
    for name, role in names:
        if role == "protagonist":
            continue
        present = [bool(name and name in c["body"]) for c in chapters]
        for s in _longest_streaks(present):
            if s["value"] and s["length"] >= limit:
                streaks.append(
                    {
                        "character": name,
                        "chapters": f"ch{chapters[s['start']]['chapter']:03d}"
                        f"-ch{chapters[s['end']]['chapter']:03d}",
                        "length": s["length"],
                    }
                )
    streaks.sort(key=lambda s: -s["length"])
    return {
        "metric": "character_stagnation",
        "label": "配角连续出场",
        "limit": limit,
        "characters": len(names),
        "violations": streaks,
    }


def check_realm_progression(chapters: list[dict[str, Any]]) -> dict[str, Any]:
    """指标 4：境界推进速率。

    以突破/进阶关键词的章号序列为事件流，报告最近一次突破距最新章的间隔。
    间隔过长 = 战力停滞（读者弃书点之一）。
    """
    events: list[dict[str, Any]] = []
    for c in chapters:
        hits = _ADVANCE_RE.findall(c["body"])
        if hits:
            events.append({"chapter": c["chapter"], "keywords": sorted(set(hits))})
    current_chapter = chapters[-1]["chapter"] if chapters else 0
    since_last = None
    if events:
        since_last = current_chapter - events[-1]["chapter"]
    total = len(chapters)
    return {
        "metric": "realm_progression",
        "label": "境界推进",
        "current_chapter": current_chapter,
        "advance_events": events,
        "advance_count": len(events),
        "chapters_per_advance": round(total / len(events), 1) if events else None,
        "chapters_since_last_advance": since_last,
    }


def _normalize_line(line: str) -> str:
    """归一化章末句：去标点空白，保留实词以比较句式骨架。"""
    return re.sub("[，。！？…、；：“”‘’《》\\s,.!?;:~\\-—]+", "", line)


def check_ending_hooks(
    chapters: list[dict[str, Any]], similarity: float
) -> dict[str, Any]:
    """指标 5：章末钩子句式近重复聚类。

    每章取正文最后一个非空句段（钩子位），两两相似度 ≥ 阈值即归入同簇——
    差评实证"真正的风暴才刚刚开始"式钩子被读者归纳为公式。
    """
    endings: list[dict[str, Any]] = []
    for c in chapters:
        lines = [ln.strip() for ln in c["body"].splitlines() if ln.strip()]
        if not lines:
            continue
        raw = lines[-1]
        norm = _normalize_line(raw)
        if len(norm) < 4:
            continue
        endings.append({"chapter": c["chapter"], "raw": raw, "norm": norm})

    clusters: list[dict[str, Any]] = []
    assigned = [False] * len(endings)
    for i, e in enumerate(endings):
        if assigned[i]:
            continue
        group = [e]
        assigned[i] = True
        for j in range(i + 1, len(endings)):
            if assigned[j]:
                continue
            ratio = SequenceMatcher(None, e["norm"], endings[j]["norm"]).ratio()
            if ratio >= similarity:
                group.append(endings[j])
                assigned[j] = True
        if len(group) >= 3:
            clusters.append(
                {
                    "sample": group[0]["raw"][:40],
                    "count": len(group),
                    "chapters": [g["chapter"] for g in group][:20],
                }
            )
    clusters.sort(key=lambda c: -c["count"])
    return {
        "metric": "ending_hooks",
        "label": "章末钩子句式重复",
        "similarity": similarity,
        "endings_sampled": len(endings),
        "clusters": clusters,
    }


def check_word_count(
    chapters: list[dict[str, Any]], min_chars: int
) -> dict[str, Any]:
    """指标 6：章节字数分布（以正文实际字符数为准，不信 frontmatter 声明值）。"""
    sizes = [len(re.sub(r"\s", "", c["body"])) for c in chapters]
    if not sizes:
        return {
            "metric": "word_count",
            "label": "章节字数分布",
            "count": 0,
            "undersized": [],
        }
    mean = sum(sizes) / len(sizes)
    undersized = [
        {
            "chapter": c["chapter"],
            "chars": s,
            "detail": f"低于阈值 {min_chars}（均值 {int(mean)}）",
        }
        for c, s in zip(chapters, sizes)
        if s < min_chars
    ]
    return {
        "metric": "word_count",
        "label": "章节字数分布",
        "count": len(sizes),
        "min": min(sizes),
        "max": max(sizes),
        "mean": int(mean),
        "min_threshold": min_chars,
        "undersized": undersized,
    }


def run_book_checkup(
    project_dir: Path,
    *,
    stage_streak_limit: int = DEFAULT_STAGE_STREAK_LIMIT,
    char_streak_limit: int = DEFAULT_CHAR_STREAK_LIMIT,
    hook_similarity: float = DEFAULT_HOOK_SIMILARITY,
    foreshadow_grace: int = DEFAULT_FORESHADOW_GRACE,
    min_chapter_chars: int = DEFAULT_MIN_CHAPTER_CHARS,
) -> dict[str, Any]:
    """执行全书体检，返回六个指标的报告与汇总判定。

    汇总语义遵循"失败显性化"红线：任一指标存在违规即 ``passed=False``，
    解析失败计入 ``degraded`` 而非通过。
    """
    project_dir = Path(project_dir)
    chapters = _load_chapters(project_dir)
    if not chapters:
        return {
            "success": False,
            "error": {"code": "no_chapters", "message": f"{project_dir}/chapters 下没有章节文件"},
        }

    metrics: list[dict[str, Any]] = [
        check_stage_streak(chapters, stage_streak_limit),
        check_foreshadow_aging(project_dir, chapters, foreshadow_grace),
        check_character_stagnation(project_dir, chapters, char_streak_limit),
        check_realm_progression(chapters),
        check_ending_hooks(chapters, hook_similarity),
        check_word_count(chapters, min_chapter_chars),
    ]

    degraded: list[str] = []
    for c in chapters:
        if c.get("meta_error"):
            degraded.append(f"ch{c['chapter']:03d}: {c['meta_error']}")
    fo = next(m for m in metrics if m["metric"] == "foreshadow_aging")
    degraded.extend(fo["parse_errors"])

    issues: list[dict[str, str]] = []
    for m in metrics:
        if m["metric"] == "stage_streak":
            issues += [{"metric": m["metric"], "detail": f"{v['chapters']} 连续 {v['length']} 章「{v['value']}」"} for v in m["violations"]]
        elif m["metric"] == "foreshadow_aging":
            issues += [{"metric": m["metric"], "detail": f"{v['id']} 预期 {v['expected']}，{v['detail']}"} for v in m["overdue"]]
            issues += [{"metric": m["metric"], "detail": f"{v['id']} 计划 {v['planned']}，{v['detail']}"} for v in m["unburied"]]
        elif m["metric"] == "character_stagnation":
            issues += [{"metric": m["metric"], "detail": f"{v['character']} 在 {v['chapters']} 连续出场 {v['length']} 章"} for v in m["violations"]]
        elif m["metric"] == "realm_progression":
            gap = m["chapters_since_last_advance"]
            if gap is not None and gap >= 20 and m["advance_count"] <= 1:
                issues.append(
                    {
                        "metric": m["metric"],
                        "detail": f"全书 {m['current_chapter']} 章仅 {m['advance_count']} 次突破事件，最近一次距今 {gap} 章",
                    }
                )
        elif m["metric"] == "ending_hooks":
            issues += [
                {
                    "metric": m["metric"],
                    "detail": f"「{c['sample']}…」句式在 {c['count']} 个章末重复出现",
                }
                for c in m["clusters"]
            ]
        elif m["metric"] == "word_count":
            issues += [{"metric": m["metric"], "detail": f"ch{v['chapter']:03d} 正文 {v['chars']} 字，{v['detail']}"} for v in m["undersized"]]

    return {
        "success": True,
        "project": str(project_dir),
        "chapter_count": len(chapters),
        "first_chapter": chapters[0]["chapter"],
        "last_chapter": chapters[-1]["chapter"],
        "metrics": metrics,
        "issues": issues,
        "degraded": degraded,
        "passed": not issues and not degraded,
    }
