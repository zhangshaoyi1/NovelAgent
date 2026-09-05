from __future__ import annotations

import re
from typing import Any


"""M5 文本净化：G-EN 英文污染硬关卡 + 元信息清理 + 去重（由 m5_write_chapter 拆出）"""

# ===== G-EN：正文纯中文硬关卡（确定性扫描，不依赖 LLM 自觉）=====
# 正文出现 2+ 连续「拉丁字母或下划线」即视为英文污染（单字母如 X光/S级 暂放行，避免过度纠偏）。
# 注意：必须至少含一个拉丁字母，避免把纯下划线占位符（如 ________）误判为英文。
_ENGLISH_RUN_RE = re.compile(r"[A-Za-z][A-Za-z_]*[A-Za-z]|[A-Za-z]{2,}")

# 中文连续区间判定用的正则（区间包含 CJK 及常见中文标点）
_CJK_RANGE_RE = re.compile(r"[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]")


def _collapse_cjk_spaces(text: str) -> str:
    """剔除中文字符之间的孤立空格。

    拉丁残留剔除（hard_replace_english 第 2 步）会把原词删掉，却可能在中文字符之间
    留下一个空格（如「发出 的摩擦声」）。这里将两侧都是中文/中文标点的空格直接删除，
    避免成稿出现本应被清理掉、却残留在中文间隙的空白。
    """
    return re.sub(
        r"(?<=" + _CJK_RANGE_RE.pattern + r") +(?=" + _CJK_RANGE_RE.pattern + r")",
        "",
        text,
    )
# 常见英文 token → 中文等价（仅作落盘前确定性兜底；主修复靠 LLM 修订把整句理顺）。
# 注意：值尽量取「独立名词」避免与原句已有中文叠词（如原句已有『认证』就不写『贵宾认证』）。
_ENGLISH_REPLACE_MAP = {
    "VIP": "贵宾", "KPI": "绩效指标", "CEO": "掌权者", "BUG": "漏洞", "bug": "漏洞",
    "IP": "网络地址", "ID": "身份标识", "logo": "标识", "log": "日志",
    "Plan": "备选方案", "NGOs": "国际非政府组织", "allocation_weight": "分配权重",
    "shoulders": "肩背", "loys": "洛城", "kreisel": "陀螺状", "thirty": "三十",
    "Lv": "级", "Lv2": "二级", "Lv3": "三级", "XH": "玄霄", "ZG": "天工", "API": "接口", "AI": "人工智能", "debug": "调试",
    "cache": "缓存", "buffer": "缓冲", "token": "令牌", "node": "节点",
    "DL": "地灵", "JY": "九幽", "LF": "灵链", "TM": "商标", "Street": "街道",
    # 叙事英文泄漏（无歧义内容词，确定性替换）
    "frowned": "皱眉", "already": "已经", "rejected": "拒绝", "please": "请",
    "swallowed": "咽下", "tomorrow": "明日", "darkness": "黑暗", "widest": "最宽",
    "dozens": "数十", "shook": "摇头", "chewing": "咀嚼", "clamp": "夹紧",
    "murmured": "低语", "platinum": "铂金", "slowly": "缓缓", "desperate": "绝望",
    "desperately": "拼命", "flicker": "闪烁", "shake": "晃动", "suddenly": "突然",
    "gossip": "闲言", "tied": "系住", "crimson": "绯红", "temporary": "临时",
    "weakly": "虚弱", "shrugged": "耸肩", "smiling": "微笑", "traps": "陷阱",
    "mixed": "混杂", "whispered": "低声", "screaming": "尖叫", "cheap": "廉价",
    "undergoing": "正经历", "faces": "脸庞", "raises": "抬起", "stabilizing": "稳住",
    "nodded": "点头", "verdict": "裁决", "waiting": "等待",
    "Instead": "反而", "cold": "冰冷", "sharp": "锐利", "interesting": "有趣",
    "shaky": "颤抖", "deeper": "更深", "stolen": "被夺", "lower": "压低",
    "seconds": "秒", "flick": "轻弹", "forward": "向前", "stepping": "迈步",
    "tokens": "令牌", "trembling": "颤抖", "eyes": "眼睛", "reborn": "重生",
    "report": "报告", "reverberating": "回荡", "funnel": "汇聚", "itself": "自身",
    "leverage": "利用", "painpoint": "痛点", "light": "灯光", "nobody": "无人",
    "shadows": "阴影", "IDs": "身份标识",
    "fifty": "五十", "AND": "而且", "silently": "沉默地", "twitch": "抽动",
    "brow": "眉心", "formula": "公式", "auctions": "拍卖会", "payload": "载荷",
    "Leveraged": "杠杆", "fingers": "手指", "distant": "远处", "progress": "进度",
    "Audit": "审计", "grant_token": "授权令牌", "ip_in_whitelist": "白名单内地址",
    "in_maintenance_window": "维护窗口期", "xxxx": "某某",
    "nodded": "点头", "auditorium": "礼堂", "CRC": "校验码",
}
# 修订时给 LLM 的中文等价参考（与上面映射保持一致口径）
_ENGLISH_REPLACE_GUIDE = (
    "正文存在英文单词/变量名/缩写，必须全部改写为自然的中文叙事，不得保留任何拉丁字母词。"
    "中文等价参考：VIP→贵宾认证；CEO→掌权者/总裁；KPI→绩效指标；bug/BUG→漏洞/差错；"
    "IP→网络地址；ID→身份标识；Plan B→备选方案；allocation_weight→分配权重的后门代码；"
    "NGOs→国际非政府组织；logo→标识；shoulders→肩背；loys→（改为中文地名，如洛城）；"
    "kreisel→（德文，改为中文描述，如陀螺状）；thirty→三十；Lv2→二级；XH→玄霄；ZG→天工。"
    "代码/变量名（如 allocation_weight）严禁直接写进正文，必须译为叙事化中文"
    "（如『分配权重的后门代码』）。仅替换英文部分，保持情节/人物/对话/结构完全不变，直接输出完整正文。"
)

