"""T-3 hook 分发器 + 题材规则模块级通道测试

覆盖：
- 对 xiuxian 分发 hooks 后 quality_checker.GENRE_RULES 非空
- 不存在的 hook 名仅 warning，不中断宿主
- M5 质量校验 prompt 注入题材层质量规则文本
- m1_config.load_genre_template hook 在 world.md 缺失时落盘、存在时跳过
"""
from __future__ import annotations

import warnings
from pathlib import Path

from agent.core.registry.genre_pack import GenreManifest, GenrePack, GenrePackRegistry
from agent.core.infra.hook_dispatcher import dispatch_genre_hooks
from agent.workflows.planning import m1_config


def _snapshot_genre_rules():
    from agent.core.quality.scoring import quality_checker

    return list(quality_checker.GENRE_RULES)


def test_dispatch_xiuxian_populates_genre_rules(tmp_path: Path) -> None:
    """对 xiuxian 调 dispatch_genre_hooks 后 GENRE_RULES 非空"""
    from agent.core.quality.scoring import quality_checker

    original = _snapshot_genre_rules()
    quality_checker.GENRE_RULES.clear()
    try:
        pack = GenrePackRegistry().load("xiuxian")
        dispatched = dispatch_genre_hooks(tmp_path, "xiuxian", pack)
        assert "agent.core.quality.scoring.quality_checker.register_genre_rules" in dispatched
        assert len(quality_checker.GENRE_RULES) > 0
        # 解析出的规则应含题材层 id（如 G-01）
        assert any(r.layer.value == "genre" for r in quality_checker.GENRE_RULES)
    finally:
        quality_checker.GENRE_RULES[:] = original


def test_unknown_hook_only_warns(tmp_path: Path) -> None:
    """不存在的 hook 名仅 warning，不中断宿主"""
    pack = GenrePack(
        manifest=GenreManifest(name="x", hooks=["no_such_module.no_such_func"]),
    )
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        dispatched = dispatch_genre_hooks(tmp_path, "x", pack)
    assert dispatched == []
    assert len(caught) == 1
    assert "no_such_module" in str(caught[0].message)


def test_dispatch_bare_name_registered_hook(tmp_path: Path) -> None:
    """具名注册 hook（bare name）经 register_genre_hook 反向注入后可分发

    R6：core 不 import workflows；load_genre_template 由 m1_config import 时自注册。
    """
    from agent.workflows.planning import m1_config  # noqa: F401  触发注册副作用

    pack = GenrePackRegistry().load("xiuxian")
    world_file = tmp_path / "world.md"
    assert not world_file.exists()
    dispatched = dispatch_genre_hooks(tmp_path, "xiuxian", pack)
    assert "load_genre_template" in dispatched, "具名 hook 应解析并执行"
    assert world_file.exists(), "load_genre_template 应真实落盘种子草稿"


def test_dispatch_refuses_upper_layer_spec(tmp_path: Path) -> None:
    """点分 hook 规格指向上层（agent.workflows.*）必须拒绝且不 import（R6 运行时护栏）"""
    upper = "agent.workflows.planning.m1_config.load_genre_template"
    pack = GenrePack(manifest=GenreManifest(name="x", hooks=[upper]))
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        dispatched = dispatch_genre_hooks(tmp_path, "x", pack)
    assert dispatched == []
    assert len(caught) == 1
    msg = str(caught[0].message)
    assert "R6" in msg and "agent.workflows" in msg


def test_m5_check_prompt_includes_genre_rules(tmp_path: Path) -> None:
    """M5 质量校验 prompt 注入题材层质量规则文本（mock 捕获）"""
    from unittest.mock import MagicMock

    from agent.workflows.writing.m5_write_chapter import M5WriteChapterWorkflow

    from types import SimpleNamespace

    llm = MagicMock()
    llm.chat.return_value = SimpleNamespace(
        text='{"overall_pass": true, "rules": [], "suggestions": ""}'
    )
    wf = M5WriteChapterWorkflow(project_dir=tmp_path, llm_client=llm)
    ctx = {
        "world_info": {
            "tone": "热血",
            "chapter_length": 3000,
            "genre": "xiuxian",
            "characters_fingerprint": "",
        },
        "pressure_stage": "发展",
        "characters_fingerprint": "",
        "chapter_num": 1,
    }
    chapter = "主角运转功法，丹田灵气翻涌，一举突破至炼气三层。"
    wf._quality_check_and_revise(ctx, chapter)

    called = llm.chat.call_args_list[0]
    req = called[0][0]
    check_prompt = req.messages[1]["content"]
    assert "xiuxian" in check_prompt
    # 题材层质量规则文本应被注入（如 G-01 境界推进频率）
    assert "境界推进" in check_prompt or "G-01" in check_prompt


def test_load_genre_template_writes_when_absent(tmp_path: Path) -> None:
    """m1_config.load_genre_template 在 world.md 缺失时落盘种子草稿，存在时跳过"""
    pack = GenrePackRegistry().load("xiuxian")
    world_file = tmp_path / "world.md"
    assert not world_file.exists()

    m1_config.load_genre_template(tmp_path, "xiuxian", pack)
    assert world_file.exists()
    # R2-D：实现演进（bbd2ea6）——只写「冻结核心分节」种子草稿，不整模板落盘
    # （整模板会让「金手指登记模板」样板块泄漏进 world.md，被 M4/M5 前缀误匹配）。
    text = world_file.read_text(encoding="utf-8")
    assert "境界体系（冻结）" in text, "种子草稿应含冻结核心分节（境界体系）"
    assert "金手指登记模板" not in text, "样板块不得泄漏进 world.md（bbd2ea6 修复语义）"

    # 再次调用（world.md 已存在）不应覆盖（保持幂等）
    world_file.write_text("# 用户自定义 world", encoding="utf-8")
    m1_config.load_genre_template(tmp_path, "xiuxian", pack)
    assert world_file.read_text(encoding="utf-8") == "# 用户自定义 world"
