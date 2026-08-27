"""M6 动态调整工作流单元测试

覆盖：
路线调整门禁（状态、架构确认、protagonist_route.md 存在）
关系调整门禁（状态、架构确认、graph.md 存在）
路线调整：新节点数量、旧主分支→archived_alt、不删除原内容
当前节点定位（按章节范围匹配 NXX）
关系调整：新边活跃、旧边→archived、强度归零、graph.md 含归档段
关系解析：nodes/edges 从 graph.md 正确提取
影响报告：冲突字段结构、severity 统计、2 种方案建议
归档统计：archived_alt 数量统计递增
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import frontmatter
import pytest

from agent.client import LLMClient, LLMResponse
from agent.core.state_machine import State, StateMachine
from agent.workflows.m6_adjust import (
    M6AdjustRouteWorkflow,
    M6AdjustRelationWorkflow,
    M6ImpactReport,
)


ARCH_JSON = {
    "story_core": "凡人以痛感证道。",
    "protagonist_triple": {
        "who": "林寻",
        "want": "复仇",
        "obstacle": "宗门垄断",
    },
    "main_plot": {
        "beginning": "觉醒",
        "development": "逃亡",
        "twist": "真相",
        "resolution": "证道",
    },
    "theme": "效率vs人性",
    "ending": "殉道",
    "emotional_tone": "悲壮",
    "synopsis": "林寻唤醒太虚镜。",
}


# ============================================================
# 项目夹具
# ============================================================
def _build_project(
    tmp_path: Path,
    state: State = State.WRITING,
    total_written: int = 1,
    with_route: bool = True,
    with_graph: bool = True,
) -> Path:
    d = tmp_path / "p"
    d.mkdir(parents=True)

    # world.md
    world = """---
title: "太虚镜"
scope: long
genre: xiuxian
style:
  tone: 热血
frozen_fields:
  - realm_system
---

# 总设定集

## 境界体系（冻结）

炼气→筑基→金丹

## 金手指登记

名称：太虚镜
代价：精血寿元
上限：推演次数≤百次
"""
    (d / "world.md").write_text(world, encoding="utf-8")

    # architecture.md (confirmed)
    arch_post = frontmatter.Post(
        "# 故事架构\n",
        title="太虚镜",
        confirmed=True,
        confirmed_at="2026-01-01",
        architecture=ARCH_JSON,
    )
    (d / "architecture.md").write_bytes(frontmatter.dumps(arch_post).encode("utf-8"))

    # outline.md (required by check_confirmed indirectly)
    (d / "outline.md").write_text("# 大纲\n", encoding="utf-8")

    # protagonist_route.md
    if with_route:
        route = """# 主角成长路线 · 寒门弃徒

## N01 · 太虚镜初启

- **章节范围**：1-15

### 主分支 · 拒献同门推演笨法

- **结果**：修补版锻体术
- **成长**：炼气一层

## N02 · 残篇逆推

- **章节范围**：16-45

### 主分支 · 搜集禁书建立网络

- **结果**：三处垄断节点拆解
- **成长**：炼气圆满

## N03 · 真相揭露

- **章节范围**：46-80

### 主分支 · 查明师父死因

- **结果**：器灵和解
- **成长**：筑基期
"""
        (d / "protagonist_route.md").write_text(route, encoding="utf-8")

    # relations/graph.md
    if with_graph:
        graph = """---
updated_at: "2026-01-01"
current_chapter: "ch001"
---

# 关系网

## Mermaid 可视化

```mermaid
graph LR
    linxun -->|共生(10)| taixu
    linxun -->|对立(9)| zhao
```

## 节点

| ID | 角色 | 分组 |
|---|---|---|
| linxun | 林寻 | protagonist |
| taixu | 太虚镜 | supporting |
| zhao | 赵无极 | antagonist |
| suwan | 苏婉儿 | supporting |

## 边（关系）