# ===== 章节元信息清理（去除 LLM 误输出的标题/批注，保证字数统计与成稿干净）=====
# 开头的 markdown 标题行（模型常自报标题，落盘时由 _save_chapter 统一补「# 第N章 · 书名」）
_HEADING_RE = re.compile(r"^[ \t]*#+\s*.*$")
# 标题提取：『第X章 · 书名』『第X章 书名』等
_TITLE_SPLIT_SEPS = ("·", "．", "。", ":")
_CHAPTER_ORDINAL_RE = re.compile(r"^第\s*(?:\d+|[一二三四五六七八九十百千万]+)\s*章\s*")
# 结尾/任意位置的元信息行：『原文标题：…』『原标题：…』『章节名：…』
_TITLE_META_RE = re.compile(r"^(?:原文标题|原标题|题目|章节名|章节标题)\s*[:：]\s*.*$")
# 整行被（）包裹且含执导/批注词的可疑注记（如『（此处为快节奏场景，黑袍修士……）』）
_EDITOR_NOTE_WHOLE_LINE_RE = re.compile(r"^（[^）]*）$")
# 执导/批注关键词（命中即视为模型自陈述/编辑注记，非正文叙事）
_EDITOR_HINT_WORDS_RE = re.compile(
    r"(此处|本段|这里|这段|应当|应该|避免|为了|符合|压力曲线|铺垫|指导|批注|不要|"
    r"采用|营造|采用短促|禁用词|埋下伏笔|作为揭示|核心)"
)
# 章节收尾标注：『（本章完）』『（全文完）』『（本章结束）』等，LLM 常直接贴在末句/末行后
_END_MARK_RE = re.compile(r"[（(]\s*(?:本章完|全文完|本章结束)\s*[）)]")


def _strip_frontmatter(text: str) -> str:
    """去掉可能存在的 YAML frontmatter（保险起见，正文本身不应带，但修订回传可能夹带）"""
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            rest = text[end + 4:]
            return rest.lstrip("\n")
    return text


