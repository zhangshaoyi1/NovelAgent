"""坏点自动采集器（A1）

不靠逐章手动介入，自动发现已完成章节的"硬伤"与"模板复用"，输出结构化 bad_points。

双通道：
1. **静态规则通道**（零 LLM，可靠）：字数门禁、灭门回忆计数、手段复用频率估算、
   章节标题去占位、沉没模板标记。
2. **LLM 精扫通道**（可选，默认开）：一次调用扫 ch001-020 + 设定文档，输出结构化
   坏点清单（事实冲突/角色漂移/逻辑断裂），并给出 suggested_fix。

坏点分层（对接 A2 仲裁）：
- type == "fact_conflict" / (plot_hole / character_drift) → 事实型 → 可自动定版自动改
- type == "orientation" / "self_repeat" / "underworded"（取向/审美类）→ 进建议清单，不自动改正文

设计原则：
- 静态规则优先，稳定可复现；LLM 只在 static 之外补漏，避免把"有意伏笔"当硬伤。
- 所有产物可追溯；带 `--dry-run` 只采集不改动。
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import frontmatter
from rich.console import Console

from agent.base.utils import parse_llm_json


# ============================================================
# 数据契约
# ============================================================
@dataclass
class BadPoint:
    """单条坏点。"""

    type: str                        # fact_conflict | plot_hole | character_drift | orientation | self_repeat | underworded
    severity: str                    # high | medium | low
    chapter: int | None = None       # 关联章节（None = 全局/设定层）
    evidence: str = ""               # 依据原文片段/事实
    suggested_fix: str = ""          # 建议修复（事实型 → 可直接作 feedback）
    source: str = "static"           # static | llm
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ScannerReport:
    """采集结果。"""

    points: list[BadPoint] = field(default_factory=list)
    scanned_chapters: int = 0
    scanned_at: str = ""

    @property
    def has_points(self) -> bool:
        return len(self.points) > 0

    @property
    def by_type(self) -> dict[str, list[BadPoint]]:
        out: dict[str, list[BadPoint]] = {}
        for p in self.points:
            out.setdefault(p.type, []).append(p)
        return out

    def to_dict(self) -> dict[str, Any]:
        return {
            "scanned_chapters": self.scanned_chapters,
            "scanned_at": self.scanned_at,
            "points": [p.to_dict() for p in self.points],
        }


def _wc(text: str) -> int:
    return len(text.replace("\n", "").replace(" ", ""))


# 灭门回忆关键词（用于计数，align 与 sum 代接续判断交给字段证据）
_ANNIHILATION_RECALL_PATTERNS = [
    re.compile(r"灭门"),
    re.compile(r"(当年|从前|昔日|幼年).{0,12}(灭门|满门|全家).{0,6}(惨死|被杀|死|屠)"),
    re.compile(r"回忆.{0,6}(灭门|家人|爹娘|父母)"),
]

# 手段复用关键词（用于频率估算；真实判定依赖 LLM，静态只标记高嫌疑）
_TROPE_KEYWORDS = {
    "借刀杀人": re.compile(r"借.{0,6}(刀|势|剑|毒).{0,10}(杀人|借刀)"),
    "假死": re.compile(r"假死|诈死|装死"),
    "灭门报复": re.compile(r"灭门.{0,10}(复仇|报仇)|复仇.{0,10}灭门"),
}

# 沉没创作残留（作者注/编辑器指令，写入正文应剔除）
_EDITORIAL_PATTERNS = [
    re.compile(r"此处为|避免|压力曲线|铺垫|不要|埋下伏笔", re.IGNORECASE),
    re.compile(r"^原文标题:"),
    re.compile(r"【.*?(作者|编辑器|指令|注).*?】"),
]


class BadPointScanner:
    """坏点采集器。"""

    WORD_MIN = 1500           # 字数下限（硬门禁）
    WORD_TARGET = 3000        # 目标字数
    ANNIHILATION_CAP = 3      # 灭门回忆上限（书级偏好可覆盖）
    TROPE_CAP = 2             # 同手段复用上限

    def __init__(
        self,
        project_dir: str | Path,
        llm: Any | None = None,
        console: Console | None = None,
        *,
        use_llm: bool = True,
    ) -> None:
        self.project_dir = Path(project_dir)
        self.chapters_dir = self.project_dir / "chapters"
        self._llm = llm
        self.console = console or Console()
        self.use_llm = use_llm

    # ------------------------------------------------------ LLM 惰性
    @property
    def llm(self) -> Any:
        if self._llm is None:
            from agent.client import LLMClient
            self._llm = LLMClient()
        return self._llm

    # ------------------------------------------------------ 主入口
    def scan(self) -> ScannerReport:
        chapters = sorted(self.chapters_dir.glob("ch[0-9]*.md")) if self.chapters_dir.exists() else []
        report = ScannerReport(
            scanned_chapters=len(chapters),
            scanned_at=datetime.now().isoformat(timespec="seconds"),
        )
        texts: dict[int, str] = {}

        for ch in chapters:
            num = self._chapter_num(ch)
            post = frontmatter.load(ch)
            body = post.content if hasattr(post, "content") else ""
            texts[num] = body

        # 通道1：静态规则
        report.points.extend(self._static_scan(texts))

        # 通道2：LLM 精扫（静态之外补漏）
        if self.use_llm and texts:
            try:
                report.points.extend(self._llm_scan(texts))
            except Exception as e:  # noqa: BLE001 - LLM 失败降级，不阻断
                self.console.print(f"[yellow]⚠ LLM 坏点精扫失败（保留静态结果）：{e}[/yellow]")

        report.points.sort(
            key=lambda p: (
                {"high": 0, "medium": 1, "low": 2}[p.severity],
                p.chapter if p.chapter is not None else 10**9,
            )
        )
        return report

    # ------------------------------------------------------ 静态规则
    def _static_scan(self, texts: dict[int, str]) -> list[BadPoint]:
        points: list[BadPoint] = []
        ann_count = _sum(len(p.findall(t)) for p in _ANNIHILATION_RECALL_PATTERNS for t in texts.values())
        trope_counts = {name: 0 for name in _TROPE_KEYWORDS}

        for num, t in texts.items():
            # 字数门禁
            wc = _wc(t)
            if wc < self.WORD_MIN:
                points.append(BadPoint(
                    type="underworded", severity="high", chapter=num,
                    evidence=f"本章去空白 {wc} 字，低于下限 {self.WORD_MIN}",
                    suggested_fix=f"本章字数偏低（{wc}），请扩写到 {self.WORD_TARGET} 字以上，补足情节展开与细节。",
                ))
            elif wc < self.WORD_TARGET:
                points.append(BadPoint(
                    type="underworded", severity="low", chapter=num,
                    evidence=f"本章去空白 {wc} 字，未达目标 {self.WORD_TARGET}",
                    suggested_fix=f"本章字数 {wc} 未达 {self.WORD_TARGET} 的目标，酌情充实。",
                ))

            # 创作残留
            for pat in _EDITORIAL_PATTERNS:
                if pat.search(t):
                    points.append(BadPoint(
                        type="orientation", severity="low", chapter=num,
                        evidence=f"正文含编辑/作者指令残留（命中 {pat.pattern[:24]}…），应剔除后再计数",
                        suggested_fix="剔除正文中的作者注/编辑器指令/『原文标题:』行，保持正文纯净。",
                    ))
                    break

            # 章节标题去占位
            title = _extract_title(t)
            if title and title.startswith(("·", "· ", "-", "：")):
                points.append(BadPoint(
                    type="orientation", severity="medium", chapter=num,
                    evidence=f"章节标题含占位前缀：『{title[:30]}』",
                    suggested_fix="去掉标题占位前缀（如『· 幼犬噬主』→『幼犬噬主』）。",
                ))

            # 手段复用统计
            for name, pat in _TROPE_KEYWORDS.items():
                n = len(pat.findall(t))
                trope_counts[name] += n

        # 灭门回忆超限（跨章全局）
        if ann_count > self.ANNIHILATION_CAP:
            points.append(BadPoint(
                type="self_repeat", severity="medium",
                evidence=f"全本灭门回忆约 {ann_count} 次（上限 {self.ANNIHILATION_CAP}），读者观感重复",
                suggested_fix="后续章节避免再直接复述灭门记忆；确需提及则压缩到一两句，别再整段回忆。",
            ))

        # 手段复用超限（跨章全局）
        for name, n in trope_counts.items():
            if n > self.TROPE_CAP:
                points.append(BadPoint(
                    type="self_repeat", severity="medium",
                    evidence=f"『{name}』手段全本出现约 {n} 次，模板化风险高",
                    suggested_fix=f"『{name}』已复用过多，后续换用其他冲突手段或改变切入角度；若必须复用，压缩篇幅/换呈现。",
                ))

        return points

    # ------------------------------------------------------ LLM 精扫
    def _llm_scan(self, texts: dict[int, str]) -> list[BadPoint]:
        # 汇总设定文档（低 token 成本，先截断）
        world = self._read_section("world.md")
        chars = self._read_section("characters")
        golden = self._read_section("golden_finger_registration.md")

        # 章节正文拼接（限额，避免超长）
        parts: list[str] = []
        for num in sorted(texts):
            parts.append(f"### 第{num}章\n{texts[num][:1600]}")
        chapters_text = "\n\n".join(parts)[:12000]

        system = _LLM_SCAN_SYSTEM_PROMPT
        user = _LLM_SCAN_USER_TEMPLATE.format(
            world=world[:2000] or "（无 world.md）",
            characters=chars[:2000] or "（无角色档案）",
            golden=golden[:1000] or "（无金手指登记）",
            chapters=chapters_text,
        )
        resp = self.llm.chat_utility(
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0.1,
            max_tokens=2000,
        )
        try:
            data = parse_llm_json(resp.text)
        except ValueError:
            # 首败后追加「强制纯 JSON」重试一次（项目惯例，减少重复解析失败）
            retry = self.llm.chat_utility(
                messages=[
                    {"role": "system", "content": system + "\n铁律：只输出合法 JSON，禁止任何 ``` 标记或解释文字。"},
                    {"role": "user", "content": "请重新扫描并严格只输出 JSON：" + user},
                ],
                temperature=0.1,
                max_tokens=2000,
            )
            data = parse_llm_json(retry.text)
        out: list[BadPoint] = []
        for item in data.get("bad_points", []) or []:
            btype = str(item.get("type", "plot_hole"))
            out.append(BadPoint(
                type=btype,
                severity=str(item.get("severity", "medium")),
                chapter=item.get("chapter"),
                evidence=str(item.get("evidence", "")),
                suggested_fix=str(item.get("suggested_fix", "")),
                source="llm",
                metadata={"llm_confidence": item.get("confidence", "low")},
            ))
        return out

    # ------------------------------------------------------ 工具
    def _chapter_num(self, path: Path) -> int:
        m = re.search(r"(\d+)", path.stem)
        return int(m.group(1)) if m else 0

    def _read_section(self, name: str) -> str:
        """读取项目根下给定文件名（world / characters 目录 / 单个文件）。"""
        if name == "characters":
            d = self.project_dir / "characters"
            if not d.exists():
                return ""
            return "\n\n".join(
                f"### {p.stem}\n{' '.join(p.read_text(encoding='utf-8').split())[:800]}"
                for p in sorted(d.glob("*.md"))
            )
        p = self.project_dir / name
        return p.read_text(encoding="utf-8") if p.exists() else ""


# ============================================================
# LLM 提示词
# ============================================================
_LLM_SCAN_SYSTEM_PROMPT = """你是一位资深小说设定审读编辑，负责从已完成章节与设定集中找出**真正的硬伤**，输出结构化 JSON。

