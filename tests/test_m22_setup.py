"""M22 项目脚手架（写作基础设施部署）离线测试

覆盖（全部离线，无 LLM / 网络）：
- split_sections / merge_markdown_sections：按 ``## `` section 合并策略
  （已有 section 保留、模板新增 section 追加、preamble 处理）
- render_placeholders：占位符替换（空值保留占位符原样）
- workflow 部署：CLAUDE.md、rules（4）、agents（7）、上下文模板、.story-deployed 哨兵
- 部署策略：CLAUDE.md 合并、rules 存在保留 / 缺失复制、agents 覆盖
- 已部署检测：二次运行默认跳过并提示；--force 重新部署
- CLI setup 命令：--json 输出与已部署跳过

运行：python -m pytest tests/test_m22_setup.py -q --tb=short（cwd=agent/）
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agent.cli import app  # 触发全部命令注册（导入副作用）
from agent.workflows.m22_setup import (
    AGENTS_VERSION,
    SENTINEL_NAME,
    SETUP_SKILL_VERSION,
    M22SetupInput,
    M22SetupWorkflow,
    merge_markdown_sections,
    render_placeholders,
    split_sections,
)

RULES_COUNT = 4  # story-consistency / story-format / story-narrative / story-outline
AGENTS_COUNT = 7  # story-architect / character-designer / narrative-writer / ... / chapter-extractor


# ---------------------------------------------------------------------------
# 纯函数：section 合并 / 占位符
# ---------------------------------------------------------------------------
def test_split_sections_headings() -> None:
    text = "# 标题\n\n## A\n内容A\n\n## B\n内容B\n"
    sections = split_sections(text)
    headings = [h.strip() for h, _ in sections if h]
    assert headings == ["## A", "## B"]


def test_merge_keeps_existing_section() -> None:
    existing = "# 用户标题\n\n## 用户专属\n\n用户内容\n"
    template = (
        "# {项目名} — 网文写作工具集\n\n"
        "## Skill 路由表\n\n路由内容\n"
        "## 用户专属\n\n模板覆盖内容（不应生效）\n"
    )
    merged = merge_markdown_sections(existing, template)
    # 用户已有 section 保留，模板同名 section 不覆盖
    assert "用户内容" in merged
    assert "模板覆盖内容" not in merged
    # 模板新增 section 追加
    assert "Skill 路由表" in merged
    # 用户 preamble 保留，模板 preamble 不叠加
    assert "用户标题" in merged
    assert "网文写作工具集" not in merged


def test_merge_appends_new_sections() -> None:
    existing = "## 用户专属\n\n用户内容\n"
    template = (
        "# {项目名} — 网文写作工具集\n\n"
        "## Skill 路由表\n\n路由内容\n"
        "## 文件结构\n\n结构内容\n"
    )
    merged = merge_markdown_sections(existing, template)
    assert merged.index("用户专属") < merged.index("Skill 路由表")
    assert merged.index("Skill 路由表") < merged.index("文件结构")


def test_merge_uses_template_preamble_when_user_has_none() -> None:
    existing = "## 用户专属\n\n用户内容\n"
    template = "# {项目名} — 网文写作工具集\n\n## Skill 路由表\n\n路由内容\n"
    merged = merge_markdown_sections(existing, template)
    # 用户无 preamble → 模板 preamble 兜底
    assert "网文写作工具集" in merged


def test_render_placeholders_replaces_all() -> None:
    text = "{项目名}/{书名}/{目标平台}/{作者名}"
    out = render_placeholders(
        text, project_name="P", book="B", platform="番茄", author="A"
    )
    assert out == "P/B/番茄/A"


def test_render_placeholders_keeps_empty() -> None:
    text = "{项目名}/{书名}"
    out = render_placeholders(text, project_name="P")
    assert out == "P/{书名}"


# ---------------------------------------------------------------------------
# workflow 部署
# ---------------------------------------------------------------------------
def _deploy(tmp_path: Path, **kw) -> tuple[Path, M22SetupWorkflow, object]:
    proj = tmp_path / "bookproj"
    wf = M22SetupWorkflow(project_dir=proj)
    result = wf.run(
        M22SetupInput(
            project_dir=proj,
            book=kw.get("book", ""),
            platform=kw.get("platform", "起点"),
            author=kw.get("author", "作者"),
            force=kw.get("force", False),
        )
    )
    return proj, wf, result


def test_deploy_creates_all_files(tmp_path: Path) -> None:
    proj, _wf, result = _deploy(tmp_path)
    assert result.deployed is True
    # CLAUDE.md
    claude = proj / "CLAUDE.md"
    assert claude.exists()
    assert "## Skill 路由表" in claude.read_text(encoding="utf-8")
    # rules 合并部署（4 个）
    rules = list((proj / ".claude" / "rules").glob("*.md"))
    assert len(rules) == RULES_COUNT
    # agents 覆盖部署（7 个）
    agents = list((proj / ".claude" / "agents").glob("*.md"))
    assert len(agents) == AGENTS_COUNT
    # 哨兵
    sentinel = proj / SENTINEL_NAME
    assert sentinel.exists()
    data = json.loads(sentinel.read_text(encoding="utf-8"))
    assert data["deployed_at"]
    assert data["agents_version"] == AGENTS_VERSION
    assert data["setup_skill_version"] == SETUP_SKILL_VERSION


def test_deploy_placeholders_replaced(tmp_path: Path) -> None:
    proj, _wf, _r = _deploy(tmp_path, book="仙路", platform="番茄", author="李白")
    claude = (proj / "CLAUDE.md").read_text(encoding="utf-8")
    assert "# 仙路 — 网文写作工具集" in claude
    assert "> 目标平台：番茄 · 作者：李白" in claude
    # {书名} 在文件结构中被替换
    assert "`仙路/正文/`" in claude
    assert "{书名}" not in claude


def test_deploy_merges_existing_claude_md(tmp_path: Path) -> None:
    proj = tmp_path / "bookproj"
    proj.mkdir(parents=True)
    (proj / "CLAUDE.md").write_text(
        "# 我的小说\n\n## 我的专属规则\n\n作者自定义内容\n", encoding="utf-8"
    )
    wf = M22SetupWorkflow(project_dir=proj)
    result = wf.run(M22SetupInput(project_dir=proj))
    merged = (proj / "CLAUDE.md").read_text(encoding="utf-8")
    # 已有 preamble 与 section 保留
    assert "我的小说" in merged
    assert "我的专属规则" in merged
    assert "作者自定义内容" in merged
    # 模板新增 section 追加
    assert "## Skill 路由表" in merged
    assert "CLAUDE.md" in result.preserved_files


def test_deploy_rules_preserve_existing(tmp_path: Path) -> None:
    proj = tmp_path / "bookproj"
    (proj / ".claude" / "rules").mkdir(parents=True)
    (proj / ".claude" / "rules" / "story-format.md").write_text(
        "用户自定义格式规则\n", encoding="utf-8"
    )
    wf = M22SetupWorkflow(project_dir=proj)
    result = wf.run(M22SetupInput(project_dir=proj))
    # 既有 rules 文件保留
    kept = (proj / ".claude" / "rules" / "story-format.md").read_text(encoding="utf-8")
    assert kept == "用户自定义格式规则\n"
    assert "rules/story-format.md" in result.preserved_files
    # 缺失 rules 文件被复制
    assert (proj / ".claude" / "rules" / "story-consistency.md").exists()


def test_deploy_agents_overwrite(tmp_path: Path) -> None:
    proj = tmp_path / "bookproj"
    (proj / ".claude" / "agents").mkdir(parents=True)
    (proj / ".claude" / "agents" / "story-architect.md").write_text(
        "旧版 agent 定义\n", encoding="utf-8"
    )
    wf = M22SetupWorkflow(project_dir=proj)
    result = wf.run(M22SetupInput(project_dir=proj))
    overwritten = (proj / ".claude" / "agents" / "story-architect.md").read_text(
        encoding="utf-8"
    )
    # agents 可覆盖：旧内容被模板替换
    assert "旧版 agent 定义" not in overwritten
    assert "Story Architect" in overwritten
    assert "agents/story-architect.md" in result.deployed_files


def test_deploy_context_when_book_dir_exists(tmp_path: Path) -> None:
    proj = tmp_path / "bookproj"
    (proj / "仙路").mkdir(parents=True)
    wf = M22SetupWorkflow(project_dir=proj)
    result = wf.run(M22SetupInput(project_dir=proj, book="仙路"))
    ctx = proj / "仙路" / "追踪" / "上下文.md"
    assert ctx.exists()
    assert "# 写作进度 — 仙路" in ctx.read_text(encoding="utf-8")
    assert "仙路/追踪/上下文.md" in result.deployed_files


def test_deploy_skips_on_second_run(tmp_path: Path) -> None:
    proj, _wf, first = _deploy(tmp_path)
    assert first.deployed is True
    # 二次运行：哨兵存在 → 跳过
    wf = M22SetupWorkflow(project_dir=proj)
    second = wf.run(M22SetupInput(project_dir=proj))
    assert second.deployed is False
    assert second.skipped_existing is True
    assert any("跳过" in n for n in second.notes)


def test_deploy_force_redeploys(tmp_path: Path) -> None:
    proj, _wf, first = _deploy(tmp_path)
    assert first.deployed is True
    wf = M22SetupWorkflow(project_dir=proj)
    second = wf.run(M22SetupInput(project_dir=proj, force=True))
    assert second.deployed is True
    assert second.redeployed is True
    assert second.skipped_existing is False


# ---------------------------------------------------------------------------
# CLI setup 命令（--json）
# ---------------------------------------------------------------------------
def test_cli_setup_json_output(tmp_path: Path) -> None:
    proj = tmp_path / "novel"
    runner = CliRunner()
    result = runner.invoke(
        app, ["setup", "--dir", str(proj), "--book", "仙路", "--json"]
    )
    assert result.exit_code == 0
    data = json.loads(result.output.strip().splitlines()[-1])
    assert data["success"] is True
    assert data["deployed"] is True
    assert data["agents_version"] == AGENTS_VERSION
    assert (proj / SENTINEL_NAME).exists()


def test_cli_setup_second_run_skips(tmp_path: Path) -> None:
    proj = tmp_path / "novel"
    runner = CliRunner()
    first = runner.invoke(app, ["setup", "--dir", str(proj), "--json"])
    assert first.exit_code == 0
    second = runner.invoke(app, ["setup", "--dir", str(proj), "--json"])
    assert second.exit_code == 0
    data = json.loads(second.output.strip().splitlines()[-1])
    assert data["deployed"] is False
    assert data["skipped_existing"] is True


def test_cli_setup_is_global() -> None:
    """/setup 登记为全局命令（任意状态放行，纯文件部署不依赖状态机）。"""
    from agent.core.engine.command_router import get_command_meta

    meta = get_command_meta("/setup")
    assert meta is not None
    assert meta.is_global is True
