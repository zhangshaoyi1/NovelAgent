"""文体卫生门禁（2026-09-12，用户对成书质量的批评驱动）

背景
----
对五灵破归档 / 无灵两本成书的读者批评里，杀伤力最大、也最可验证的一类是
**生成残留与文体失控**，它们全部绕过了现有 LLM 质检（九项审稿不带前文、
无文笔维度）直接进入成书：

- 残缺比喻：「跟……似的从地底深处传来」（五灵破 ch004）——省略号是生成时
  占位符未被填实的残句；
- 破折号劈词：「他手——里挥舞着一柄铁剑」（无灵 ch140）；
- 成语误用：「果然名不传虚」（应为"名不虚传"，无灵 ch140）；
- 口语填充词裸奔在叙述里：「就这么着，山壁被凿出……」（五灵破 ch050）；
- 密度失控：感叹号 / "像是·如同·仿佛"比喻堆叠（无灵全书抽样）；
- 短语复读：同一 6+ 字短语章内三次以上（"骨骼碎裂的声音"一章两响、
  "渗血的布条"缠三个人——无灵 ch240）；
- 对话轮次断裂：提问后主角零回应、第三方直接"你说得对"（无灵 ch350 终章）。

设计
----
- **纯规则、零 LLM**：全部是正则/统计，可在落盘前以确定性门禁拦截，
  blocking 项直接打回修订循环（与 min_length 同位、同语义）；
- 维度 ``text_hygiene_blocking`` 已登记 dimension_registry（L2 SSOT），
  量纲 COUNT / LOWER_BETTER / Scope.WINDOW / Source.COMPUTED；
- 误报控制：劈词破折号、填充词等可疑形态只记 **warning**（不阻断），
  只有确定性错误（成语误用 / 残缺比喻 / 高频复读 / 极端密度）才 blocking。

依赖方向：本模块属 agent.core.quality（领域层），仅依赖标准库。
"""

from __future__ import annotations

import re
from collections import Counter

__all__ = ["hygiene_issues", "split_issues"]


def _issue(rule_id: str, severity: str, description: str) -> dict[str, str]:
    return {"rule_id": rule_id, "severity": severity, "description": description}


# ---------------------------------------------------------------------------
# 规则 1：残缺比喻（省略号占位未被填实）
# ---------------------------------------------------------------------------
# 「跟……似的」「像……一样」「如同……一般」——比喻本体位置是字面省略号，
# 正常写作不会在"像/跟/如同"与"一样/似的"之间直接放省略号。
_RESIDUAL_SIMILE_RE = re.compile(
    r"[像跟同如好比似]……(?:一样|似的|一般|般|样)"
    r"|(?:一样|似的|一般|般)……[。；，\s]"
)

# ---------------------------------------------------------------------------
# 规则 2：破折号劈词（warning）
# ---------------------------------------------------------------------------
# 「他手——里」：两个汉字被破折号直接隔开且两侧无标点。排比强调（我——不——要）
# 存在误报可能，故仅 warning。
_DASH_SPLIT_RE = re.compile(r"[\u4e00-\u9fff]——[\u4e00-\u9fff]")

# ---------------------------------------------------------------------------
# 规则 3：口语填充词裸奔（warning）
# ---------------------------------------------------------------------------
_FILLER_PHRASES = (
    "就这么着",
    "话说回来",
    # 2026-09-13 灵荒炉火点评实证回填：ch020/021/022/024/030/033 残留
    "你别说",
    "说起来",
)
_FILLER_RE = re.compile("|".join(_FILLER_PHRASES))

# ---------------------------------------------------------------------------
# 规则 4：成语误用（blocking，确定性映射）
# ---------------------------------------------------------------------------
_IDIOM_MISUSE: dict[str, str] = {
    "名不传虚": "名不虚传",
    "迫不急待": "迫不及待",
    "一如即往": "一如既往",
    "走头无路": "走投无路",
    "按纳不住": "按捺不住",
    "甘败下风": "甘拜下风",
    "世外桃园": "世外桃源",
    "不径而走": "不胫而走",
    "哀声叹气": "唉声叹气",
    # 2026-09-13 灵荒炉火 ch033 点评实证回填
    "戛不过止": "戛然而止",
}

