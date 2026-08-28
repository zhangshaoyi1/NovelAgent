"""M3 大纲生成工作流单元测试

mock LLM，验证 outline.md 生成、subline 占位、门禁、状态转换。
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import frontmatter
import pytest

from agent.client import LLMClient, LLMResponse
from agent.core.story.setting_manager import SettingManager
from agent.core.engine.state_machine import State, StateMachine
from agent.workflows.m3_outline import M3OutlineWorkflow, M3Result


# ------ mock LLM 输出 ------

OUTLINE_JSON = """{
  "synopsis": "末法时代宗门垄断真法，灵根残缺的少年林寻偶得上古至宝太虚镜，以精血寿元为薪推演万法。在复仇过程中他发现师父之死实为宗门定期的耗材清洗计划，遂以推演出的残缺功法反向解构宗门暗门。最终在万宗大典之上，太虚镜以自我崩碎的代价将普世真法铭刻天地，林寻背负挚友的永恒消逝，在废墟之上开启万物共生的新纪元。",
  "sublines": [
    {
      "subline_name": "太虚镜初醒",
      "goal": "林寻获得太虚镜，完成身份转变并初破宗门阴谋",
      "characters": "林寻、太虚镜、执法堂弟子甲、杂役好友",
      "conflicts": "- 宗门执法堂追杀\\n- 镜灵理性 vs 主角人性的第一次价值冲突",
      "constraints": "只能使用凡阶功法；必须在宗门内门封锁前逃出",
      "mainline_relation": "铺垫：引出主角身份与金手指、建立宗门反派立场与镜灵冲突基础",
      "pressure_curve": {"setup": "1-3", "conflict": "4-6", "climax": "7-8", "relief": "9"}
    },
    {
      "subline_name": "寒门聚义",
      "goal": "集结被抛弃的寒门修士，组建反抗雏形",
      "characters": "林寻、太虚镜、逆命盟高层、百草谷叛徒、散修代表",
      "conflicts": "- 逆命盟内部派系的信任博弈\\n- 百草谷炼药陷阱",
      "constraints": "只能在荒域秘密行动，不得暴露太虚镜存在",
      "mainline_relation": "推动：建立主角阵营，积蓄对抗宗门的力量",
      "pressure_curve": {"setup": "10-12", "conflict": "13-16", "climax": "17-18", "relief": "19"}
    },
    {
      "subline_name": "古史解密",
      "goal": "还原上古修仙界毁灭真相，强化镜灵冲突",
      "characters": "林寻、太虚镜、衍道天尊残念、天机阁长老",
      "conflicts": "- 上古残念的考验\\n- 镜灵对自身程序的质疑觉醒",
      "constraints": "推演地级以上功法需古修手札作为媒介",
      "mainline_relation": "升华：揭示历史循环，为镜灵最终牺牲做铺垫",
      "pressure_curve": {"setup": "20-22", "conflict": "23-25", "climax": "26", "relief": "27"}
    },
    {
      "subline_name": "万宗证道",
      "goal": "大典对决，太虚镜殉道，新法铭刻天地",
      "characters": "林寻、太虚镜、三大宗门老祖、寒门修士群像",
      "conflicts": "- 宗门大阵围剿\\n- 镜灵程序崩溃与觉醒选择",
      "constraints": "新法铭刻需万宗大典特定时辰，否则功亏一篑",
      "mainline_relation": "回收全部伏笔：镜灵牺牲+宗门覆灭+旧秩序终结",
      "pressure_curve": {"setup": "28-30", "conflict": "31-33", "climax": "34-35", "relief": "36"}
    }
  ]
}"""


@pytest.fixture
def mock_llm() -> MagicMock:
    llm = MagicMock(spec=LLMClient)
    llm.chat_creative.return_value = LLMResponse(
        text=OUTLINE_JSON, usage={}, model="m"
    )
    return llm


@pytest.fixture
def project_with_confirmed_arch(tmp_path: Path) -> Path:
    """创建一个已完成 M14（架构已确认）的项目"""
    # world.md
    sm = SettingManager(tmp_path)
    sm.save_world(
        metadata={
            "title": "太虚镜",
            "scope": "long",
            "genre": "xiuxian",
            "style": {"tone": "热血"},
        },
        content=(
            "# 总设定集 · 太虚镜\n\n"
            "## 故事简介\n\n废柴少年偶得太虚镜踏上逆天修仙路\n\n"
            "## 世界观\n\n世界观内容"
        ),
    )
    # architecture.md（含已确认状态 + 八维度 JSON）
    arch_data = {
        "title": "太虚镜",
        "confirmed": True,
        "confirmed_at": "2026-08-14 10:00:00",
        "version": 2,
        "created_at": "2026-08-14 10:00:00",
        "updated_at": "2026-08-14 10:00:00",
        "architecture": {
            "story_core": "废柴少年凭借太虚镜推演功法，逆天改命推翻宗门垄断",
            "protagonist_triple": {
                "who": "林寻",
                "want": "推翻宗门垄断",
                "obstacle": "宗门势力庞大",
            },
            "main_plot": {
                "beginning": "林寻被逐，唤醒太虚镜",
                "development": "暗中成长",
                "twist": "发现师父之死是制度性屠杀",
                "resolution": "镜灵殉道，新法传世",
            },
            "sublines_preview": "- 镜灵觉醒线\n- 寒门聚义线",
            "conflict_nodes": "- 宗门围剿\n- 镜灵价值观冲突",
            "theme": "知识共享对抗垄断",
            "ending": "镜灵殉道，新法铭刻天地",
            "emotional_tone": "热血悲壮",
            "synopsis": "架构简介：末法时代林寻携太虚镜逆天之路。",
        },
    }
    from agent.workflows.m14_architecture import M14ArchitectureWorkflow  # noqa
    import frontmatter as fm
    # 手动构造：直接写 frontmatter + 正文
    content = f"# 故事架构 · 太虚镜\n\n正文..."
    post = fm.Post(content, **arch_data)
    (tmp_path / "architecture.md").write_text(fm.dumps(post), encoding="utf-8")

    # 状态：ARCH_CONFIRMED
    sm_state = StateMachine(tmp_path)
    sm_state.state = State.ARCH_CONFIRMED
    sm_state.save()
    return tmp_path


@pytest.fixture
def workflow(
    project_with_confirmed_arch: Path, mock_llm: MagicMock
) -> M3OutlineWorkflow:
    return M3OutlineWorkflow(
        project_dir=project_with_confirmed_arch,
        llm_client=mock_llm,
        setting_manager=SettingManager(project_with_confirmed_arch),
        state_machine=StateMachine(project_with_confirmed_arch),
    )


# ============================================================
# 正常流程
# ============================================================

def test_m3_generates_outline_md(workflow: M3OutlineWorkflow) -> None:
    """M3 应生成 outline.md 含简介与支线表格"""
    result = workflow.run()
    assert result.outline_file.exists()
    content = result.outline_file.read_text(encoding="utf-8")
    assert "太虚镜" in content
    assert "故事简介" in content
    assert "顶层支线任务列表" in content


def test_m3_returns_correct_synopsis_and_sublines(
    workflow: M3OutlineWorkflow,
) -> None:
    """结果对象应返回正确的简介与支线列表"""
    result = workflow.run()
    assert "末法时代" in result.synopsis or "太虚镜" in result.synopsis
    assert len(result.sublines) == 4
    names = [s["subline_name"] for s in result.sublines]
    assert "太虚镜初醒" in names
    assert "寒门聚义" in names
    assert "古史解密" in names
    assert "万宗证道" in names


def test_m3_creates_subline_files(workflow: M3OutlineWorkflow) -> None:
    """应为每条支线创建 subline.md 占位文件"""
    result = workflow.run()
    assert len(result.subline_files) == 4
    for p in result.subline_files:
        assert p.exists()
        assert p.name == "subline.md"
        # 目录结构应是 S<NN>_<name>/subline.md
        assert p.parent.name.startswith("S01_") or p.parent.name.startswith("S02_") \
            or p.parent.name.startswith("S03_") or p.parent.name.startswith("S04_")


def test_m3_sublines_have_valid_ids(workflow: M3OutlineWorkflow) -> None:
    """subline frontmatter 含 subline_id / status: planned"""
    result = workflow.run()
    for p in result.subline_files:
        post = frontmatter.load(p)
        assert "subline_id" in post.metadata
        assert post.metadata.get("status") == "planned"
        assert "subline_name" in post.metadata
        content = p.read_text(encoding="utf-8")
        assert "支线目标" in content


def test_m3_state_transitions_to_outlining(
    workflow: M3OutlineWorkflow,
) -> None:
    """执行后状态应转为 OUTLINING"""
    workflow.run()
    workflow.state_machine.load()
    assert workflow.state_machine.state == State.OUTLINING


def test_m3_llm_called_with_arch_info(
    workflow: M3OutlineWorkflow, mock_llm: MagicMock
) -> None:
    """LLM prompt 应包含架构关键信息"""
    workflow.run()
    mock_llm.chat_creative.assert_called_once()
    kw = mock_llm.chat_creative.call_args.kwargs
    user_msg = next(m["content"] for m in kw["messages"] if m["role"] == "user")
    assert "太虚镜" in user_msg
    assert "林寻" in user_msg
    assert "推翻宗门垄断" in user_msg


def test_m3_outline_contains_detail_section(workflow: M3OutlineWorkflow) -> None:
    """outline.md 应包含支线详情段"""
    workflow.run()
    content = workflow.outline_file.read_text(encoding="utf-8")
    assert "### 支线详情" in content or "支线详情" in content
    # 至少包含 4 条支线的标题
    assert "S01" in content
    assert "S04" in content


# ============================================================
# 门禁
# ============================================================

def test_m3_rejects_unconfirmed_architecture(tmp_path: Path, mock_llm: MagicMock) -> None:
    """架构未确认时 M3 应拒绝执行"""
    sm = SettingManager(tmp_path)
    sm.save_world(
        metadata={"title": "太虚镜", "scope": "long"},
        content="# 总设定集\n\n## 故事简介\n\n简介\n",
    )
    # architecture.md 但 confirmed=false
    import frontmatter as fm
    post = fm.Post(
        "# 架构\n",
        title="太虚镜", confirmed=False, version=1, architecture={},
    )
    (tmp_path / "architecture.md").write_text(fm.dumps(post), encoding="utf-8")

    sm_state = StateMachine(tmp_path)
    sm_state.state = State.ARCH_CONFIRMED  # 状态假装过，但文件未确认
    sm_state.save()

    wf = M3OutlineWorkflow(
        project_dir=tmp_path,
        llm_client=mock_llm,
        setting_manager=SettingManager(tmp_path),
        state_machine=StateMachine(tmp_path),
    )
    with pytest.raises(RuntimeError, match="尚未确认"):
        wf.run()


def test_m3_rejects_wrong_state(
    project_with_confirmed_arch: Path, mock_llm: MagicMock
) -> None:
    """非 ARCH_CONFIRMED/OUTLINING 状态应拒绝"""
    sm_state = StateMachine(project_with_confirmed_arch)
    sm_state.state = State.DISCUSSING
    sm_state.save()
    wf = M3OutlineWorkflow(
        project_dir=project_with_confirmed_arch,
        llm_client=mock_llm,
        setting_manager=SettingManager(project_with_confirmed_arch),
        state_machine=StateMachine(project_with_confirmed_arch),
    )
    with pytest.raises(RuntimeError, match="不允许生成大纲"):
        wf.run()


def test_m3_requires_world_md(tmp_path: Path, mock_llm: MagicMock) -> None:
    """world.md 不存在应报错"""
    sm_state = StateMachine(tmp_path)
    sm_state.state = State.ARCH_CONFIRMED
    sm_state.save()
    wf = M3OutlineWorkflow(
        project_dir=tmp_path,
        llm_client=mock_llm,
        setting_manager=SettingManager(tmp_path),
        state_machine=StateMachine(tmp_path),
    )
    with pytest.raises(RuntimeError, match="world.md 不存在"):
        wf.run()


def test_m3_requires_architecture_md(tmp_path: Path, mock_llm: MagicMock) -> None:
    """architecture.md 不存在应报错"""
    sm = SettingManager(tmp_path)
    sm.save_world(
        metadata={"title": "太虚镜", "scope": "long"},
        content="# 总设定集\n\n## 故事简介\n\n简介\n",
    )
    sm_state = StateMachine(tmp_path)
    sm_state.state = State.ARCH_CONFIRMED
    sm_state.save()
    wf = M3OutlineWorkflow(
        project_dir=tmp_path,
        llm_client=mock_llm,
        setting_manager=SettingManager(tmp_path),
        state_machine=StateMachine(tmp_path),
    )
    with pytest.raises(RuntimeError, match="尚未确认"):
        wf.run()


# ============================================================
# 容错
# ============================================================

def test_m3_handles_empty_sublines(tmp_path: Path) -> None:
    """LLM 返回空 sublines 时应兜底生成一条"""
    # 构造项目
    sm = SettingManager(tmp_path)
    sm.save_world(
        metadata={"title": "太虚镜", "scope": "long"},
        content="# 总设定集\n\n## 故事简介\n\n简介\n",
    )
    import frontmatter as fm
    post = fm.Post(
        "# 架构\n",
        title="太虚镜", confirmed=True, confirmed_at="x", version=1, architecture={
            "story_core": "xxx",
            "protagonist_triple": {"who": "", "want": "", "obstacle": ""},
            "main_plot": {"beginning": "", "development": "", "twist": "", "resolution": ""},
        },
    )
    (tmp_path / "architecture.md").write_text(fm.dumps(post), encoding="utf-8")
    sm_state = StateMachine(tmp_path)
    sm_state.state = State.ARCH_CONFIRMED
    sm_state.save()

    # mock: LLM 返回空 sublines
    empty_llm = MagicMock(spec=LLMClient)
    empty_llm.chat_creative.return_value = LLMResponse(
        text='{"synopsis": "简介", "sublines": []}', usage={}, model="m"
    )
    wf = M3OutlineWorkflow(
        project_dir=tmp_path,
        llm_client=empty_llm,
        setting_manager=SettingManager(tmp_path),
        state_machine=StateMachine(tmp_path),
    )
    result = wf.run()
    assert len(result.sublines) >= 1
    assert result.subline_files and result.subline_files[0].exists()


def test_m3_handles_llm_json_parse_failure(tmp_path: Path) -> None:
    """LLM 输出非 JSON 应降级，不应崩溃"""
    sm = SettingManager(tmp_path)
    sm.save_world(
        metadata={"title": "太虚镜", "scope": "long"},
        content="# 总设定集\n\n## 故事简介\n\n简介\n",
    )
    import frontmatter as fm
    post = fm.Post(
        "# 架构\n",
        title="太虚镜", confirmed=True, confirmed_at="x", version=1, architecture={
            "story_core": "xxx",
            "protagonist_triple": {"who": "", "want": "", "obstacle": ""},
            "main_plot": {"beginning": "", "development": "", "twist": "", "resolution": ""},
        },
    )
    (tmp_path / "architecture.md").write_text(fm.dumps(post), encoding="utf-8")
    sm_state = StateMachine(tmp_path)
    sm_state.state = State.ARCH_CONFIRMED
    sm_state.save()

    bad_llm = MagicMock(spec=LLMClient)
    bad_llm.chat_creative.return_value = LLMResponse(
        text="这是一段纯文本介绍，没有 JSON 格式...", usage={}, model="m"
    )
    wf = M3OutlineWorkflow(
        project_dir=tmp_path,
        llm_client=bad_llm,
        setting_manager=SettingManager(tmp_path),
        state_machine=StateMachine(tmp_path),
    )
    result = wf.run()  # 不抛异常
    assert result.outline_file.exists()
    assert len(result.sublines) >= 1