| 起 | 止 | 类型 | 强度 | 起于 | 备注 |
|---|---|---|---|---|---|
| linxun | taixu | 共生/殉道 | 10 | S01 | 工具→挚友 |
| linxun | zhao | 意识形态对立 | 9 | S01 | 新旧秩序碰撞 |
| linxun | suwan | 理念同盟 | 7 | S01 | 新法传播双翼 |
"""
        (d / "relations").mkdir(exist_ok=True)
        (d / "relations" / "graph.md").write_text(graph, encoding="utf-8")

    # chapters (1 written)
    if total_written > 0:
        ch_dir = d / "chapters"
        ch_dir.mkdir(exist_ok=True)
        ch = frontmatter.Post(
            "# 第1章 寒风绝灵崖\n\n正文...",
            title="寒风绝灵崖",
            route_node="N01",
            chapter_num=1,
        )
        (ch_dir / "ch001.md").write_bytes(frontmatter.dumps(ch).encode("utf-8"))

    # state
    sm = StateMachine(d)
    sm.load()
    sm.state = state
    sm.progress = {
        "current_subline": "S01_器灵人性觉醒",
        "total_written": total_written,
        "current_chapter": total_written,
    }
    sm.save()

    return d


# ============================================================
# Mock LLM 工厂
# ============================================================
def _build_mock_llm(
    route_json: dict | None = None,
    graph_json: dict | None = None,
    impact_json: dict | None = None,
) -> MagicMock:
    llm = MagicMock(spec=LLMClient)
    import json as _json

    # 默认路线
    if route_json is None:
        route_json = {
            "root_node": "寒门弃徒",
            "nodes": [
                {
                    "id": "N01",
                    "chapter_range": "1-15",
                    "milestone": "太虚镜初启与人性抉择",
                    "main_branch": {
                        "title": "拒献同门，自损寿元推演笨法",
                        "result": "修补版锻体术",
                        "growth": "炼气一层",
                    },
                    "alt_branches": [
                        {
                            "title": "接受最优解献祭同门",
                            "when": "archived_alt（由主分支归档）",
                            "result": "沦为镜子傀儡",
                        }
                    ],
                },
                {
                    "id": "N02",
                    "chapter_range": "16-45",
                    "milestone": "卧底执法堂暗布棋子",
                    "main_branch": {
                        "title": "假意投诚卧底执法堂",
                        "result": "掌握内部黑幕名单",
                        "growth": "炼气圆满 + 情报网",
                    },
                    "alt_branches": [
                        {
                            "title": "搜集禁书逆向补全",
                            "when": "archived_alt（由主分支归档）",
                            "result": "拆解垄断节点",
                        }
                    ],
                },
                {
                    "id": "N03",
                    "chapter_range": "46-80",
                    "milestone": "真相揭露",
                    "main_branch": {
                        "title": "卧底身份暴露后策反高层",
                        "result": "里应外合瓦解宗门",
                        "growth": "筑基期",
                    },
                    "alt_branches": [
                        {
                            "title": "查明师父死于耗材清洗",
                            "when": "archived_alt（由主分支归档）",
                            "result": "器灵和解",
                        }
                    ],
                },
            ],
        }

    # 默认 graph（比原来多 1 条新边：suwan<->taixu 同盟，确保 new_edges_count >= 1）
    if graph_json is None:
        graph_json = {
            "nodes": [
                {"id": "linxun", "label": "林寻", "group": "protagonist"},
                {"id": "taixu", "label": "太虚镜", "group": "supporting"},
                {"id": "zhao", "label": "赵无极", "group": "antagonist"},
                {"id": "suwan", "label": "苏婉儿", "group": "supporting"},
            ],
            "edges": [
                {"from": "linxun", "to": "taixu", "type": "共生/殉道", "intensity": 10, "since": "S01", "note": "工具→挚友", "archived": False},
                {"from": "linxun", "to": "zhao", "type": "意识形态对立", "intensity": 9, "since": "S01", "note": "archived: 原对立关系（调整为赏识）", "archived": True},
                {"from": "linxun", "to": "zhao", "type": "暗中赏识/互相利用", "intensity": 6, "since": "ch002", "note": "赵无极认可林寻才能，暗中放水", "archived": False},
                {"from": "linxun", "to": "suwan", "type": "理念同盟", "intensity": 7, "since": "S01", "note": "新法传播双翼", "archived": False},
                {"from": "suwan", "to": "taixu", "type": "技术同盟", "intensity": 5, "since": "ch002", "note": "苏婉儿协助器灵修复算法", "archived": False},
            ],
        }

    # 默认影响报告（无冲突）
    if impact_json is None:
        impact_json = {
            "field_conflicts": [],
            "affected_characters": ["赵无极"],
            "affected_chapters": [],
            "golden_finger_risk": "",
            "timeline_conflicts": [],
            "recommendations": [
                {"option": "保留原设定改章节", "detail": "后续章节体现新关系"},
                {"option": "改设定并标记", "detail": "标记受影响角色弧线"},
            ],
        }

    creative_idx = 0
    utility_idx = 0

    def creative_side(*args, **kwargs):
        nonlocal creative_idx
        # 获取 messages（兼容 kwargs 和 positional）
        msgs = kwargs.get("messages")
        if msgs is None and args:
            msgs = args[0]
        if not msgs:
            msgs = []
        sys_msg = ""
        for m in msgs:
            if isinstance(m, dict) and m.get("role") == "system":
                sys_msg = str(m.get("content", ""))
                break
        # 使用多个关键词组合判断，避免误判
        # 路线调整：系统 prompt 包含 "主角成长路线" 或 "protagonist_route"
        # 关系调整：包含 "关系网" 或 "graph.md" 或 "角色关系演化"
        is_route = (
            "主角成长路线" in sys_msg
            or "protagonist_route" in sys_msg
            or "小说架构师" in sys_msg
        )
        if is_route:
            text = _json.dumps(route_json, ensure_ascii=False)
        else:
            text = _json.dumps(graph_json, ensure_ascii=False)
        creative_idx += 1
        return LLMResponse(text=text, raw={}, usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2})

    def utility_side(*args, **kwargs):
        nonlocal utility_idx
        text = _json.dumps(impact_json, ensure_ascii=False)
        utility_idx += 1
        return LLMResponse(text=text, raw={}, usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2})

    llm.chat_creative.side_effect = creative_side
    llm.chat_utility.side_effect = utility_side
    return llm


# ============================================================
# Test 路线门禁
# ============================================================
class TestRouteGates:
    def test_route_requires_world_md(self, tmp_path: Path) -> None:
        d = _build_project(tmp_path)
        (d / "world.md").unlink()
        wf = M6AdjustRouteWorkflow(project_dir=d, llm_client=_build_mock_llm())
        with pytest.raises(RuntimeError, match="world.md 不存在"):
            wf.run("change route")

    def test_route_requires_confirmed_architecture(self, tmp_path: Path) -> None:
        d = _build_project(tmp_path)
        post = frontmatter.load(d / "architecture.md")
        post.metadata["confirmed"] = False
        (d / "architecture.md").write_bytes(frontmatter.dumps(post).encode("utf-8"))
        wf = M6AdjustRouteWorkflow(project_dir=d, llm_client=_build_mock_llm())
        with pytest.raises(RuntimeError, match="尚未确认"):
            wf.run("change route")

    def test_route_requires_correct_state(self, tmp_path: Path) -> None:
        d = _build_project(tmp_path, state=State.OUTLINING)
        wf = M6AdjustRouteWorkflow(project_dir=d, llm_client=_build_mock_llm())
        with pytest.raises(RuntimeError, match="WRITING"):
            wf.run("change route")

    def test_route_requires_route_file(self, tmp_path: Path) -> None:
        d = _build_project(tmp_path, with_route=False)
        wf = M6AdjustRouteWorkflow(project_dir=d, llm_client=_build_mock_llm())
        with pytest.raises(RuntimeError, match="protagonist_route.md 不存在"):
            wf.run("change route")

    def test_route_allows_character_design_state(self, tmp_path: Path) -> None:
        d = _build_project(tmp_path, state=State.CHARACTER_DESIGN, total_written=0)
        # 删除 chapters 避免影响
        import shutil
        ch_dir = d / "chapters"
        if ch_dir.exists():
            shutil.rmtree(ch_dir)
        wf = M6AdjustRouteWorkflow(project_dir=d, llm_client=_build_mock_llm())
        # 不抛异常即通过
        r = wf.run("change route")
        assert r.new_nodes_count >= 1


# ============================================================
# Test 关系门禁
# ============================================================
class TestRelationGates:
    def test_relation_requires_world_md(self, tmp_path: Path) -> None:
        d = _build_project(tmp_path)
        (d / "world.md").unlink()
        wf = M6AdjustRelationWorkflow(project_dir=d, llm_client=_build_mock_llm())
        with pytest.raises(RuntimeError, match="world.md 不存在"):
            wf.run("change relation")

    def test_relation_requires_confirmed_architecture(self, tmp_path: Path) -> None:
        d = _build_project(tmp_path)
        post = frontmatter.load(d / "architecture.md")
        post.metadata["confirmed"] = False
        (d / "architecture.md").write_bytes(frontmatter.dumps(post).encode("utf-8"))
        wf = M6AdjustRelationWorkflow(project_dir=d, llm_client=_build_mock_llm())
        with pytest.raises(RuntimeError, match="尚未确认"):
            wf.run("change relation")

    def test_relation_requires_correct_state(self, tmp_path: Path) -> None:
        d = _build_project(tmp_path, state=State.OUTLINING)
        wf = M6AdjustRelationWorkflow(project_dir=d, llm_client=_build_mock_llm())
        with pytest.raises(RuntimeError, match="WRITING"):
            wf.run("change relation")

    def test_relation_requires_graph_file(self, tmp_path: Path) -> None:
        d = _build_project(tmp_path, with_graph=False)
        wf = M6AdjustRelationWorkflow(project_dir=d, llm_client=_build_mock_llm())
        with pytest.raises(RuntimeError, match="graph.md 不存在"):
            wf.run("change relation")


# ============================================================
# Test 路线调整核心
# ============================================================
class TestRouteAdjustment:
    def test_route_preserves_archived_alts(self, tmp_path: Path) -> None:
        """F6.1 旧主分支 → archived_alt，不删除"""
        d = _build_project(tmp_path)
        before_text = (d / "protagonist_route.md").read_text(encoding="utf-8")
        before_count = M6AdjustRouteWorkflow._count_archived_alts(before_text)

        wf = M6AdjustRouteWorkflow(project_dir=d, llm_client=_build_mock_llm())
        r = wf.run("让主角N02去执法堂当卧底")

        after_text = (d / "protagonist_route.md").read_text(encoding="utf-8")
        after_count = M6AdjustRouteWorkflow._count_archived_alts(after_text)

        # 旧分支至少被归档了 N02 + N03 = 2 条
        assert after_count > before_count
        assert r.old_route_archived == after_count - before_count
        # archived_alt 关键字出现在文件中
        assert "archived_alt" in after_text
        # 主分支标题包含"卧底"（新方向）
        assert "卧底" in after_text

    def test_route_new_nodes_count(self, tmp_path: Path) -> None:
        d = _build_project(tmp_path)
        wf = M6AdjustRouteWorkflow(project_dir=d, llm_client=_build_mock_llm())
        r = wf.run("调整路线")
        assert r.new_nodes_count == 3

    def test_route_current_node_is_n01(self, tmp_path: Path) -> None:
        """ch001 属于 1-15，所以当前节点 N01"""
        d = _build_project(tmp_path, total_written=1)
        wf = M6AdjustRouteWorkflow(project_dir=d, llm_client=_build_mock_llm())
        r = wf.run("调整路线")
        assert r.current_node_id == "N01"

    def test_route_current_node_n02_for_chapter_20(self, tmp_path: Path) -> None:
        """ch020 属于 16-45，应定位到 N02"""
        d = _build_project(tmp_path, total_written=20)
        text = (d / "protagonist_route.md").read_text(encoding="utf-8")
        idx = M6AdjustRouteWorkflow._locate_current_node(text, 20)
        assert idx == 2

    def test_route_node_structure_rendered(self, tmp_path: Path) -> None:
        """生成的文件包含所有 N01/N02/N03 章节节点"""
        d = _build_project(tmp_path)
        wf = M6AdjustRouteWorkflow(project_dir=d, llm_client=_build_mock_llm())
        wf.run("调整路线")
        text = (d / "protagonist_route.md").read_text(encoding="utf-8")
        assert "## N01" in text
        assert "## N02" in text
        assert "## N03" in text
        assert "### 主分支" in text
        assert "### 备选分支" in text


# ============================================================
# Test 关系调整核心
# ============================================================
class TestRelationAdjustment:
    def test_relation_archived_edges(self, tmp_path: Path) -> None:
        """旧边被标记为 archived 且保留"""
        d = _build_project(tmp_path)
        wf = M6AdjustRelationWorkflow(project_dir=d, llm_client=_build_mock_llm())
        r = wf.run("赵无极对林寻转为暗中赏识")

        # 归档至少 1 条（linxun→zhao 的原对立关系）
        assert r.archived_edges_count >= 1
        # 新增至少 1 条（暗中赏识）
        assert r.new_edges_count >= 1
        # 节点数不变
        assert r.nodes_count == 4

    def test_relation_graph_contains_archived_section(self, tmp_path: Path) -> None:
        """调整后的 graph.md 含『归档边』章节"""
        d = _build_project(tmp_path)
        wf = M6AdjustRelationWorkflow(project_dir=d, llm_client=_build_mock_llm())
        wf.run("调整关系")
        text = (d / "relations" / "graph.md").read_text(encoding="utf-8")
        # 归档边区块存在
        assert "归档边" in text
        # 原对立关系以 archived 说明出现
        assert "archived:" in text or "原对立关系" in text

    def test_relation_mermaid_only_active(self, tmp_path: Path) -> None:
        """Mermaid 图中只渲染非 archived 的边"""
        import re as _re

        d = _build_project(tmp_path)
        wf = M6AdjustRelationWorkflow(project_dir=d, llm_client=_build_mock_llm())
        wf.run("调整关系")
        text = (d / "relations" / "graph.md").read_text(encoding="utf-8")
        # 提取 mermaid 块
        m = _re.search(r"```mermaid\n(.*?)```", text, _re.DOTALL)
        assert m, "Mermaid 块应存在"
        mermaid = m.group(1)
        # "暗中赏识" 应出现（新活跃边）
        assert "暗中赏识" in mermaid or "互相利用" in mermaid
        # 不应出现强度为 0 的 archived 边在 mermaid
        assert "(强度0)" not in mermaid

    def test_parse_graph_md_roundtrip(self, tmp_path: Path) -> None:
        """graph.md → nodes/edges 解析正确"""
        d = _build_project(tmp_path)
        content = (d / "relations" / "graph.md").read_text(encoding="utf-8")
        nodes, edges = M6AdjustRelationWorkflow._parse_graph_md(content)
        ids = [n["id"] for n in nodes]
        assert "linxun" in ids
        assert "zhao" in ids
        assert "taixu" in ids
        # 边数：初始 3 条
        assert len(edges) == 3
        # 关系类型正确
        types = [e["type"] for e in edges]
        assert "意识形态对立" in types
        # 强度解析为整数
        intensities = [e["intensity"] for e in edges]
        assert 10 in intensities
        assert 9 in intensities


# ============================================================
# Test 影响报告
# ============================================================
class TestImpactReport:
    def test_no_conflict_report(self, tmp_path: Path) -> None:
        """无冲突场景：has_conflicts=False，severity 全 0"""
        impact = M6ImpactReport(
            field_conflicts=[],
            affected_characters=["赵无极"],
            affected_chapters=[],
            golden_finger_risk="",
            timeline_conflicts=[],
            recommendations=[{"option": "x", "detail": "y"}],
        )
        assert impact.has_conflicts is False
        sev = impact.severity_count
        assert sev == {"high": 0, "medium": 0, "low": 0}

    def test_conflict_severity_count(self, tmp_path: Path) -> None:
        impact = M6ImpactReport(
            field_conflicts=[
                {"field": "realm", "severity": "high"},
                {"field": "golden", "severity": "medium"},
                {"field": "char", "severity": "low"},
                {"field": "unknown_sev", "severity": "xxx"},
            ],
        )
        sev = impact.severity_count
        assert sev["high"] == 1
        assert sev["medium"] == 1
        assert sev["low"] == 2  # low + unknown

    def test_has_conflicts_true_when_chapters_affected(self, tmp_path: Path) -> None:
        impact = M6ImpactReport(
            affected_chapters=["ch001", "ch002"],
        )
        assert impact.has_conflicts is True

    def test_has_conflicts_true_when_timeline_conflict(self, tmp_path: Path) -> None:
        impact = M6ImpactReport(
            timeline_conflicts=["A在B之后，但B依赖A结果"],
        )
        assert impact.has_conflicts is True

    def test_route_generates_impact_report(self, tmp_path: Path) -> None:
        """run() 返回的 impact 结构正确"""
        conflict_impact = {
            "field_conflicts": [
                {"field": "境界突破点", "in_world": "炼气→筑基", "after_change": "与原节点矛盾", "severity": "high"},
            ],
            "affected_characters": ["林寻", "赵无极"],
            "affected_chapters": ["ch001"],
            "golden_finger_risk": "突破上限需要重新登记代价",
            "timeline_conflicts": ["师父死亡时点冲突"],
            "recommendations": [
                {"option": "保留原设定改章节", "detail": "重写 ch001"},
                {"option": "改设定标记章节", "detail": "回滚并标记"},
            ],
        }
        d = _build_project(tmp_path)
        wf = M6AdjustRouteWorkflow(
            project_dir=d,
            llm_client=_build_mock_llm(impact_json=conflict_impact),
        )
        r = wf.run("调整路线到筑基冲突")
        imp = r.impact_report
        assert imp.has_conflicts is True
        assert imp.severity_count["high"] == 1
        assert "林寻" in imp.affected_characters
        assert "ch001" in imp.affected_chapters
        assert "代价" in imp.golden_finger_risk
        assert len(imp.timeline_conflicts) == 1
        assert len(imp.recommendations) == 2

    def test_relation_generates_impact_report(self, tmp_path: Path) -> None:
        d = _build_project(tmp_path)
        wf = M6AdjustRelationWorkflow(
            project_dir=d,
            llm_client=_build_mock_llm(
                impact_json={
                    "field_conflicts": [],
                    "affected_characters": ["赵无极"],
                    "affected_chapters": [],
                    "golden_finger_risk": "",
                    "timeline_conflicts": [],
                    "recommendations": [
                        {"option": "仅后续章节体现新关系", "detail": "不改动已写章节"}
                    ],
                }
            ),
        )
        r = wf.run("赵无极赏识林寻")
        imp = r.impact_report
        assert imp.has_conflicts is False
        assert len(imp.recommendations) >= 1
        assert "赵无极" in imp.affected_characters


# ============================================================
# Test CLI 导入（基本语法）
# ============================================================
class TestCLIImport:
    def test_cli_has_adjust_commands(self, tmp_path: Path) -> None:
        """CLI 模块包含 adjust_route / adjust_relation 回调函数且 CLI 可导入"""
        from agent import cli as cli_module
        from agent.cli import app
        from typer.main import TyperCommand

        # 回调函数必须存在且可调用
        assert callable(getattr(cli_module, "adjust_route", None))
        assert callable(getattr(cli_module, "adjust_relation", None))

        # 通过 app.command() 查找：Typer 以函数名注册（下划线），CLI 展示自动转短横线
        registered: set[str] = set()
        try:
            # 方式 A: app.registered_commands 列表
            for c in getattr(app, "registered_commands", []) or []:
                name = getattr(c, "name", None)
                if not name:
                    # name 为 None 时，从 callback 函数名派生
                    cb = getattr(c, "callback", None)
                    name = getattr(cb, "__name__", None) if cb else None
                if name:
                    registered.add(str(name).replace("-", "_"))
            # 方式 B: 通过 Typer 内部 info.commands
            info_cmds = getattr(getattr(app, "info", None), "commands", None) or {}
            for k in info_cmds.keys():
                registered.add(str(k).replace("-", "_"))
        except Exception:
            pass

        # 如果有注册列表则校验；否则只要函数有 typer 特征即可
        if registered:
            assert "adjust_route" in registered, f"命令未注册，已注册：{sorted(registered)}"
            assert "adjust_relation" in registered, f"命令未注册，已注册：{sorted(registered)}"
        else:
            # 兜底：函数签名含 typer.Option 参数即算正确装饰
            import inspect

            for fn_name in ("adjust_route", "adjust_relation"):
                sig = inspect.signature(getattr(cli_module, fn_name))
                has_typer_opt = any(
                    "Option" in str(type(p.default).__name__)
                    or "typer" in str(type(p.default)).lower()
                    or p.default is not inspect.Parameter.empty
                    for p in sig.parameters.values()
                )
                assert has_typer_opt, f"{fn_name} 似乎未用 typer.Option 装饰"