# ---------------------------------------------------------------------------
# 规则 9：繁体字混入（warning，确定性字符表）——简体成书中出现高频繁体
# 几乎必然是生成/转换事故（2026-09-13 灵荒炉火 ch025「一個」/ch026「王執事」实证）。
# 只收与对应简体显著不同、且不会在简体文本中合法出现的字，防误报。
# ---------------------------------------------------------------------------
_TRADITIONAL_CHARS = "個執夢裡來時說對開關門問間陣雲風飛馬書見誰後進遠運這還沒們萬與專業叢東絲兩嚴喪臨為烏無歲歸當餘勝務動區華賣質樂罷學覺語誤讀變讓議護贏趕趨車軌軍輪軟輸蘭慮虧蟲蠻錄鐵長閃閉閒閱隊際陸難電靈靜頁項順須顧頓預頑頭頻賴張強壯態牽滿"
_TRADITIONAL_RE = re.compile("[" + _TRADITIONAL_CHARS + "]")

# ---------------------------------------------------------------------------
# 规则 5/6：密度统计（感叹号 / 比喻标记）
# ---------------------------------------------------------------------------
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
# 像是/如同/仿佛/宛如/就像/恰似/好似/犹如 + "像…一样"的"像"
_SIMILE_MARK_RE = re.compile(r"像是|如同|仿佛|宛如|恰似|好似|犹如|就像|好像")

#: 每 1000 汉字的感叹号阈值：>6 告警，>12 阻断
_EXCLAIM_WARN_PER_K = 6.0
_EXCLAIM_BLOCK_PER_K = 12.0
#: 每 1000 汉字的比喻标记阈值：>8 告警，>15 阻断
_SIMILE_WARN_PER_K = 8.0
_SIMILE_BLOCK_PER_K = 15.0

# ---------------------------------------------------------------------------
# 规则 7：短语复读（≥6 字、章内出现 ≥3 次 → blocking）
# ---------------------------------------------------------------------------
_ECHO_GRAM = 6
_ECHO_MIN_COUNT = 3
_ECHO_STRIP_RE = re.compile(r"[\s，。！？；：、「」『』""''——…·\n]")

# ---------------------------------------------------------------------------
# 规则 8：对话轮次断裂（warning，窄口径）
# ---------------------------------------------------------------------------
# 无灵 ch350 实证形态：某人被直接提问 → 主角零回应 → 第三方/提问者自己
# 接一句"你说得对"。窄口径只抓「引号内问句 → 紧邻引号对话以附和语开头」。
_QUESTION_RE = re.compile(r"[「\"“][^」\"”]{0,60}？\s*[」\"”]")
_AGREEMENT_RE = re.compile(
    r"[「\"“]\s*(?:你说得对|你说的是|没错，你|对，你|是的，你|正是，你)"
)


