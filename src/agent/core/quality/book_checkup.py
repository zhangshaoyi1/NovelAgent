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
  7. 章尾套话短语命中（风暴预告/倒计时式固定清单，与 5 互补抓措辞变体）
  8. 实体漂移（宗派/组织名出现于正典之外——灵渊宗/玄天宗类硬伤）

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

#: 章尾套话短语清单（差评实证：三本书 101 个章尾命中"风暴/倒计时"式预告）。
#: 相似度聚类抓不到措辞变体（"真正的风暴，才刚刚开始" vs "风暴正在酝酿"），
#: 固定短语清单是必要补充。命中位置限正文最后 N 行（章末钩子位）。
ENDING_CLICHE_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p)
    for p in (
        r"风暴.{0,6}(才刚刚开始|即将来临|正在酝酿|将至|将至未至|正在逼近)|真正的风暴",
        r"倒计时|还剩[一二三四五六七八九十\d]+天|距离.{0,8}(十五|期限|之约).{0,6}(还有|只剩)",
        r"(一切|好戏|故事)(才)?(刚刚|才)开始",
        r"握紧了拳头|目光(变得)?(更加)?(锐利|坚定)起来",
    )
)
#: 套话检测窗口：正文最后 N 个非空行
ENDING_CLICHE_TAIL_LINES = 5

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


def check_ending_cliche(chapters: list[dict[str, Any]]) -> dict[str, Any]:
    """指标 7：章尾套话短语命中（ENDING_CLICHE_PATTERNS 清单匹配）。

    检测窗口为正文最后 ``ENDING_CLICHE_TAIL_LINES`` 个非空行（章末钩子位）。
    与指标 5 的相似度聚类互补：聚类抓同构句式，清单抓措辞变体。
    """
    hits: list[dict[str, Any]] = []
    for c in chapters:
        lines = [ln.strip() for ln in c["body"].splitlines() if ln.strip()]
        tail = "\n".join(lines[-ENDING_CLICHE_TAIL_LINES:])
        matched = [p.pattern for p in ENDING_CLICHE_PATTERNS if p.search(tail)]
        if matched:
            hits.append({"chapter": c["chapter"], "patterns": matched, "sample": lines[-1][:40]})
    return {
        "metric": "ending_cliche",
        "label": "章尾套话",
        "tail_lines": ENDING_CLICHE_TAIL_LINES,
        "hit_count": len(hits),
        "hits": hits,
    }


# ---------------------------------------------------------------------------
# 指标 8：实体漂移（T1 的全书确定性子集）
# 2026-09-13 灵荒炉火《全维度点评》实证：ch031/032 连续冒出设定外宗门
# 「灵渊宗」6 处（一句内与天剑宗并存），沈长风中途改名沈清舟——
# 单章事实卡查不到（实体不在登记簿，无"对照物"），必须全书视野反查。
# 本指标只做高置信子集：宗派/组织名（X宗/X殿/X盟）。
#
# 算法（对称尾式抑制，2026-09-13 在三本小说上实测调参）：
#   1. 对正典（world/route/foreshadows/relations/sublines，刻意不含
#      characters/——角色档案可能被漂移本身污染）做同样的后缀锚定提取，
#      并把每个命中形式的所有后缀锚定短尾并入已知集（"与仙宗"贡献
#      "仙宗"，从而抑制正文"告诉仙宗/冲向仙宗"这类动宾污染）；
#   2. 正文候选用同一正则提取，经首字噪声剥离（"于天剑宗"→"天剑宗"）、
#      名部污染检查（"藏经阁偏殿"/"简和卷宗"）、泛称表过滤后，
#      任一尾式命中已知集即抑制，否则计数；
#   3. 同一候选出现 ≥3 章才上报（切句噪声）。
# 局限（显性声明）：人名改名漂移（沈清舟类）无法用后缀法覆盖，待 T1 完整版。
# ---------------------------------------------------------------------------
_ORG_CAND_RE = re.compile(r"[\u4e00-\u9fa5]{1,3}(?:宗|殿|盟)")
#: 实体名部（后缀前的字）出现这些字符即弃：后缀字重叠（"藏经阁偏殿"）或动宾/虚词污染（"简和卷宗"）
_ORG_NAME_CONTAMINATION = set("宗殿盟阁府派教堂楼坊会门入出回离来去在是了之的和")
#: 常见泛指词（非专名）
_ORG_GENERIC = frozenset(
    {"宗门", "本宗", "山门", "掌门", "魔宗", "正宗", "同门", "门派", "宗派",
     "殿下", "宫殿", "殿后", "联盟", "盟友", "仙宗", "宗主", "外门", "内门",
     "护宗", "大宗", "入宗", "凡骨"}
)
#: 候选首字噪声，逐字剥离（"于天剑宗"→"天剑宗"、"是青云宗"→"青云宗"）
_ORG_LEADING_NOISE = set(
    "一二几整全每了在去到从向把被和与跟或但就说等对着往回进出让个这那有没不"
    "还又也都而便才刚正想看着走得地之他她它你谁的于即将欲份各诸众是最远近垂"
)


