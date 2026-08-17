"""Dashboard 只读聚合层（增量 B）

``DashboardAggregator`` 单向读取 NovelAgent 已落盘的结构化产物
（``relations/graph.md`` / ``protagonist_route.md`` / ``foreshadows.md`` /
``.state/`` 各 JSON 与章节 frontmatter）以及 ``Doctor`` 健康诊断，聚合成
``DashboardData``（含 8 个面板数据容器）供 CLI / 模板 / ``--json`` 复用。

设计要点（与 ``docs/B可视化面板设计.md`` 一致）：
- 纯只读：仅 ``Path.read_text`` / ``frontmatter.load`` / ``json.loads``，绝不写回。
- 逐源隔离：每个 ``_read_*`` 方法内部独立 ``try/except``，解析失败 / 文件缺失
  降级为 ``available=False`` 或字段取默认值，绝不向上抛异常中断整体聚合。
- 可选数据优雅降级：``pacing.json`` / ``learnings/learnings.json`` /
  ``.state/rag/index.json`` 三类为可选数据源，缺失或解析失败 → 对应面板
  ``available=False``，整体仍 ``success:true``。
- 复用 ``Doctor.check(ping=False)`` 取健康摘要，不重造诊断逻辑。

本模块仅依赖标准库（json / re / pathlib / dataclasses）+ 已依赖的
``python-frontmatter``，零新强制依赖。
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import frontmatter


# ============================================================
# 面板数据容器（dataclass）
# ============================================================
@dataclass
class ForeshadowRow:
    """伏笔表格单行（透传 + 关联角色拆分）"""

    fid: str = ""
    content: str = ""
    plant_at: str = ""
    recover_at: str = ""
    status: str = ""
    chars: list[str] = field(default_factory=list)


@dataclass
class ChapterInfo:
    """章节质量概览（仅取 frontmatter 子集，其余字段不消费）"""

    num: int = 0
    subline: str | None = None
    route_node: str | None = None
    quality_passed: bool | None = None
    pressure_stage: str | None = None


@dataclass
class RelationPanel:
    """① 关系网（必选可降级）"""

    available: bool = False
    mermaid: str = ""
    node_count: int | None = None
    edge_count: int | None = None


@dataclass
class RoutePanel:
    """② 主角路线（必选可降级）"""

    available: bool = False
    markdown: str = ""
    toc: list[str] = field(default_factory=list)


@dataclass
class ForeshadowPanel:
    """③ 伏笔清单（必选可降级）"""

    available: bool = False
    total: int = 0
    by_status: dict[str, int] = field(default_factory=dict)
    pending: list[ForeshadowRow] = field(default_factory=list)


@dataclass
class ProgressPanel:
    """④ 写作进度（必选可降级）"""

    available: bool = False
    state: str = ""
    written: int = 0
    pass_rate: float | None = None
    chapters: list[ChapterInfo] = field(default_factory=list)


@dataclass
class PacingPanel:
    """⑤ 追读力（可选）"""

    available: bool = False
    open_debts: list[dict] = field(default_factory=list)
    cool_density: list[float] = field(default_factory=list)


@dataclass
class LearningPanel:
    """⑥ 学习沉淀（可选）"""

    available: bool = False
    items: list[dict] = field(default_factory=list)


@dataclass
class RagPanel:
    """⑦ RAG 索引统计（可选）"""

    available: bool = False
    index_count: int | None = None


@dataclass
class HealthPanel:
    """⑧ 健康摘要（必选，复用 Doctor）"""

    available: bool = False
    healthy: bool = False
    checks: list[dict] = field(default_factory=list)


@dataclass
class DashboardData:
    """Dashboard 根容器（8 面板 + 元信息）"""

    project_dir: str = ""
    generated_at: str = ""
    relations: RelationPanel = field(default_factory=RelationPanel)
    route: RoutePanel = field(default_factory=RoutePanel)
    foreshadows: ForeshadowPanel = field(default_factory=ForeshadowPanel)
    progress: ProgressPanel = field(default_factory=ProgressPanel)
    pacing: PacingPanel = field(default_factory=PacingPanel)
    learnings: LearningPanel = field(default_factory=LearningPanel)
    rag: RagPanel = field(default_factory=RagPanel)
    health: HealthPanel = field(default_factory=HealthPanel)

    def to_payload(self) -> dict[str, Any]:
        """序列化为前端 / ``--json`` 同构的纯 dict（所有字段均为 str/int/bool/
        None/list/dict，Path / datetime 已在构造时转为 str，可直接 ``json.dumps``）。
        """
        return {
            "project_dir": self.project_dir,
            "generated_at": self.generated_at,
            "relations": asdict(self.relations),
            "route": asdict(self.route),
            "foreshadows": asdict(self.foreshadows),
            "progress": asdict(self.progress),
            "pacing": asdict(self.pacing),
            "learnings": asdict(self.learnings),
            "rag": asdict(self.rag),
            "health": asdict(self.health),
        }


# ============================================================
# 聚合器
# ============================================================
class DashboardAggregator:
    """只读聚合器：聚合 8 类面板，逐源 ``try/except`` 降级，绝不抛错中断。"""

    def __init__(self, project_dir: Path) -> None:
        self.project_dir = Path(project_dir)

    def aggregate(self) -> DashboardData:
        """读取项目目录，返回 ``DashboardData``。

        每个 ``_read_*`` 已内部隔离，故本方法整体不会因任一数据源损坏而中断。
        """
        return DashboardData(
            project_dir=str(self.project_dir),
            generated_at=datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),  # noqa: DTZ005 - 设计指定本地时间、无 tz（见设计 §3.1/§4）
            relations=self._read_relations(),
            route=self._read_route(),
            foreshadows=self._read_foreshadows(),
            progress=self._read_progress(),
            pacing=self._read_pacing(),
            learnings=self._read_learnings(),
            rag=self._read_rag(),
            health=self._read_health(),
        )

    # ----------------------------------------------------------
    # ① 关系网：mermaid 原样透传；节点/边统计轻量表格解析（失败降级为 null）
    # ----------------------------------------------------------
    def _read_relations(self) -> RelationPanel:
        graph = self.project_dir / "relations" / "graph.md"
        if not graph.exists():
            return RelationPanel(available=False)
        try:
            text = graph.read_text(encoding="utf-8")
        except OSError:
            return RelationPanel(available=False)

        panel = RelationPanel(available=True, mermaid="", node_count=None, edge_count=None)

        # mermaid 围栏块原样透传（前端渲染，零解析）
        m = re.search(r"```mermaid\s*\n(.*?)```", text, re.DOTALL)
        if m:
            panel.mermaid = m.group(1).strip()

        # 节点计数：## 节点 表数据行
        node_section = self._section_after(text, "节点")
        if node_section is not None:
            try:
                panel.node_count = self._count_table_rows(node_section)
            except Exception:  # noqa: BLE001 - 统计失败降级为 null
                panel.node_count = None

        # 边计数：## 边（关系） 表数据行（**排除** ## 归档边）
        edge_section = self._section_after(text, "边（关系）")
        if edge_section is not None:
            try:
                panel.edge_count = self._count_table_rows(edge_section)
            except Exception:  # noqa: BLE001
                panel.edge_count = None

        return panel

    # ----------------------------------------------------------
    # ② 主角路线：markdown 原样透传 + ## Nxx 标题 TOC
    # ----------------------------------------------------------
    def _read_route(self) -> RoutePanel:
        route = self.project_dir / "protagonist_route.md"
        if not route.exists():
            return RoutePanel(available=False)
        try:
            text = route.read_text(encoding="utf-8")
        except OSError:
            return RoutePanel(available=False)
        try:
            toc = re.findall(r"^##\s+(N\d+\s*·\s*.+)$", text, re.MULTILINE)
        except Exception:  # noqa: BLE001
            toc = []
        return RoutePanel(available=True, markdown=text, toc=toc)

    # ----------------------------------------------------------
    # ③ 伏笔：F-ID 表格 → by_status 分布 + pending 列表
    # ----------------------------------------------------------
    def _read_foreshadows(self) -> ForeshadowPanel:
        fs = self.project_dir / "foreshadows.md"
        if not fs.exists():
            return ForeshadowPanel(available=False, total=0, by_status={}, pending=[])
        try:
            text = fs.read_text(encoding="utf-8")
        except OSError:
            return ForeshadowPanel(available=False, total=0, by_status={}, pending=[])

        rows: list[ForeshadowRow] = []
        # 固定 4 个规范状态键（§3.2），缺失状态以 0 计，保证 to_payload 形态稳定
        by_status: dict[str, int] = {"未埋": 0, "已埋": 0, "已回收": 0, "已废弃": 0}
        pending: list[ForeshadowRow] = []
        try:
            for line in text.splitlines():
                line = line.strip()
                if not line.startswith("|"):
                    continue
                # 跳过表头与分隔行
                if line.startswith("|---") or set(line) <= set("|-: "):
                    continue
                cells = [c.strip() for c in line.strip("|").split("|")]
                if not cells or not cells[0].startswith("F-"):
                    continue
                if len(cells) < 6:
                    continue
                fid = cells[0]
                status = cells[4]
                # 关联角色：逗号切分后逐项 strip（含 curly quote '医生' U+2018/2019）
                chars = [c.strip() for c in cells[5].split(",") if c.strip()]
                row = ForeshadowRow(
                    fid=fid,
                    content=cells[1],
                    plant_at=cells[2],
                    recover_at=cells[3],
                    status=status,
                    chars=chars,
                )
                rows.append(row)
                by_status[status] = by_status.get(status, 0) + 1
                # 待回收：状态 ∈ {未埋, 已埋}（尚未回收/废弃）
                if status in ("未埋", "已埋"):
                    pending.append(row)
        except Exception:  # noqa: BLE001 - 解析失败降级为空面板
            return ForeshadowPanel(available=False, total=0, by_status={}, pending=[])

        if not rows:
            return ForeshadowPanel(available=False, total=0, by_status={}, pending=[])
        return ForeshadowPanel(
            available=True,
            total=len(rows),
            by_status=by_status,
            pending=pending,
        )

    # ----------------------------------------------------------
    # ④ 进度：state.json + 章节 frontmatter（取子集）
    # ----------------------------------------------------------
    def _read_progress(self) -> ProgressPanel:
        state_file = self.project_dir / ".state" / "state.json"
        if not state_file.exists():
            return ProgressPanel(available=False)
        try:
            data = json.loads(state_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return ProgressPanel(available=False)

        state_val = data.get("state", "")
        progress = data.get("progress") or {}
        total_written = progress.get("total_written")

        chapters: list[ChapterInfo] = []
        chapters_dir = self.project_dir / "chapters"
        if chapters_dir.exists():
            try:
                files = sorted(chapters_dir.glob("ch*.md"))
            except OSError:
                files = []
            for f in files:
                try:
                    post = frontmatter.load(f)
                    meta = post.metadata
                except Exception:  # noqa: BLE001, S112 - 单章解析失败跳过，不中断聚合
                    continue
                num = meta.get("chapter")
                if not isinstance(num, int):
                    m = re.search(r"ch(\d+)", f.name)
                    num = int(m.group(1)) if m else 0
                chapters.append(
                    ChapterInfo(
                        num=int(num) if isinstance(num, int) else 0,
                        subline=meta.get("subline"),
                        route_node=meta.get("route_node"),
                        quality_passed=meta.get("quality_passed"),
                        pressure_stage=meta.get("pressure_stage"),
                    )
                )
        chapters.sort(key=lambda c: c.num)

        written = total_written if isinstance(total_written, int) else len(chapters)
        if chapters:
            passed = sum(1 for c in chapters if c.quality_passed is True)
            pass_rate: float | None = passed / len(chapters)
        else:
            pass_rate = None

        return ProgressPanel(
            available=True,
            state=state_val,
            written=int(written),
            pass_rate=pass_rate,
            chapters=chapters,
        )

    # ----------------------------------------------------------
    # ⑤ 追读力（可选）：.state/pacing.json
    # ----------------------------------------------------------
    def _read_pacing(self) -> PacingPanel:
        pacing_file = self.project_dir / ".state" / "pacing.json"
        if not pacing_file.exists():
            return PacingPanel(available=False)
        try:
            raw = json.loads(pacing_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            # 文件存在但损坏 → 降级
            return PacingPanel(available=False)
        try:
            from agent.core.pacing_store import Ledger

            # 复用 C 的存储范式（Ledger.from_dict 与 PacingStore.load 等价）
            ledger = Ledger.from_dict(raw)
            return PacingPanel(
                available=True,
                open_debts=[asdict(d) for d in ledger.open_debts],
                cool_density=list(ledger.cool_density),
            )
        except Exception:  # noqa: BLE001
            return PacingPanel(available=False)

    # ----------------------------------------------------------
    # ⑥ 学习沉淀（可选）：.state/learnings/learnings.json
    # ----------------------------------------------------------
    def _read_learnings(self) -> LearningPanel:
        learn_file = self.project_dir / ".state" / "learnings" / "learnings.json"
        if not learn_file.exists():
            return LearningPanel(available=False)
        try:
            json.loads(learn_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return LearningPanel(available=False)
        try:
            from agent.core.learning_store import LearningStore

            items = LearningStore(self.project_dir).load()
            return LearningPanel(available=True, items=[asdict(x) for x in items])
        except Exception:  # noqa: BLE001
            return LearningPanel(available=False)

    # ----------------------------------------------------------
    # ⑦ RAG 索引统计（可选）：.state/rag/index.json
    # ----------------------------------------------------------
    def _read_rag(self) -> RagPanel:
        index_file = self.project_dir / ".state" / "rag" / "index.json"
        if not index_file.exists():
            return RagPanel(available=False, index_count=None)
        try:
            data = json.loads(index_file.read_text(encoding="utf-8"))
            count = len(data.get("chunks", [])) if isinstance(data, dict) else 0
        except (json.JSONDecodeError, OSError, TypeError):
            return RagPanel(available=False, index_count=None)
        return RagPanel(available=True, index_count=count)

    # ----------------------------------------------------------
    # ⑧ 健康摘要（必选）：复用 Doctor.check(ping=False)
    # ----------------------------------------------------------
    def _read_health(self) -> HealthPanel:
        try:
            from agent.core.doctor import Doctor, doctor_to_dict

            checks = Doctor(self.project_dir).check(ping=False)
            healthy = Doctor.is_healthy(checks)
            return HealthPanel(
                available=True,
                healthy=healthy,
                checks=doctor_to_dict(checks),
            )
        except Exception:  # noqa: BLE001 - 极端情况兜底，保证 aggregate 不中断
            return HealthPanel(available=False, healthy=False, checks=[])

    # ============================================================
    # 解析辅助
    # ============================================================
    @staticmethod
    def _section_after(text: str, heading: str) -> str | None:
        """返回 ``## <heading>`` 小节内容（至下一个 ``## `` 标题或 EOF）。

        用于隔离 ``## 节点`` / ``## 边（关系）`` 等小节，避免误纳入 ``## 归档边``。
        """
        pattern = re.compile(r"^##\s+" + re.escape(heading) + r"\s*$", re.MULTILINE)
        m = pattern.search(text)
        if not m:
            return None
        start = m.end()
        next_m = re.search(r"^##\s+", text[start:], re.MULTILINE)
        end = start + next_m.start() if next_m else len(text)
        return text[start:end]

    @staticmethod
    def _is_separator(line: str) -> bool:
        """判断一行是否为 Markdown 表格分隔行（仅由 ``| - :`` 与空格组成）。"""
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if not cells:
            return False
        return all(re.fullmatch(r"[:\-]+", c) is not None for c in cells if c != "")

    @staticmethod
    def _count_table_rows(section: str) -> int:
        """统计一个 Markdown 小节内首个表格的数据行数（跳过表头与分隔行）。"""
        lines = section.splitlines()
        n = len(lines)
        idx = 0
        # 定位首个表头行
        while idx < n and not lines[idx].lstrip().startswith("|"):
            idx += 1
        if idx >= n:
            return 0
        idx += 1  # 跳过表头
        if idx < n and DashboardAggregator._is_separator(lines[idx]):
            idx += 1  # 跳过分隔行
        count = 0
        while idx < n and lines[idx].lstrip().startswith("|"):
            if DashboardAggregator._is_separator(lines[idx]):
                idx += 1
                continue
            count += 1
            idx += 1
        return count