def scan_english_contamination(text: str) -> list[str]:
    """确定性扫描正文英文污染：返回去重后的 2+ 连续拉丁字母 token 列表（单字母如 X光 放行）。"""
    if not text:
        return []
    body = _strip_frontmatter(text)
    tokens = _ENGLISH_RUN_RE.findall(body)
    seen: set[str] = set()
    uniq: list[str] = []
    for t in tokens:
        if t not in seen:
            seen.add(t)
            uniq.append(t)
    return uniq


def hard_replace_english(text: str) -> tuple[str, list[str]]:
    """落盘前确定性兜底：把已知英文 token 替换为中文；任何残留拉丁串直接剔除。

    关键保证：返回的 out 正文一定零英文（未知 token 宁可删除也不留英文）。
    返回 (清理后文本, 仍残留的 token 列表[正常应为空])。

    设计要点：
    - 大小写不敏感（Bugs/BUG/Bug 都命中 bug 映射），避免『已知词却因大小写漏替换』。
    - 长词优先（ip_in_whitelist 先于 ip；VIP 先于 IP），防止子串误中。
    - 任何仍残留的拉丁串（未知词）一律剔除，作为最后保险，绝不让英文落盘。
    """
    # 1) 已知 token 大小写不敏感替换（长词优先，避免 bug 误中 debug 等）
    out = text
    for tok in sorted(_ENGLISH_REPLACE_MAP, key=len, reverse=True):
        repl = _ENGLISH_REPLACE_MAP[tok]
        out = re.sub(
            r"(?<![A-Za-z])" + re.escape(tok) + r"(?![A-Za-z])",
            repl, out, flags=re.IGNORECASE,
        )
    # 2) 任何仍残留的拉丁串（未知词）直接剔除（宁可丢词也不留英文）
    residual = scan_english_contamination(out)
    if residual:
        out = _ENGLISH_RUN_RE.sub("", out)
        # 只压缩同行内连续空格，绝不动换行——否则 \n\n 段落分隔会被压成单空格，
        # 导致整章正文变成一长段、丢失段落格式（历史 ch20 单段正文根因）。
        out = re.sub(r"[ \t]+", " ", out)
        # 行首尾空白清除（纯空白行保留为段落分隔的空行，不当作内容删除）
        out = re.sub(r"[ \t]+(?=\n)", "", out)
        out = re.sub(r"\n[ \t]+", "\n", out)
        # 压缩过密空行：连同残留的连续换行，恢复为单一空行分隔
        out = re.sub(r"\n{3,}", "\n\n", out)
        out = out.strip()
        out = _collapse_cjk_spaces(out)
        residual = scan_english_contamination(out)
    return out, residual




# ============================================================
# 工具函数：自动按句分段（兜底 LLM 完全不分段的情况）
# ============================================================
def _auto_split_paragraphs(text: str) -> str:
    """自动将无段落分隔的长文本按句分段，每 2-4 句组成一个段落。

    仅作为安全网，当 LLM 完全遗漏段落分隔时使用。
    """
    import re as _re

    # 按中文句子结束符分割（保留分隔符）
    parts = _re.split(r"(?<=[。！？」])", text)
    sentences = [s.strip() for s in parts if s.strip()]

    if len(sentences) <= 1:
        return text

    # 分组为段落（每段 2-4 句，优先 3 句）
    paragraphs: list[str] = []
    current: list[str] = []
    for s in sentences:
        current.append(s)
        if len(current) >= 3:
            paragraphs.append("".join(current))
            current = []
    if current:
        paragraphs.append("".join(current))

    return "\n\n".join(paragraphs)