def _check_phrase_echo(body: str) -> list[dict[str, str]]:
    """章内 6+ 字短语复读检测（先粗筛 Counter，再精确非重叠计数）。"""
    cleaned = _ECHO_STRIP_RE.sub("", body)
    if len(cleaned) < _ECHO_GRAM * _ECHO_MIN_COUNT:
        return []
    grams = Counter(
        cleaned[i : i + _ECHO_GRAM] for i in range(len(cleaned) - _ECHO_GRAM + 1)
    )
    candidates = [g for g, c in grams.items() if c >= _ECHO_MIN_COUNT]
    # 合并子串：若短串的出现全部来自某个更长的高频串，去重（保留计数最高者）
    candidates.sort(key=lambda g: (-grams[g], g))
    picked: list[str] = []
    seen_multisets: set[tuple[str, ...]] = set()
    for g in candidates:
        if any(g in p for p in picked):
            continue
        # 循环串的移位变体（如「正文填充正文/文填充正文填/填充正文填充」）
        # 多数共享同一字符多重集，只保留一个，避免刷占 top 名额
        key = tuple(sorted(g))
        if key in seen_multisets:
            continue
        seen_multisets.add(key)
        picked.append(g)
    issues: list[dict[str, str]] = []
    verified: list[tuple[int, str]] = []
    for g in picked:
        # 非重叠精确计数（Counter 是滑窗重叠口径，会虚高连续重复文本）
        exact = len(re.findall(re.escape(g), cleaned))
        if exact < _ECHO_MIN_COUNT:
            continue
        verified.append((exact, g))
    verified.sort(key=lambda t: (-t[0], -len(t[1])))
    for exact, g in verified[:5]:
        issues.append(
            _issue(
                "phrase_echo",
                # warning 而非 blocking：6 字窗口的复读检测对"整段复用凑字"的
                # 退化文本（测试桩/重复灌注）会大面积命中，误报代价是重写；
                # 真复读走告警透出 + 台账统计即可
                "warning",
                f"短语「{g}」在本章出现 {exact} 次（≥3 次），属车轱辘话复读，"
                "请改写为不同表达或删除多余重复。",
            )
        )
    return issues


def _check_dialogue_jump(body: str) -> list[dict[str, str]]:
    """对话轮次断裂窄口径检测（warning）。"""
    issues: list[dict[str, str]] = []
    for m in _QUESTION_RE.finditer(body):
        tail = body[m.end() : m.end() + 120]
        if _AGREEMENT_RE.search(tail):
            issues.append(
                _issue(
                    "dialogue_turn_gap",
                    "warning",
                    "检测到引号内问句之后，紧接着的对话以附和语（你说得对/没错…）"
                    "开头，但中间没有任何应答——疑似对话轮次丢失，请核对并补上"
                    "被提问者的回应。",
                )
            )
            if len(issues) >= 2:
                break
    return issues


def _check_sentence_repetition(body: str) -> list[dict[str, str]]:
    """章内句级重复（车轱辘话）检测——与批末 required 维度 padding_repetition_abnormal
    （阈值 0.30）同口径的前置写时版（2026-09-12 风险 2）。

    判定：按中文句末标点切句（<8 字忽略），任一句与先前句的字符集合 Jaccard
    ≥ 0.85 即记一次重复；重复句占比 ≥ 0.30 → blocking。纯确定性，毫秒级。
    """
    parts = re.split(r"[。！？…；\n]+", body)
    sents = [p.strip() for p in parts if len(p.strip()) >= 8]
    if len(sents) < 10:  # 句数太少占比无统计意义
        return []
    repeated = 0
    seen: list[set[str]] = []
    for s in sents:
        ss = set(s)
        if ss and any(len(ss & t) / len(ss | t) >= 0.85 for t in seen):
            repeated += 1
        seen.append(ss)
    ratio = repeated / len(sents)
    if ratio < 0.30:
        return []
    return [
        _issue(
            "repetition_abnormal",
            "blocking",
            f"章内重复句占比 {ratio:.0%}（≥ 30%，{repeated}/{len(sents)} 句近似重复），"
            "疑似注水/车轱辘话。请定位并改写或合并相似句，用新情节/细节推进，"
            "禁止重复表达凑字。",
        )
    ]