只报你能用原文证据坐实的坏点，不要臆测；判定标准：
- fact_conflict：章节间或章节与设定文档的金手指规则/角色身份/已揭示真相互相矛盾（有客观对错）。
- plot_hole：明显断裂的名线（如已死之人复活但无解释、角色下落无故消失）。
- character_drift：同一角色前后人设/语言指纹冲突。
- orientation：创作取向问题（如感情线与"苟道独狼"定位冲突、套语堆砌）——这类只是建议，不算事实错误。
- self_repeat：同一冲突手段/回忆在短区间高频复用。

铁律：
1. 拿不准、无原文证据的不要报——宁缺毋滥（避免把作者有意伏笔当硬伤）。
2. 输出必须是合法 JSON，格式：
{"bad_points":[{"type":"","severity":"high|medium|low","chapter":1,"evidence":"原文依据","suggested_fix":"具体可执行的修复建议","confidence":"high|medium|low"}]}
3. 不要输出 JSON 之外任何文字。"""

_LLM_SCAN_USER_TEMPLATE = """# 设定文档
## world.md
{world}
## 角色档案
{characters}
## 金手指登记
{golden}

# 已完成章节（节选）
{chapters}

# 任务
请按系统提示的判定标准，扫描以上内容找出真正的硬伤。输出合法 JSON，只有确定无疑的才报。"""


# ============================================================
# 模块级工具
# ============================================================
def _sum(iterable: Any) -> int:
    return int(sum(iterable))


def _extract_title(body: str) -> str:
    """从正文提取章节标题（首个非空标题行或 frontmatter title 由调用方另行处理）。"""
    for line in body.splitlines():
        line = line.strip()
        if line.startswith("#"):
            return line.lstrip("#").strip()
    return ""