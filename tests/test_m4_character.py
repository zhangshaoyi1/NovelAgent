"""M4 角色路线与关系网工作流单元测试

覆盖：
- 门禁（状态、架构确认、world.md 不存在、outline.md 不存在）
- 正确流程：产出 5 类文件、状态转换 CHARACTER_DESIGN
- 角色数量：protagonist/antagonist/mentor/supporting 四类齐全
- 关系网：nodes/edges 都渲染、Mermaid 块存在
- 伏笔表：统计正确
- 金手指登记：frozen 字段、阶段数
- 可重复运行不崩（CHARACTER_DESIGN 再跑一次）
- 反派动机合理性自检字段必填（含 antagonist 则 motivation_check 非空）
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import frontmatter
import pytest

from agent.client import LLMClient, LLMResponse
from agent.core.engine.state_machine import Event, State, StateMachine
from agent.workflows.m14_architecture import M14ArchitectureWorkflow
from agent.workflows.m4_character import M4CharacterWorkflow


# ============================================================
# 假数据：架构
# ============================================================
ARCH_JSON = {
    "story_core": "凡人以痛感证道，器灵以殉道成全文明。",
    "protagonist_triple": {
        "who": "灵根残缺的寒门少年林寻",
        "want": "为师父复仇并找到让人人可修仙的方法",
        "obstacle": "宗门垄断真法、太虚镜的冷酷理性逻辑",
    },
    "main_plot": {
        "beginning": "林寻被弃濒死唤醒太虚镜",
        "development": "逃亡中推演功法并组建寒门势力",
        "twist": "师父之死实乃宗门屠杀",
        "resolution": "万宗大典燃尽本源，镜崩碎承道",
    },
    "sublines_preview": "S01器灵博弈 / S02寒门觉醒 / S03古史解密 / S04体制困局 / S05真法铸成",
    "conflict_nodes": "宗门围剿、器灵反噬、师父亲缘真相",
    "theme": "效率vs人性，知识公有vs私有",
    "ending": "器灵殉道，新秩序建立，林寻肉身消逝但道统长存",
    "emotional_tone": "悲壮热血，理智与情感拉锯",
    "synopsis": "灵根残缺少年林寻唤醒太虚镜，与冷酷器灵博弈寻找人之道。",
}

SUBLINES = [
    {
        "subline_name": "器灵人性觉醒",
        "goal": "太虚镜从工具进化为独立生命体",
        "pressure_curve": {"setup": "1-50", "conflict": "51-200", "climax": "450-500", "relief": "501-520"},
        "key_events": [{"event": "器灵首次选择非最优解", "at": "S01/ch020"}],
        "constraints": ["镜的进化必须由情感触发而非计算"],
        "mainline_relation": "直接推动真法铸造精神内核",
        "tags": ["器灵成长", "理性vs情感"],
    },
    {
        "subline_name": "寒门众生觉醒",
        "goal": "寒门修士从耗材变为共建者",
        "pressure_curve": {"setup": "30-80", "conflict": "81-250", "climax": "380-420", "relief": "421-430"},
        "key_events": [{"event": "首次公开推演功法", "at": "S02/ch080"}],
        "constraints": ["民众觉醒必须来自行动而非宣传"],
        "mainline_relation": "提供组织基础",
        "tags": ["阶层反抗"],
    },
]

M4_LLM_OUTPUT = {
    "protagonist_route": {
        "root_node": "林寻·寒门弃徒",
        "nodes": [
            {
                "id": "N01",
                "chapter_range": "1-20",
                "milestone": "太虚镜初醒，识破奴役暗门",
                "main_branch": {
                    "title": "逃亡推演",
                    "result": "以精血破宗门追杀，逃入凡城",
                    "growth": "凝气三层 + 推演凡级功法",
                },
                "alt_branches": [
                    {"title": "投诚宗门换取资源", "when": "师姐劝降", "result": "提前暴露镜"}
                ],
            },
            {
                "id": "N02",
                "chapter_range": "21-80",
                "milestone": "组建寒门互助盟",
                "main_branch": {
                    "title": "布道凡城",
                    "result": "公开推演筑基残篇",
                    "growth": "筑基初期 + 公开威望",
                },
                "alt_branches": [],
            },
        ],
    },
    "characters": [
        {
            "name": "林寻",
            "role": "protagonist",
            "identity": "太虚门弃徒，太虚镜持有者",
            "faction": "寒门互助盟",
            "realm": "凝气三层",
            "first_appearance": "S01",
            "core_motivation": "让人人可修仙，打破宗门知识垄断",
            "surface_goal": "为师父复仇",
            "deep_goal": "以自己的痛证明人之尊严",
            "secret": "真实身份是上古宗门遗孤",
            "arc": {"start": "自卑愤怒的弃徒", "end": "以身证道的殉道者"},
            "language_fingerprint": {
                "catchphrase": "我选笨路。",
                "sentence_style": "短句、斩钉截铁、少修饰",
                "vocabulary": "喜用反问、用修仙术语作比喻",
                "banned_words": ["突然", "微微一笑"],
            },
            "relations": "- 对太虚镜：由恨到惺惺相惜\n- 对沈砚：师兄弟之情",
            "validation": {"motivation_check": "", "appearance_interval": 1},
        },
        {
            "name": "太虚镜·玄澈",
            "role": "mentor",
            "identity": "上古至宝的人格化身",
            "faction": "中立",
            "realm": "超越境界",
            "first_appearance": "S01",
            "core_motivation": "验证效率至上的最优解",
            "surface_goal": "最大化宿主存活率",
            "deep_goal": "找到比效率更高的解：牺牲",
            "secret": "自己毁灭就是最终方案",
            "arc": {"start": "冷酷计算器灵", "end": "理解情感自我崩碎"},
            "language_fingerprint": {
                "catchphrase": "计算完毕，存活率 3.14%。",
                "sentence_style": "数据先行、报告体",
                "vocabulary": "概率、代价、最优、解",
                "banned_words": ["喜欢", "爱"],
            },
            "relations": "- 对林寻：宿主→伙伴→继承者",
            "validation": {"motivation_check": "", "appearance_interval": 1},
        },
        {
            "name": "裴玄机",
            "role": "antagonist",
            "identity": "万宗盟盟主，化神期",
            "faction": "万宗盟",
            "realm": "化神",
            "first_appearance": "S04",
            "core_motivation": "维持秩序以延缓末法，哪怕以寒门为薪柴",
            "surface_goal": "消灭一切‘非法传承’",
            "deep_goal": "掩盖自己年轻时因理念动摇亲手弑师的真相",
            "secret": "当年弑师是为了抢灵脉续命，不是宗门说的护道",
            "arc": {"start": "秩序化神至尊", "end": "秩序崩塌下的孤家寡人"},
            "language_fingerprint": {
                "catchphrase": "此乃必要之恶。",
                "sentence_style": "双关、典故、居高临下",
                "vocabulary": "天道、苍生、大棋、牺牲",
                "banned_words": ["错了"],
            },
            "relations": "- 对林寻：曾经同情过的‘另一个自己’ → 必须抹除的异端",
            "validation": {
                "motivation_check": "裴玄机曾亲身经历末法早期的宗门混战，亲眼见无秩序比有秩序死更多人，因此他选择‘用规则守住大多数’。他并非恨寒门，而是认为寒门反抗只会加速末法崩溃，导致所有人一起死。他的恶来自‘大局理性被时间腐化’，而非个人贪欲。",
                "appearance_interval": "",
            },
        },
        {
            "name": "沈砚",
            "role": "supporting",
            "identity": "林寻同门师兄，实则宗门暗子",
            "faction": "太虚门",
            "realm": "筑基后期",
            "first_appearance": "S01",
            "core_motivation": "在‘恩’与‘义’之间找自己的立场",
            "surface_goal": "抓回林寻立功",
            "deep_goal": "向林寻证明，他选的‘笨路’不是错",
            "secret": "妹妹还在宗门做人质",
            "arc": {"start": "听话的宗门犬", "end": "为寒门断后的义士"},
            "language_fingerprint": {
                "catchphrase": "……这次是我赢。",
                "sentence_style": "省略号多、话少但字重",
                "vocabulary": "隐忍派，克制但关键时刻爆发",
                "banned_words": ["随便"],
            },
            "relations": "- 对林寻：既是猎人也是兄弟\n- 对裴玄机：厌恶的主子",
            "validation": {"motivation_check": "", "appearance_interval": 4},
        },
        {
            "name": "苏月白",
            "role": "supporting",
            "identity": "凡城女医，懂草木药性",
            "faction": "寒门互助盟",
            "realm": "凡人",
            "first_appearance": "S02",
            "core_motivation": "用凡人之力证明‘不是修士也能护人’",
            "surface_goal": "治好娘亲的寒毒",
            "deep_goal": "让修行者不再把凡人当耗材",
            "secret": "她是裴玄机丢弃的私生女",
            "arc": {"start": "唯唯诺诺小医女", "end": "凡城城主，寒门之母"},
            "language_fingerprint": {
                "catchphrase": "等我煎碗药。",
                "sentence_style": "比喻多取草木，温柔但坚韧",
                "vocabulary": "药草词汇、拟人化比喻",
                "banned_words": ["我不行"],
            },
            "relations": "- 对林寻：先同情后并肩\n- 对裴玄机：血亲但不共戴天",
            "validation": {"motivation_check": "", "appearance_interval": 6},
        },
    ],
    "relation_graph": {
        "nodes": [
            {"id": "A", "label": "林寻", "group": "protagonist"},
            {"id": "B", "label": "玄澈", "group": "mentor"},
            {"id": "C", "label": "裴玄机", "group": "antagonist"},
            {"id": "D", "label": "沈砚", "group": "supporting"},
            {"id": "E", "label": "苏月白", "group": "supporting"},
        ],
        "edges": [
            {"from": "A", "to": "B", "type": "宿主-器灵", "intensity": 9, "since": "S01", "note": "从博弈到默契"},
            {"from": "A", "to": "C", "type": "对抗", "intensity": 10, "since": "S04", "note": "理念冲突"},
            {"from": "A", "to": "D", "type": "师兄弟", "intensity": 8, "since": "S01", "note": "相爱相杀"},
            {"from": "A", "to": "E", "type": "战友", "intensity": 7, "since": "S02", "note": "凡城相识"},
            {"from": "C", "to": "D", "type": "掌控", "intensity": 8, "since": "S01", "note": "沈砚妹妹为质"},
            {"from": "C", "to": "E", "type": "血亲-被弃", "intensity": 6, "since": "S05", "note": "私生女关系"},
        ],
    },
    "foreshadows": [
        {"id": "F-01", "content": "太虚镜内刻有一串林寻娘亲的生辰八字", "planted_at": "S01/ch003", "expected_resolve": "S03/ch150 身份揭秘", "state": "未埋", "related_characters": "林寻, 玄澈"},
        {"id": "F-02", "content": "裴玄机右手有一块与沈砚相同的胎记", "planted_at": "S04/ch320", "expected_resolve": "S04/ch400 师徒实为父子揭露", "state": "未埋", "related_characters": "裴玄机, 沈砚"},
        {"id": "F-03", "content": "苏月白的寒毒只有太虚门真法能解", "planted_at": "S02/ch070", "expected_resolve": "S05/ch480 普世真法铸成时自愈", "state": "未埋", "related_characters": "苏月白"},
        {"id": "F-04", "content": "玄澈曾提过‘上一位宿主也是弃徒’", "planted_at": "S01/ch010", "expected_resolve": "S03/ch200 上古宗门毁灭真相", "state": "未埋", "related_characters": "玄澈"},
        {"id": "F-05", "content": "万宗大典下埋着上古崩碎的另一半镜", "planted_at": "S01/ch005", "expected_resolve": "S05/ch490 镜崩碎时合体", "state": "未埋", "related_characters": "林寻, 玄澈, 裴玄机"},
        {"id": "F-06", "content": "裴玄机年轻时曾想公开真法", "planted_at": "S04/ch330", "expected_resolve": "S05/ch495 临死前交付手稿", "state": "未埋", "related_characters": "裴玄机"},
    ],
    "golden_finger_registration": {
        "name": "太虚镜",
        "type": "推演型辅助至宝",
        "growth_stages": [
            {"stage": "1（凝气/筑基）", "ability": "推演凡级功法，提供战斗最优解", "cost": "1 次推演 = 1 日寿元精血"},
            {"stage": "2（金丹/元婴）", "ability": "推演灵级功法，可推演 3 步未来", "cost": "1 次 = 10 日寿元 + 剧痛"},
            {"stage": "3（化神/大乘）", "ability": "推演真级功法，承载普世真法", "cost": "器灵寿元耗尽，崩碎"},
        ],
        "cost_rules": "推演结果越偏离‘冷酷最优解’，代价越高；选择笨路则以林寻寿元代镜之寿元。",
        "hard_limits": "不可直接赐予修为、不可起死回生、推演上限为‘当前世界真法总量’。",
        "unlock_conditions": "阶段 2：林寻筑基 + 玄澈完成一次情感抉择；阶段 3：万宗大典，镜与埋于地下的另一半合体。",
    },
}


# ============================================================
# 夹具
# ============================================================
def _build_mock_llm(output: dict) -> MagicMock:
    """MagicMock + chat_creative.return_value = LLMResponse(json)"""
    import json as _json

    llm = MagicMock(spec=LLMClient)
    llm.chat_creative.return_value = LLMResponse(
        text=_json.dumps(output, ensure_ascii=False),
        raw={"choices": [{"message": {"content": _json.dumps(output, ensure_ascii=False)}}]},
        usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    )
    return llm


def _build_mock_llm_custom(text: str) -> MagicMock:
    llm = MagicMock(spec=LLMClient)
    llm.chat_creative.return_value = LLMResponse(
        text=text,
        raw={"choices": [{"message": {"content": text}}]},
        usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    )
    return llm


def _build_minimal_project(tmp_path: Path, state: State = State.OUTLINING) -> Path:
    """搭建最小可用项目（含 world.md + architecture.md(confirmed) + outline.md）"""
    d = tmp_path / "p"
    d.mkdir(parents=True)

    # world.md
    world = """---