def _org_tail_forms(form: str) -> set[str]:
    """后缀锚定短尾：'与仙宗' → {'与仙宗', '仙宗'}（含自身）。"""
    return {form[i:] for i in range(len(form) - 1)}


def _load_org_canon(project_dir: Path) -> str:
    """汇总组织名正典来源文本（不含 characters/，防漂移自证）。"""
    sources: list[Path] = [
        project_dir / "world.md",
        project_dir / "protagonist_route.md",
        project_dir / "foreshadows.md",
        project_dir / "relations" / "graph.md",
        *sorted(project_dir.glob("sublines/*/subline.md")),
    ]
    return "".join(
        p.read_text(encoding="utf-8", errors="replace") for p in sources if p.is_file()
    )


def check_entity_drift(
    project_dir: Path, chapters: list[dict[str, Any]], min_count: int = 3
) -> dict[str, Any]:
    """指标 8：实体漂移——正文出现正典之外的宗派/组织名。

    ``min_count`` 为同一候选的最低出现次数（<3 视为切句噪声）。已知集由
    正典做对称尾式展开（见模块 docstring）；人名漂移不在本指标范围。
    """
    canon = _load_org_canon(project_dir)
    known = set(_ORG_GENERIC)
    for form in _ORG_CAND_RE.findall(canon):
        known |= _org_tail_forms(form)

    occurrences: dict[str, list[str]] = {}
    for c in chapters:
        for m in _ORG_CAND_RE.finditer(c["body"]):
            w = m.group(0)
            if len(w) < 3:  # 名部 1 字的短形（"仙宗"）由泛称表/尾式兜底
                continue
            core = w
            while len(core) > 2 and core[0] in _ORG_LEADING_NOISE:
                core = core[1:]
            if len(core) < 3:  # 剥完只剩后缀 → 泛指
                continue
            name_part = core[:-1]
            if any(ch in _ORG_NAME_CONTAMINATION for ch in name_part):
                continue
            if any(t in known for t in _org_tail_forms(core)):
                continue
            occurrences.setdefault(core, []).append(c["path"])

    unknown: list[dict[str, Any]] = []
    for core, files in occurrences.items():
        if len(files) < min_count:
            continue
        unknown.append(
            {
                "entity": core,
                "count": len(files),
                "first_seen": min(files),
                "chapters": sorted(set(files))[:10],
            }
        )
    unknown.sort(key=lambda x: -x["count"])
    return {
        "metric": "entity_drift",
        "label": "实体漂移（宗派/组织）",
        "unknown": unknown,
    }


