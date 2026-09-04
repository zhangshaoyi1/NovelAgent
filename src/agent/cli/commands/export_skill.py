"""export_skill 命令 —— 跨平台 skill 分发（G11 P0-3，拍板 4：标准 SKILL.md 契约 + 内容 + README + 可选 zip）。

把 ``src/agent/skills/<name>/`` 打包为可被任意 AI 工具（Claude/Cursor 等）加载的
标准分发目录到 ``out_dir/<name>/``。纯确定性文件操作、零 LLM；只读源目录、
只写输出目录（dist/skills/ 已 .gitignore 不入库）。

用法：
    novel-agent export-skill                       # 默认导出 bookworm 到 dist/skills/
    novel-agent export-skill -s all --zip          # 导出全部 skill 并打包 zip
    novel-agent export-skill -s bookworm -o out --json
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from agent.cli._app import app, command, console, typer
from agent.cli._shared import *  # noqa: F401,F403 - emit_result / make_quiet_console

# skill 源目录：src/agent/skills/
_SKILLS_SRC = Path(__file__).resolve().parents[2] / "skills"

# Anthropic Agent Skills 最小契约（G11 设计 §4.2 / PRD 拍板 B3）
_CONTRACT_KEYS = ("name", "description", "version", "type")


def _cli_value(v: Any, default: Any) -> Any:
    """归一化 CLI 参数：经 typer 真实调用时值为标量；直接函数调用时还原 OptionInfo。"""
    if hasattr(v, "default"):
        return v.default
    return v


def _read_frontmatter(skill_dir: Path) -> dict[str, Any]:
    """读取 SKILL.md frontmatter；缺失契约字段时尽力补全。

    description 缺失 → 从正文首行 '# ' 标题提取；仍缺 → 抛 ValueError（调用方报错信封）。
    """
    try:
        import frontmatter

        post = frontmatter.load(skill_dir / "SKILL.md")
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"SKILL.md 解析失败：{e}") from e
    meta = dict(post.metadata or {})
    if not meta.get("name"):
        meta["name"] = skill_dir.name
    if not meta.get("version"):
        meta["version"] = "0.1.0"
    if not meta.get("type"):
        meta["type"] = "skill"
    if not meta.get("description"):
        for line in (post.content or "").splitlines():
            if line.strip().startswith("# "):
                meta["description"] = line.strip()[2:].strip()
                break
    missing = [k for k in _CONTRACT_KEYS if not meta.get(k)]
    if missing:
        raise ValueError(
            f"SKILL.md 契约字段缺失：{','.join(missing)}（需 name/description/version/type）"
        )
    return meta


def _copy_files(src: Path, dst: Path) -> list[str]:
    """复制全部内容文件（含子目录）到 dst，返回相对路径清单。"""
    copied: list[str] = []
    for f in sorted(src.rglob("*")):
        if f.is_file():
            rel = f.relative_to(src)
            target = dst / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, target)
            copied.append(str(rel).replace("\\", "/"))
    return copied


def _write_readme(dst: Path, name: str, files: list[str]) -> None:
    """生成 README.md（加载指引：如何把该目录作为 skill 提供给 AI 工具）。"""
    readme = (
        f"# {name} · 可分发 Skill\n\n"
        "本目录由 `novel-agent export-skill` 生成，为标准 Anthropic Agent Skills 形态，"
        "可被 Claude / Cursor 等支持 skill 的 AI 工具直接加载。\n\n"
        "## 加载方式\n\n"
        "1. 将本目录放入工具约定的 skills 目录（如 `~/.claude/skills/<name>/`）；\n"
        "2. 或在对话中引用目录路径，工具会读取 `SKILL.md` 获取能力说明；\n"
        "3. 以 zip 分发时解压到 skills 目录后同上。\n\n"
        "## 内容清单\n\n"
        + "\n".join(f"- {f}" for f in files)
        + "\n\n## 说明\n\n"
        "SKILL.md frontmatter 契约：name / description / version / type。"
        "`commands` 为 NovelAgent 扩展字段（其余工具可忽略）。\n"
    )
    (dst / "README.md").write_text(readme, encoding="utf-8")


def export_one(
    name: str, out_dir: str | Path, zip_pkg: bool = False
) -> dict[str, Any]:
    """导出单个 skill 到 out_dir/<name>/。

    Returns:
        {"name", "out", "files", "zip"}；异常抛 ValueError（调用方处理）。
    """
    src = _SKILLS_SRC / name
    if not src.is_dir():
        raise ValueError(f"skill 不存在：{name}（src/agent/skills/{name}/）")
    if not (src / "SKILL.md").exists():
        raise ValueError(f"skill 缺少 SKILL.md：{name}")

    _read_frontmatter(src)  # 校验契约（缺失抛 ValueError）

    out = Path(out_dir) / name
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)

    files = _copy_files(src, out)
    _write_readme(out, name, files)

    zip_path = None
    if zip_pkg:
        base = Path(out_dir) / name
        zip_path = str(shutil.make_archive(str(base), "zip", root_dir=out_dir, base_dir=name))
    return {
        "name": name,
        "out": str(out.resolve()),
        "files": files,
        "zip": zip_path,
    }


@command(global_=True)
def export_skill(
    skill: str = typer.Option(
        "bookworm", "--skill", "-s", help="skill 名或 all（导出全部）"
    ),
    out_dir: str = typer.Option(
        "dist/skills", "--out", "-o", help="输出目录（默认 dist/skills/，不入库）"
    ),
    zip_pkg: bool = typer.Option(
        False, "--zip", help="同时打包 zip"
    ),
    json_output: bool = typer.Option(
        False, "--json", help="以 JSON 形式输出导出清单到 stdout"
    ),
    env_file: str = typer.Option(None, "--env", help="指定 .env 文件（透传）"),
) -> None:
    """跨平台 skill 分发 - 把内置 skill 导出为标准 SKILL.md 分发目录

    只读 src/agent/skills/<name>/，输出到 out_dir/<name>/（含 README 加载指引，
    --zip 可打包）。纯确定性文件操作，不修改书稿/源码。
    """
    _env_file = _cli_value(env_file, None)
    if _env_file:
        import os

        os.environ["NOVEL_AGENT_DOTENV"] = _env_file

    _skill = _cli_value(skill, "bookworm")
    _out = _cli_value(out_dir, "dist/skills")
    _zip = bool(_cli_value(zip_pkg, False))
    _json = bool(_cli_value(json_output, False))

    from agent.cli._shared import enforce_gate

    enforce_gate("projects/my-novel", "export_skill", json_mode=_json)  # 只读命令，状态放行

    try:
        if _skill == "all":
            names = sorted(
                d.name for d in _SKILLS_SRC.iterdir() if d.is_dir() and d.name != "__pycache__"
            )
            results = [export_one(n, _out, _zip) for n in names]
        else:
            results = [export_one(_skill, _out, _zip)]
    except ValueError as e:
        if _json:
            emit_result({"success": False, "error": str(e)}, json_mode=True)
        else:
            console.print(f"[bold red]✗ 导出失败：{e}[/bold red]")
        raise typer.Exit(code=1)

    if _json:
        emit_result(
            {"success": True, "skills": results, "out_dir": str(Path(_out).resolve())},
            json_mode=True,
        )
        return

    workflow_console = make_quiet_console() if _json else console
    workflow_console.print("[bold cyan]skill 导出完成[/bold cyan]")
    for r in results:
        workflow_console.print(
            f"  ✓ {r['name']} → {r['out']}（{len(r['files'])} 文件"
            + (f"，zip: {r['zip']}" if r.get("zip") else "")
            + "）"
        )
    workflow_console.print(
        "[dim]加载指引：将目录放入工具的 skills 目录（如 ~/.claude/skills/<name>/），工具读取 SKILL.md 即可。[/dim]"
    )