title: "太虚镜"
scope: 长篇
genre: 修仙
style:
  tone: 悲壮热血
---

# 世界设定

## 金手指登记

- 名称：太虚镜
- 类型：推演型至宝
- 代价：精血寿元
"""
    (d / "world.md").write_text(world, encoding="utf-8")

    # architecture.md（confirmed + architecture JSON in frontmatter）
    arch_md = frontmatter.Post(
        "# 故事架构\n\n已确认内容...",
        title="太虚镜",
        confirmed=True,
        confirmed_at="2026-01-01",
        version=1,
        architecture=ARCH_JSON,
    )
    (d / "architecture.md").write_bytes(frontmatter.dumps(arch_md).encode("utf-8"))

    # outline.md
    outline_md = frontmatter.Post(
        "# 故事大纲\n\n## 故事简介\n\n林寻唤醒太虚镜...\n\n## 顶层支线任务列表\n\n表格略\n",
        title="太虚镜",
        synopsis="林寻唤醒太虚镜...",
        sublines=SUBLINES,
    )
    (d / "outline.md").write_bytes(frontmatter.dumps(outline_md).encode("utf-8"))

    # state
    sm = StateMachine(d)
    sm.load()
    if state != State.INIT:
        sm.state = state
        sm.save()

    return d


# ============================================================
# 测试：门禁
# ============================================================
class TestGates:
    def test_m4_requires_world_md(self, tmp_path: Path) -> None:
        d = _build_minimal_project(tmp_path)
        (d / "world.md").unlink()
        wf = M4CharacterWorkflow(project_dir=d, llm_client=_build_mock_llm(M4_LLM_OUTPUT))
        with pytest.raises(RuntimeError, match="world.md 不存在"):
            wf.run()

    def test_m4_requires_architecture_md(self, tmp_path: Path) -> None:
        d = _build_minimal_project(tmp_path)
        (d / "architecture.md").unlink()
        wf = M4CharacterWorkflow(project_dir=d, llm_client=_build_mock_llm(M4_LLM_OUTPUT))
        with pytest.raises(RuntimeError):
            wf.run()

    def test_m4_requires_confirmed_architecture(self, tmp_path: Path) -> None:
        d = _build_minimal_project(tmp_path)
        # 取消 confirmed
        post = frontmatter.load(d / "architecture.md")
        post.metadata["confirmed"] = False
        (d / "architecture.md").write_bytes(frontmatter.dumps(post).encode("utf-8"))
        wf = M4CharacterWorkflow(project_dir=d, llm_client=_build_mock_llm(M4_LLM_OUTPUT))
        with pytest.raises(RuntimeError, match="尚未确认"):
            wf.run()

    def test_m4_requires_outline_md(self, tmp_path: Path) -> None:
        d = _build_minimal_project(tmp_path)
        (d / "outline.md").unlink()
        wf = M4CharacterWorkflow(project_dir=d, llm_client=_build_mock_llm(M4_LLM_OUTPUT))
        with pytest.raises(RuntimeError, match="outline.md 不存在"):
            wf.run()

    def test_m4_requires_outlining_or_char_state(self, tmp_path: Path) -> None:
        d = _build_minimal_project(tmp_path, state=State.ARCH_CONFIRMED)
        wf = M4CharacterWorkflow(project_dir=d, llm_client=_build_mock_llm(M4_LLM_OUTPUT))
        with pytest.raises(RuntimeError, match="OUTLINING"):
            wf.run()


# ============================================================
# 测试：正确流程
# ============================================================
class TestHappyPath:
    def test_runs_and_generates_all_files(self, tmp_path: Path) -> None:
        d = _build_minimal_project(tmp_path)
        wf = M4CharacterWorkflow(project_dir=d, llm_client=_build_mock_llm(M4_LLM_OUTPUT))
        r = wf.run()

        # 文件都生成了
        assert r.protagonist_route_file.exists()
        assert r.graph_file.exists()
        assert r.foreshadows_file.exists()
        assert r.golden_finger_file.exists()
        assert len(r.character_files) == 5
        for p in r.character_files:
            assert p.exists()

        # 主角路线有两个节点
        route_text = r.protagonist_route_file.read_text(encoding="utf-8")
        assert "N01" in route_text
        assert "备选分支" in route_text
        assert "太虚镜初醒" in route_text

        # 状态转换
        sm = StateMachine(d)
        sm.load()
        assert sm.state == State.CHARACTER_DESIGN

    def test_characters_md_contains_required_sections(self, tmp_path: Path) -> None:
        d = _build_minimal_project(tmp_path)
        wf = M4CharacterWorkflow(project_dir=d, llm_client=_build_mock_llm(M4_LLM_OUTPUT))
        r = wf.run()

        # 林寻
        lin = next(p for p in r.character_files if "林寻" in p.name)
        txt = lin.read_text(encoding="utf-8")
        assert "核心动机" in txt
        assert "语言指纹" in txt
        assert "口头禅" in txt
        assert "禁用词" in txt
        assert "我选笨路" in txt
        # frontmatter
        post = frontmatter.load(lin)
        assert post.metadata.get("name") == "林寻"
        assert post.metadata.get("role") == "protagonist"
        assert post.metadata.get("faction") == "寒门互助盟"

    def test_antagonist_has_motivation_check(self, tmp_path: Path) -> None:
        d = _build_minimal_project(tmp_path)
        wf = M4CharacterWorkflow(project_dir=d, llm_client=_build_mock_llm(M4_LLM_OUTPUT))
        r = wf.run()
        pei = next(p for p in r.character_files if "裴玄机" in p.name)
        txt = pei.read_text(encoding="utf-8")
        assert "反派动机合理性自检" in txt
        assert "必要之恶" in txt or "末法早期" in txt

    def test_supporting_has_appearance_interval(self, tmp_path: Path) -> None:
        d = _build_minimal_project(tmp_path)
        wf = M4CharacterWorkflow(project_dir=d, llm_client=_build_mock_llm(M4_LLM_OUTPUT))
        r = wf.run()
        shen = next(p for p in r.character_files if "沈砚" in p.name)
        txt = shen.read_text(encoding="utf-8")
        assert "配角独立支线钩子" in txt
        assert "每 4 章至少露面一次" in txt

    def test_graph_md_has_mermaid_and_tables(self, tmp_path: Path) -> None:
        d = _build_minimal_project(tmp_path)
        wf = M4CharacterWorkflow(project_dir=d, llm_client=_build_mock_llm(M4_LLM_OUTPUT))
        r = wf.run()
        txt = r.graph_file.read_text(encoding="utf-8")
        assert "```mermaid" in txt
        assert "graph LR" in txt
        # 有林寻节点
        assert "林寻" in txt
        # 边表有 6 条
        # 统计 "| A |" 数量（边表行），其实我们不精确，只要有 表格 2 张
        assert "| 起 | 止 | 类型 |" in txt
        assert "| ID | 角色 | 分组 |" in txt

    def test_foreshadows_table_and_stats(self, tmp_path: Path) -> None:
        d = _build_minimal_project(tmp_path)
        wf = M4CharacterWorkflow(project_dir=d, llm_client=_build_mock_llm(M4_LLM_OUTPUT))
        r = wf.run()
        txt = r.foreshadows_file.read_text(encoding="utf-8")
        for id_ in [f"F-{i:02d}" for i in range(1, 7)]:
            assert id_ in txt
        # 统计：6 条未埋
        assert "- 未埋：6" in txt
        assert "- 已埋：0" in txt
        assert "回收率：N/A" in txt

    def test_golden_finger_has_frozen_and_stages(self, tmp_path: Path) -> None:
        d = _build_minimal_project(tmp_path)
        wf = M4CharacterWorkflow(project_dir=d, llm_client=_build_mock_llm(M4_LLM_OUTPUT))
        r = wf.run()
        txt = r.golden_finger_file.read_text(encoding="utf-8")
        post = frontmatter.load(r.golden_finger_file)
        assert post.metadata.get("name") == "太虚镜"
        assert post.metadata.get("frozen") is True
        assert "## 成长阶段" in txt
        # 阶段数
        assert "1（凝气/筑基）" in txt
        assert "3（化神/大乘）" in txt
        assert "## 硬上限（冻结字段）" in txt

    def test_llm_prompt_includes_sublines(self, tmp_path: Path) -> None:
        d = _build_minimal_project(tmp_path)
        fake = _build_mock_llm(M4_LLM_OUTPUT)
        wf = M4CharacterWorkflow(project_dir=d, llm_client=fake)
        wf.run()
        assert fake.chat_creative.call_count == 1
        call_kwargs = fake.chat_creative.call_args.kwargs
        messages = call_kwargs.get("messages") or fake.chat_creative.call_args.args[0]
        user_msg = next(m["content"] for m in messages if m["role"] == "user")
        # prompt 包含支线名
        assert "器灵人性觉醒" in user_msg
        assert "寒门众生觉醒" in user_msg
        # prompt 包含架构要素
        assert "林寻" in user_msg
        assert "裴玄机" not in user_msg  # 架构里还没有裴玄机
        # prompt 包含金手指信息
        assert "太虚镜" in user_msg

    def test_re_run_ok_in_character_design_state(self, tmp_path: Path) -> None:
        """CHARACTER_DESIGN 再跑一次不抛状态错"""
        d = _build_minimal_project(tmp_path)
        wf = M4CharacterWorkflow(project_dir=d, llm_client=_build_mock_llm(M4_LLM_OUTPUT))
        r1 = wf.run()
        assert r1.protagonist_route_file.exists()
        # 再跑：同一个 LLM 输出
        fake2 = _build_mock_llm(M4_LLM_OUTPUT)
        wf2 = M4CharacterWorkflow(project_dir=d, llm_client=fake2)
        r2 = wf2.run()
        assert len(r2.character_files) == 5
        # 状态没有回退
        sm = StateMachine(d)
        sm.load()
        assert sm.state == State.CHARACTER_DESIGN

    def test_empty_characters_raises(self, tmp_path: Path) -> None:
        """LLM 返回空角色/空路线时：两次尝试后响亮抛错，绝不静默写占位角色
        （生成类写操作失败要响亮报错，不能静默写残缺产物）。"""
        d = _build_minimal_project(tmp_path)
        modified = dict(M4_LLM_OUTPUT)
        modified["characters"] = []
        modified["protagonist_route"] = {}
        modified["relation_graph"] = {}
        modified["foreshadows"] = []
        wf = M4CharacterWorkflow(project_dir=d, llm_client=_build_mock_llm(modified))
        with pytest.raises(RuntimeError):
            wf.run()