# ---------------------------------------------------------------------------
# 指标 9/10：人名漂移（T1 离线子集二，2026-09-13 灵荒炉火点评实证）
# 案例细节：楚寒烟的师父前半书叫「沈长风」（characters/ 已注册），ch031 起
# 同一人物无任何交代地变成「沈清舟」——注册角色绝迹 + 同姓新名接棒，是
# 改名漂移的确定性指纹（rename handoff）；另有大量 recurring 配角从未注册
# （周长老/王执事/苏清雪/赵铁/孙小豆），对话归属语是高置信人名信号。
# 局限（显性声明）：归属语/姓氏窗均为窄口径，漏检可能，但报出即可信。
# ---------------------------------------------------------------------------
_ATTR_VERB_RE = re.compile(
    r"(?:说道|说[：:，。」]|开口[道说]?|问道|冷笑[道]?|低声道|沉声道|喝道|笑道"
    r"|叹道|答道|喃喃|追问|喊道|骂道|嘀咕)"
)
#: 说话人候选尾字/内含字噪声（副词化动词尾、代词等）
_SPEAKER_TAIL_NOISE = set("声地然于着了续是再别想要去来过得头")
_SPEAKER_CONTAIN_NOISE = set("你别是否或者虽然居然竟然既然")
_SPEAKER_GENERIC = frozenset(
    {"老者", "年轻人", "执事", "黑衣人", "黑袍人", "灰袍人", "黑影", "那人",
     "有人", "他们", "她们", "众人", "长老", "管事", "少女", "男子", "女子",
     "少年", "老人", "孩子", "中年", "青年", "自己", "个声音", "的声音",
     "弟子", "散修", "修士", "太监", "宫女"}
)


def _iter_chapter_bodies(chapters: list[dict[str, Any]]):
    """yield (chapter_no, body)。"""
    for c in chapters:
        yield c["chapter"], c["body"]


def check_rename_drift(
    project_dir: Path, chapters: list[dict[str, Any]], min_chapters: int = 3
) -> dict[str, Any]:
    """指标 9：改名漂移——注册角色绝迹后同姓新名接棒（rename handoff）。

    对每个注册角色 R（姓 = 首字），扫描正文「姓+1~2 字」且 ≠ R 的 token：
    若该 token 覆盖 ≥min_chapters 章且其首章晚于 R 的末章（-2 宽限），
    判定疑似中途改名。右边界约束（token 后紧跟标点/的/说/道等）压制
    「林凡站」式名动粘连。
    """
    chars_dir = project_dir / "characters"
    registered = [p.stem for p in chars_dir.glob("*.md")] if chars_dir.is_dir() else []
    canon = _load_org_canon(project_dir)
    #: 称谓词：token 含这些字是头衔/称呼（"沈师父/沈执事"），不是名字漂移
    _APPELLATION = "长老执事师父师尊师兄师姐师叔师伯公子姑娘兄弟大哥大姐先生大人夫人小姐掌柜管事阁下大人老"
    _RIGHT_BOUND = (
        r"(?=[$，。！？；：、""''\s]|$|的|说|道|在|也|都|是|和|与|没|不|却|便)"
    )
    suspects: list[dict[str, Any]] = []
    for reg_name in registered:
        if len(reg_name) < 2:
            continue
        surname = reg_name[0]
        # R 的逐章出现（含正文任意位置）
        present = {ch: bool(reg_name in body) for ch, body in _iter_chapter_bodies(chapters)}
        chapters_with_r = [ch for ch, ok in present.items() if ok]
        if not chapters_with_r:
            continue
        last_r = max(chapters_with_r)
        # 同姓候选 token（右边界约束）
        token_chapters: dict[str, set[int]] = {}
        for ch, body in _iter_chapter_bodies(chapters):
            for m in re.finditer(surname + r"[\u4e00-\u9fa5]{1,2}" + _RIGHT_BOUND, body):
                token = m.group(0)
                if token == reg_name:
                    continue
                if any(app in token for app in _APPELLATION):
                    continue
                token_chapters.setdefault(token, set()).add(ch)
        for token, chs in token_chapters.items():
            if len(chs) < min_chapters:
                continue
            first_t = min(chs)
            if first_t > last_r - 2:  # 接棒：注册名绝迹后新名才出现
                # 注意：不因别名已在 world.md 而抑制——灵荒炉火实证 world.md
                # 可能被漂移本身污染（沈清舟已写入正典），两名并行正是要报的
                suspects.append(
                    {
                        "registered": reg_name,
                        "alias": token,
                        "first_chapter": first_t,
                        "alias_chapters": sorted(chs)[:10],
                        "last_registered_chapter": last_r,
                        "alias_in_canon": token in canon,
                        "detail": f"「{reg_name}」末见于 ch{last_r:03d}，「{token}」自 ch{first_t:03d} 接棒出现 {len(chs)} 章——疑似中途改名未交代"
                        + ("（⚠ 别名已渗入正典，正典与角色册两名并行）" if token in canon else ""),
                    }
                )
    suspects.sort(key=lambda s: s["alias_chapters"][0])
    return {
        "metric": "rename_drift",
        "label": "改名漂移",
        "registered": len(registered),
        "suspects": suspects,
    }