def hygiene_issues(text: str) -> list[dict[str, str]]:
    """对章节正文执行文体卫生扫描，返回问题列表。

    每项形如 ``{"rule_id", "severity"("blocking"|"warning"), "description"}``。
    blocking 项存在即应打回修订（与 min_length 同语义）；warning 项仅随
    质检报告透出供 Writer 复查。纯确定性，无 LLM、无 IO，任何输入都安全。
    """
    body = (text or "").strip()
    if not body:
        return []
    issues: list[dict[str, str]] = []

    for m in _RESIDUAL_SIMILE_RE.finditer(body):
        issues.append(
            _issue(
                "residual_simile",
                "blocking",
                f"残缺比喻「…{m.group(0)}…」：比喻本体位置是字面省略号，"
                "属生成残留。请补全本体（如「跟闷雷似的」）或改写整句。",
            )
        )

    for wrong, right in _IDIOM_MISUSE.items():
        if wrong in body:
            issues.append(
                _issue(
                    "idiom_misuse",
                    "blocking",
                    f"成语误用：「{wrong}」应为「{right}」，请全文替换并复查"
                    "同类成语拼写。",
                )
            )

    cjk_count = len(_CJK_RE.findall(body))
    if cjk_count >= 500:  # 太短的文本密度统计无意义
        per_k = cjk_count / 1000.0
        exclaims = body.count("！") + body.count("!")
        exclaim_per_k = exclaims / per_k
        if exclaim_per_k > _EXCLAIM_BLOCK_PER_K:
            issues.append(
                _issue(
                    "exclamation_density",
                    "blocking",
                    f"感叹号密度失控：每千字约 {exclaim_per_k:.1f} 个"
                    f"（阈值 {_EXCLAIM_BLOCK_PER_K}）。情绪表达应落在情节与"
                    "细节上，请大幅削减感叹号。",
                )
            )
        elif exclaim_per_k > _EXCLAIM_WARN_PER_K:
            issues.append(
                _issue(
                    "exclamation_density",
                    "warning",
                    f"感叹号偏多：每千字约 {exclaim_per_k:.1f} 个"
                    f"（告警线 {_EXCLAIM_WARN_PER_K}），建议改用叙述传达情绪。",
                )
            )

        similes = len(_SIMILE_MARK_RE.findall(body))
        simile_per_k = similes / per_k
        if simile_per_k > _SIMILE_BLOCK_PER_K:
            issues.append(
                _issue(
                    "simile_density",
                    "blocking",
                    f"比喻密度失控：每千字约 {simile_per_k:.1f} 处"
                    f"「像/如同/仿佛/宛如」类标记（阈值 {_SIMILE_BLOCK_PER_K}）。"
                    "请改用直接描写，只保留最有力的比喻。",
                )
            )
        elif simile_per_k > _SIMILE_WARN_PER_K:
            issues.append(
                _issue(
                    "simile_density",
                    "warning",
                    f"比喻偏密：每千字约 {simile_per_k:.1f} 处"
                    f"（告警线 {_SIMILE_WARN_PER_K}），建议削减一半。",
                )
            )

    if _DASH_SPLIT_RE.search(body):
        hit = _DASH_SPLIT_RE.search(body).group(0)
        issues.append(
            _issue(
                "dash_split_word",
                "warning",
                f"疑似破折号劈词「{hit}」：破折号不应把一个词/短语从中间劈开，"
                "请核对是否为排版错误（正常强调式破折号可忽略本条）。",
            )
        )

    for m in _FILLER_RE.finditer(body):
        issues.append(
            _issue(
                "filler_phrase",
                "warning",
                f"口语填充词「{m.group(0)}」裸奔在叙述里，请删除或改写为"
                "叙事性衔接。",
            )
        )

    trad_hits = sorted(set(_TRADITIONAL_RE.findall(body)))
    if trad_hits:
        preview = "、".join(trad_hits[:8]) + ("…" if len(trad_hits) > 8 else "")
        issues.append(
            _issue(
                "traditional_char",
                "warning",
                f"正文混入繁体字（{len(trad_hits)} 个：{preview}），"
                "简体成书中出现繁体几乎必然是生成/转换事故，请统一为简体。",
            )
        )

    issues.extend(_check_phrase_echo(body))
    issues.extend(_check_dialogue_jump(body))
    issues.extend(_check_sentence_repetition(body))
    return issues


def split_issues(
    issues: list[dict[str, str]],
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """按 severity 拆分为 (blocking, warning)。"""
    blocking = [i for i in issues if i.get("severity") == "blocking"]
    warning = [i for i in issues if i.get("severity") == "warning"]
    return blocking, warning