class M5TextHygieneMixin:
    """正文格式化 / 标题提取 / 正文清理 / 整章去重（纯文本处理）"""

    # ============================================================
    # 1.5 章节正文后格式化（安全网，兜底 LLM 段落格式遗漏）
    # ============================================================
    @staticmethod
    def _format_chapter_body(text: str) -> str:
        """规范化章节正文的段落格式。

        1. 去除每行首尾空白字符
        2. 将连续 3+ 空行压缩为 1 个空行
        3. 确保最后没有多余空行
        4. 如果全文没有任何段落分隔，自动按句分段（兜底 LLM 完全不分段的情况）
        """
        import re as _re

        # 1) 按行处理，去除行首尾空白
        lines = text.split("\n")
        stripped = [line.strip() for line in lines]

        # 2) 压缩连续空行：连续空白行 → 一个空行
        result: list[str] = []
        blank_count = 0
        for line in stripped:
            if line == "":
                blank_count += 1
                if blank_count == 1:
                    result.append("")
            else:
                blank_count = 0
                result.append(line)

        # 3) 去除末尾多余空行
        while result and result[-1] == "":
            result.pop()

        body = "\n".join(result)

        # 4) 如果全文没有任何段落分隔（无空行），自动按句分段
        if "\n" not in body and len(body) > 200:
            body = _auto_split_paragraphs(body)

        return body
    # ============================================================
    # 4. 章节标题
    # ============================================================
    def _extract_title(self, text: str, ctx: dict[str, Any]) -> str:
        """提取章节名：优先取 markdown 标题行『第X章 · 书名』中的书名，否则取默认。

        修正：模型常在正文首行自报标题（如『# 第二章 · 幼犬噬主』），
        旧逻辑只判断首行是否以「第」开头而退回『第N章』占位，导致成稿双标题
        （落盘模板『# 第 2 章 · 第2章』 + 正文残留『# 第二章 · 幼犬噬主』）。
        现改为从标题行中提取真正的书名（『·』后 / 去『第N章』前缀）。
        """
        for line in text.split("\n"):
            s = line.strip()
            if not s:
                continue
            if not s.startswith("#"):
                break  # 已到正文，未命中标题行 → 走默认
            head = re.sub(r"^#+\s*", "", s).strip()
            for sep in _TITLE_SPLIT_SEPS:
                if sep in head:
                    after = head.split(sep, 1)[1].strip()
                    if after:
                        return after[:30]
            # 无分隔符：去掉『第N章』前缀后再取（如『第5章 鬼市』→『鬼市』）
            after_no = _CHAPTER_ORDINAL_RE.sub("", head).strip()
            if after_no:
                return after_no[:30]
            if not re.match(r"^第", head):
                return head[:30]
            # 命中『第X章』但无书名 → 继续，最终走默认
        return f"第{ctx['chapter_num']}章"
    @staticmethod
    def _clean_chapter_body(text: str) -> str:
        """去掉 LLM 误输出的元信息，只保留可读正文。

        处理：
        1. 开头连续的 markdown 标题行（模型自报标题，落盘统一补标题，避免双标题）。
        2. 任意位置的『原文标题：…』『原标题：…』等元信息行。
        3. 整行被（）包裹且含执导/批注词的编辑注记（如『（此处为快节奏场景……）』）。
        """
        lines = text.split("\n")
        # 1) 去掉开头空行与 markdown 标题行（模型自报标题，落盘统一补标题，避免双标题）
        while lines and (not lines[0].strip() or _HEADING_RE.match(lines[0])):
            lines.pop(0)
        # 2) 过滤元信息行（保留空行结构，便于后续段落压缩）
        out: list[str] = []
        for ln in lines:
            s = ln.strip()
            if not s:
                out.append(ln)
                continue
            if _TITLE_META_RE.match(s):
                continue
            if (
                _EDITOR_NOTE_WHOLE_LINE_RE.match(s)
                and _EDITOR_HINT_WORDS_RE.search(s)
            ):
                continue
            out.append(ln)
        # 3) 去掉末尾多余空行
        while out and not out[-1].strip():
            out.pop()
        # 4) 去掉行尾/任意位置的章节收尾标注『（本章完）』等（LLM 常贴在末句后）
        out = [_END_MARK_RE.sub("", ln).rstrip() for ln in out]
        return "\n".join(out)
    @staticmethod
    def _dedup_repeated_chapter(text: str) -> str:
        """去掉 LLM 把整章正文重复输出两遍的情况（正文中途再次出现『# 第N章·…』标题）。

        复现：LLM 自报标题 + 完整正文后，又重复了一遍『# 第一章 · …』+ 正文，
        导致落盘出现重复内容、字数虚增。本方法在 ``_clean_chapter_body`` 之后、
        字数统计之前调用：若正文中段再次出现章节标题行（非开头），则在中段标题处截断，
        只保留第一遍正文（去掉后续重复内容）。
        """
        import re as _re

        # 章节标题形如：# 第 1 章 · 标题 / # 第一章 · 标题 / # 第1章· 标题。
        # 关键：LLM 重复正文时该标题常被直接拼在一段末尾（无换行），因此**不**锚定行首，
        # 用 `#` 前置即可（小说正文里 # 与『第N章』连写几乎不会合法出现）。
        title_re = _re.compile(r"#\s*第\s*[0-9一二三四五六七八九十百千]+\s*章\s*[·:：,，、\-\s]")
        matches = list(title_re.finditer(text))
        if not matches:
            return text
        # 正文开头正常应无章节标题（落盘统一由 _save_chapter 生成 `# 第 N 章 · 标题`），
        # 所以出现的首个标题视为重复起点，截断到它之前即可。
        first = matches[0]
        # 保险：若标题恰在正文开头（极端情形 _clean_chapter_body 未剔除干净），跳过去重
        if first.start() <= 1:
            return text
        head = text[: first.start()].rstrip()
        return head
    # ---- 尾部循环段落去重（P-DEDUP-2）----------------------------
    # ch238 实证：LLM 在章节结尾把**前面已写过的整段连续段落原样复读**一遍，但
    # 中途**没有再次出现『# 第N章·…』标题**，故 _dedup_repeated_chapter 的标题锚点
    # 不命中。本方法改为纯段落级相似度匹配：检测正文末尾是否存在一段「循环复述」，
    # 即最长的**后缀块**与其前面的某段连续块高度相似，命中则以该后缀块起点为界
    # 截断，删除复读尾部。
    @staticmethod
    def _dedup_tail_loop(text: str) -> str:
        """去掉章节尾部把前面段落整段复读的循环重复（无标题锚点）。

        仅在 _clean_chapter_body 去元信息 + _dedup_repeated_chapter 整章去重后调用。

        判定（P-DEDUP-2，全部满足才截断，保证保守不误删）：
          - 正文按空行切分为段落块 blocks；
          - 寻找**最长**循环块长度 L（≥ min_run）：存在源起点 i 与复读起点 r=i+L、
            使 blocks[i:i+L] 与 blocks[r:] 逐段对齐（段级 Jaccard ≥ sim）；
          - 命中即以 r 为界返回 blocks[:r]。
        优先取最长 L，能识别任意长度的尾部循环复读。
        """
        # 段级字符集合 Jaccard 阈值（与 evaluator._sent_sim 口径一致）
        sim = 0.8
        # 至少连续对齐多少段才判定为循环复读（≥2 避免单段巧合）
        min_run = 2

        blocks = [b.strip() for b in text.split("\n\n") if b.strip()]
        n = len(blocks)
        if n < 2 * min_run:          # 至少要够「源块 + 独立循环块」
            return text

        def _sim(a: str, b: str) -> float:
            sa, sb = set(a), set(b)
            if not sa or not sb:
                return 0.0
            return len(sa & sb) / len(sa | sb)

        # 找最长的后缀循环块：r 为复读起点，L = n - r 为循环长度，源起点 i = r - L。
        # 遍历可能的循环长度 L，校验 blocks[0:L] 与 blocks[r:]... 用源起点 = L（前缀）
        # 与后缀 blocks[n-L:] 对齐是最典型的「开头复读」；也支持源在正文中部的
        # （blocks[i:i+L] 与 blocks[n-L:] 对齐），这里统一扫描 i。
        best_r = -1
        best_L = 0
        # 循环块长度上界：正文一半以内（复读块不可能超过正文总长的一半）
        max_L = n // 2
        for L in range(max_L, min_run - 1, -1):
            r = n - L
            # 源块起点 i 需满足 i + L <= r（源与复读不重叠）
            for i in range(0, r - L + 1):
                ok = True
                for k in range(L):
                    if _sim(blocks[i + k], blocks[r + k]) < sim:
                        ok = False
                        break
                if ok:
                    best_r = r
                    best_L = L
                    break
            if best_r != -1:
                break
        if best_r == -1:
            return text
        head_text = "\n\n".join(blocks[:best_r]).rstrip()
        return head_text