def check_speaker_registry(
    project_dir: Path, chapters: list[dict[str, Any]], min_chapters: int = 2
) -> dict[str, Any]:
    """指标 10：未注册说话人——对话归属语抽取的 recurring 角色。

    「……」X 说/道/开口 中的 X 是高置信人名信号（无需分词）。跨
    ≥min_chapters 章出现却不在 characters/ 登记簿与正典中的说话人 =
    配角漏登记（差评实证：周长老/王执事/苏清雪/赵铁/孙小豆）。
    """
    chars_dir = project_dir / "characters"
    registered = {p.stem for p in chars_dir.glob("*.md")} if chars_dir.is_dir() else set()
    canon = _load_org_canon(project_dir)
    # 正典中出现过的归属名也算已知（relations 里的周长老等）
    known = registered | set(
        re.findall(r"[\u4e00-\u9fa5]{2,3}(?=" + _ATTR_VERB_RE.pattern + r")", canon)
    )
    speakers: dict[str, set[int]] = {}
    for ch, body in _iter_chapter_bodies(chapters):
        for m in re.finditer(r"([\u4e00-\u9fa5]{2,3})(?=" + _ATTR_VERB_RE.pattern + r")", body):
            n = m.group(1)
            while len(n) > 2 and n[0] in "着他她它那这或就还但和与跟对把被让等又便才只却都已然突倏随继接竟":
                n = n[1:]
            if len(n) < 2 or n in known or n in _SPEAKER_GENERIC:
                continue
            if n[-1] in _SPEAKER_TAIL_NOISE or any(z in n for z in _SPEAKER_CONTAIN_NOISE):
                continue
            speakers.setdefault(n, set()).add(ch)
    unregistered = [
        {
            "speaker": n,
            "chapters": sorted(c)[:10],
            "detail": f"「{n}」在 {len(c)} 章有对话归属但未登记 characters/",
        }
        for n, c in sorted(speakers.items(), key=lambda x: -len(x[1]))
        if len(c) >= min_chapters
    ]
    return {
        "metric": "speaker_registry",
        "label": "未注册说话人",
        "registered": len(registered),
        "unregistered": unregistered,
    }


