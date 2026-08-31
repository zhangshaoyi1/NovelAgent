"""M22 项目脚手架（写作基础设施部署）工作流

向目标项目目录部署「写作基础设施」：CLAUDE.md、rules/*.md、agents/*.md、
上下文.md.tmpl（有书名目录时）与 .story-deployed 哨兵文件。

移植自 oh-story-claudecod 的 story-setup skill（纯文件部署，一般无需 LLM；
本工作流不调用任何 LLM）。

部署策略：
- ``CLAUDE.md``：按 ``## `` section 合并（已有 section 保留，模板新增 section 追加），
  绝不整体覆盖用户已有 CLAUDE.md。
- ``rules/*.md``：合并——目标已存在同名文件则保留（记入 preserved），缺失则复制。
- ``agents/*.md``：可覆盖——始终用模板内容覆盖目标同名文件（story-setup 管理文件）。
- ``上下文.md.tmpl``：有书名目录（``project/{书名}/`` 存在）时复制到 ``{书名}/追踪/``。
- ``.story-deployed`` 哨兵：含 deployed_at / agents_version / setup_skill_version；
  已部署（文件存在）时默认跳过并提示，``--force`` 重新部署。

占位符替换：{项目名} / {书名} / {目标平台} / {作者名}（空值保留占位符原样）。

状态转换：无（纯文件部署，不驱动状态机）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from rich.console import Console

from agent.core.engine.workflow_registry import workflow

# 模板目录：src/agent/templates/scaffold/
TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates" / "scaffold"

AGENTS_VERSION = 7
SETUP_SKILL_VERSION = "1.0.0"
SENTINEL_NAME = ".story-deployed"


# ---------------------------------------------------------------------------
# 纯函数：section 合并 / 占位符渲染（供测试直接调用）
# ---------------------------------------------------------------------------
def split_sections(text: str) -> list[tuple[str, str]]:
    """按 ``## `` 标题切分 markdown，返回 [(heading_line, body)]。

    heading_line 含 ``## `` 前缀；body 为该 section 到下一个 ``## `` 之前的全文。
    ``## `` 之前的文档头部（如标题）归入 ''（空 heading）section。
    """
    lines = text.splitlines(keepends=True)
    sections: list[tuple[str, str]] = []
    current_heading = ""
    current_body: list[str] = []
    for line in lines:
        if line.startswith("## "):
            if current_heading != "" or current_body:
                sections.append((current_heading, "".join(current_body)))
            current_heading = line
            current_body = []
        else:
            current_body.append(line)
    if current_heading != "" or current_body:
        sections.append((current_heading, "".join(current_body)))
    return sections


def merge_markdown_sections(existing: str, template: str) -> str:
    """按 ``## `` section 合并：已有 section 保留，模板新增 section 追加。

    规则（非破坏性，合并而非覆盖）：
    - 用户已有同名的 ``## `` section → 保留用户内容（不覆盖）
    - 模板独有 section → 追加到末尾
    - 文档头部（无 ``## `` 前缀的 preamble）→ 保留用户头部；用户无头部才用模板头部
    """
    existing_sections = split_sections(existing or "")
    template_sections = split_sections(template or "")

    existing_headings = {h for h, _ in existing_sections if h}
    out: list[tuple[str, str]] = []

    # 用户 preamble（无 heading 的文档开头）优先保留
    user_preamble = ""
    for h, body in existing_sections:
        if not h and body.strip():
            user_preamble = body
            break
    if user_preamble:
        out.append(("", user_preamble))
    # 其余用户 section 按原顺序保留
    for h, body in existing_sections:
        if h:
            out.append((h, body))

    # 模板新增 section 追加；模板 preamble 仅在用户无 preamble 时兜底
    if not user_preamble:
        for h, body in template_sections:
            if not h and body.strip():
                out.append(("", body))
                break
    for h, body in template_sections:
        if h and h not in existing_headings:
            out.append((h, body))

    # 重建文档：heading + body 一并输出（不丢 section 标题）
    return "".join(h + body for h, body in out)


def render_placeholders(
    text: str,
    *,
    project_name: str = "",
    book: str = "",
    platform: str = "",
    author: str = "",
) -> str:
    """替换模板占位符 {项目名}/{书名}/{目标平台}/{作者名}。空值保留占位符原样。"""
    repl = {
        "{项目名}": project_name,
        "{书名}": book,
        "{目标平台}": platform,
        "{作者名}": author,
    }
    for key, value in repl.items():
        if value:
            text = text.replace(key, value)
    return text


# ---------------------------------------------------------------------------
# 输入 / 结果
# ---------------------------------------------------------------------------
@dataclass
class M22SetupInput:
    """M22 部署输入"""

    project_dir: Path | str
    book: str = ""  # 书名（子目录名；空则用项目目录名）
    platform: str = "起点"
    author: str = "作者"
    force: bool = False  # 已部署也重新部署（覆盖 agents，合并 CLAUDE.md/rules）


@dataclass
class M22SetupResult:
    """M22 执行结果"""

    deployed: bool = False  # 本次是否有部署动作（False = 已部署且跳过）
    redeployed: bool = False  # 是否 --force 重新部署
    skipped_existing: bool = False  # 检测到 .story-deployed 且未 force，已跳过
    sentinel: Optional[Path] = None
    deployed_files: list[str] = field(default_factory=list)
    preserved_files: list[str] = field(default_factory=list)  # 合并/保留的既有文件
    notes: list[str] = field(default_factory=list)  # 注意事项

    def to_dict(self) -> dict:
        return {
            "success": True,
            "deployed": self.deployed,
            "redeployed": self.redeployed,
            "skipped_existing": self.skipped_existing,
            "sentinel": str(self.sentinel) if self.sentinel else None,
            "agents_version": AGENTS_VERSION,
            "setup_skill_version": SETUP_SKILL_VERSION,
            "deployed_files": self.deployed_files,
            "preserved_files": self.preserved_files,
            "notes": self.notes,
        }


# ---------------------------------------------------------------------------
# 工作流
# ---------------------------------------------------------------------------
@workflow("m22_setup")
class M22SetupWorkflow:
    """M22 项目脚手架工作流（写作基础设施纯文件部署，无 LLM）"""

    def __init__(
        self,
        project_dir: Path | str,
        templates_dir: Path | None = None,
        console: Console | None = None,
    ) -> None:
        self.project_dir = Path(project_dir)
        self.templates_dir = Path(templates_dir) if templates_dir else TEMPLATES_DIR
        self.console = console or Console()

    # ------ 入口 ------
    def run(self, user_input: M22SetupInput | None = None) -> M22SetupResult:
        inp = user_input or M22SetupInput(project_dir=self.project_dir)
        project = Path(inp.project_dir)
        self.project_dir = project

        project_name = inp.book.strip() or project.name
        book_name = inp.book.strip() or project_name
        sentinel = project / SENTINEL_NAME

        result = M22SetupResult()

        # 1. 已部署检测：.story-deployed 存在且未 --force → 默认跳过并提示
        if sentinel.exists() and not inp.force:
            result.skipped_existing = True
            result.sentinel = sentinel
            result.notes.append(
                f"检测到已部署（{SENTINEL_NAME} 存在），默认跳过；"
                "如需重新部署请加 --force。"
            )
            return result

        project.mkdir(parents=True, exist_ok=True)

        placeholders = {
            "project_name": project_name,
            "book": book_name,
            "platform": inp.platform or "起点",
            "author": inp.author or "作者",
        }

        # 2. CLAUDE.md（合并而非覆盖，按 ## section 合并）
        self._deploy_claude_md(project, placeholders, result)

        # 3. rules（合并：存在保留，缺失复制）
        result.deployed_files += self._deploy_dir(
            "rules", project / ".claude" / "rules",
            overwrite=False, preserved=result.preserved_files, placeholders=placeholders,
        )

        # 4. agents（可覆盖）
        result.deployed_files += self._deploy_dir(
            "agents", project / ".claude" / "agents",
            overwrite=True, preserved=result.preserved_files, placeholders=placeholders,
        )

        # 5. 上下文.md.tmpl（有书名目录时复制到 {书名}/追踪/）
        self._deploy_context(project, book_name, placeholders, result)

        # 6. 创建部署哨兵 .story-deployed
        sentinel.write_text(
            json.dumps(
                {
                    "deployed_at": datetime.now(timezone.utc).strftime(
                        "%Y-%m-%dT%H:%M:%SZ"
                    ),
                    "agents_version": AGENTS_VERSION,
                    "setup_skill_version": SETUP_SKILL_VERSION,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        result.sentinel = sentinel
        result.redeployed = bool(inp.force)
        result.deployed = True
        return result

    # ------ 部署动作 ------
    def _deploy_claude_md(
        self, project: Path, placeholders: dict, result: M22SetupResult
    ) -> None:
        claude_path = project / "CLAUDE.md"
        rendered = self._read_render("CLAUDE.md.tmpl", **placeholders)
        if claude_path.exists():
            merged = merge_markdown_sections(
                claude_path.read_text(encoding="utf-8"), rendered
            )
            claude_path.write_text(merged, encoding="utf-8")
            result.preserved_files.append("CLAUDE.md")
            result.notes.append(
                "CLAUDE.md 已存在，按 ## section 合并"
                "（已有 section 保留，模板新增 section 追加）"
            )
        else:
            claude_path.write_text(rendered, encoding="utf-8")
        result.deployed_files.append("CLAUDE.md")

    def _deploy_dir(
        self,
        rel: str,
        target_dir: Path,
        *,
        overwrite: bool,
        preserved: list[str],
        placeholders: dict,
    ) -> list[str]:
        """从模板目录复制 rel/ 下所有 .md 到 target_dir。

        overwrite=False 时目标已存在同名文件则保留（记入 preserved）。
        """
        src_dir = self.templates_dir / rel
        if not src_dir.is_dir():
            return []
        target_dir.mkdir(parents=True, exist_ok=True)
        deployed: list[str] = []
        for f in sorted(src_dir.glob("*.md")):
            dest = target_dir / f.name
            if dest.exists() and not overwrite:
                preserved.append(f"{rel}/{f.name}")
                continue
            content = render_placeholders(
                f.read_text(encoding="utf-8"), **placeholders
            )
            dest.write_text(content, encoding="utf-8")
            deployed.append(f"{rel}/{f.name}")
        return deployed

    def _deploy_context(
        self, project: Path, book_name: str, placeholders: dict, result: M22SetupResult
    ) -> None:
        book_dir = project / book_name
        ctx_tmpl = self.templates_dir / "上下文.md.tmpl"
        if not book_dir.is_dir():
            result.notes.append(
                f"未检测到书名目录 {book_name}/，跳过 上下文.md.tmpl 部署"
            )
            return
        if not ctx_tmpl.exists():
            return
        track_dir = book_dir / "追踪"
        track_dir.mkdir(parents=True, exist_ok=True)
        content = render_placeholders(
            ctx_tmpl.read_text(encoding="utf-8"), **placeholders
        )
        (track_dir / "上下文.md").write_text(content, encoding="utf-8")
        result.deployed_files.append(f"{book_name}/追踪/上下文.md")

    # ------ 工具 ------
    def _read_render(self, name: str, **placeholders) -> str:
        text = (self.templates_dir / name).read_text(encoding="utf-8")
        return render_placeholders(text, **placeholders)