def write_time_entity_check(
    project_dir: Path, chapter_num: int, chapter_text: str
) -> dict[str, Any]:
    """写时实体/人名一致性检查（T1 写时化，供出章门禁调用）。

    以磁盘上已发布章节 + **待检新章** 组成临时全书，重跑三个确定性指标：
      - 实体漂移：正典外组织名在**本章首次出现** → blocking（灵渊宗类硬伤
        在诞生那一刻拦住；此前章节已存在的漂移本章改不动 → warning）；
      - 改名漂移：接棒嫌疑的别名章落在**本章** → blocking（沈清舟类）；
      - 未注册说话人：本章新出现的归属名 → warning（配角可合法暂缓注册，
        但显性提醒，连续多章出现即应由规划层补档）。

    纯确定性、零 LLM。异常由调用方按 degrade() 惯例处理。
    """
    project_dir = Path(project_dir)
    chapters = _load_chapters(project_dir)
    chapters = [c for c in chapters if c["chapter"] != chapter_num]
    current = {
        "chapter": chapter_num,
        "path": f"ch{chapter_num:03d}.md(current)",
        "pressure_stage": None,
        "route_node": None,
        "word_count": None,
        "meta_error": None,
        "body": chapter_text,
    }
    full = chapters + [current]

    blocking: list[str] = []
    warnings: list[str] = []

    ed = check_entity_drift(project_dir, full, min_count=2)
    for u in ed["unknown"]:
        if u.get("first_seen", "").endswith("(current)"):
            blocking.append(
                f"设定外组织名「{u['entity']}」在本章首次出现，"
                "不在 world/route/sublines 正典中——若为新设定请先登记 world.md，"
                "否则改用既有宗门/组织名（灵渊宗/玄天宗类硬伤）。"
            )
        elif u["entity"] in chapter_text:  # 只对本章实际沿用的漂移报 warning
            warnings.append(
                f"组织名「{u['entity']}」沿用了此前章节的漂移用法（累计 "
                f"{u['count']} 次，最早见 {u['first_seen']}），建议在全书体检中定位首次漂移章修订。"
            )

    rd = check_rename_drift(project_dir, full, min_chapters=1)
    prev_chapters = {c["chapter"] for c in chapters}
    for s in rd["suspects"]:
        # 接棒语义收紧（写时）：
        #   ① 别名首现章 == 本章（历史章里从未出现过，才有"就在此刻接棒"可言）；
        #   ② 注册名在本章正文中确已绝迹（"林寻伏在…"这类名动粘连不算——
        #      注册名仍在场就不是改名）；
        #   ③ 必须存在历史章（全书首章无历史，不存在接棒）。
        if s.get("first_chapter") != chapter_num:
            continue
        if not prev_chapters:
            continue
        if s["registered"] in chapter_text:
            continue
        blocking.append(
            s["detail"] + " 若确需改名，必须先更新 characters/*.md 与 world.md 再写。"
        )

    sr = check_speaker_registry(project_dir, full, min_chapters=1)
    for u in sr["unregistered"]:
        if chapter_num in u["chapters"]:
            warnings.append(
                f"说话人「{u['speaker']}」未登记 characters/（已出现于 {len(u['chapters'])} 章）——"
                "recurring 配角应补档，避免工具人化与名实漂移。"
            )

    return {"blocking": blocking, "warnings": warnings}


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
        check_ending_cliche(chapters),
        check_entity_drift(project_dir, chapters),
        check_rename_drift(project_dir, chapters),
        check_speaker_registry(project_dir, chapters),
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
        elif m["metric"] == "ending_cliche":
            if m["hit_count"]:
                issues.append(
                    {
                        "metric": m["metric"],
                        "detail": f"{m['hit_count']}/{len(chapters)} 个章末命中套话短语清单（风暴预告/倒计时/才刚刚开始式）",
                    }
                )
        elif m["metric"] == "entity_drift":
            issues += [
                {
                    "metric": m["metric"],
                    "detail": f"「{v['entity']}」出现 {v['count']} 次（{','.join(v['chapters'][:6])}），不在 world/route/sublines 正典中",
                }
                for v in m["unknown"]
            ]
        elif m["metric"] == "rename_drift":
            issues += [
                {"metric": m["metric"], "detail": v["detail"]}
                for v in m["suspects"]
            ]
        elif m["metric"] == "speaker_registry":
            issues += [
                {"metric": m["metric"], "detail": v["detail"]}
                for v in m["unregistered"][:10]
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
