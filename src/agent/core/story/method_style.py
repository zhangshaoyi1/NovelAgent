"""G11 竞品借鉴三件套：风格模仿 / 写作方法模板 的读取 helper。

约定（见 G11/设计.md §11 共享知识）：
- project 根 ``style.md``：用户写文风描述/样本，正文即注入文本（≤800 字截断）。
- project 根 ``method.md``：写作方法模板正文（用户可编辑覆盖）；``--method`` 选择内置
  模板（src/agent/methods/<name>.md）时写入该文件。
- 全降级不阻断：文件缺失/读取失败/内容为空 → 返回空串，绝不抛异常。
"""

from __future__ import annotations

from pathlib import Path

# 风格指引正文截断上限（防 prompt 膨胀）
STYLE_GUIDE_MAX_CHARS = 800
# 方法模板正文截断上限（模板文件本身控制，双保险）
METHOD_TEXT_MAX_CHARS = 2000

# 内置写作方法模板目录（相对 agent 包根：src/agent/methods/；本文件位于
# .../core/story/，需上溯 3 层到 agent/）
_METHODS_DIR = Path(__file__).resolve().parent.parent.parent / "methods"


def load_style_guide(
    project_dir: str | Path,
    enabled: bool = True,
    style_file: str | None = None,
) -> str:
    """读取风格指引文本。

    Args:
        project_dir: 小说项目目录（默认读 project/style.md）。
        enabled: False 时直接返回 ""（--no-style）。
        style_file: 指定风格文件路径（--style-file）；None 时读 project/style.md。

    Returns:
        风格指引正文（≤800 字）；缺失/失败/为空 → ""。
    """
    if not enabled:
        return ""
    try:
        p = Path(style_file) if style_file else Path(project_dir) / "style.md"
        if not p.exists():
            return ""
        text = p.read_text(encoding="utf-8").strip()
        if not text:
            return ""
        return text[:STYLE_GUIDE_MAX_CHARS]
    except Exception:  # noqa: BLE001 - 读取失败降级为空，不阻断
        return ""


def load_method_text(
    project_dir: str | Path,
    enabled: bool = True,
    method: str | None = None,
    methods_dir: str | Path | None = None,
) -> tuple[str, str]:
    """加载写作方法模板文本。

    Args:
        project_dir: 小说项目目录（写/读 project/method.md）。
        enabled: False 时返回 ("", "")（--no-method）。
        method: 内置模板名（three_act / hero_journey / qi_cheng_zhuan_he）；
            None 时读已存在的 project/method.md。
        methods_dir: 内置模板目录覆盖（测试用）；None 用 src/agent/methods/。

    Returns:
        (method_text, method_name)：正文与名称；缺失/失败 → ("", "")。
    """
    if not enabled:
        return "", ""
    proj = Path(project_dir)
    method_file = proj / "method.md"
    try:
        if method:
            mdir = Path(methods_dir) if methods_dir else _METHODS_DIR
            src = mdir / f"{method}.md"
            if not src.exists():
                return "", ""
            name = method
            text = _strip_template_title(src.read_text(encoding="utf-8")).strip()
            if not text:
                return "", ""
            # 写入 project/method.md（用户可再编辑覆盖；写失败不阻断注入）
            try:
                method_file.parent.mkdir(parents=True, exist_ok=True)
                method_file.write_text(
                    f"# {name}\n\n{text}", encoding="utf-8"
                )
            except Exception:  # noqa: BLE001 - 写失败不阻断
                pass
        else:
            if not method_file.exists():
                return "", ""
            text = _strip_template_title(method_file.read_text(encoding="utf-8")).strip()
            if not text:
                return "", ""
            # 名称取首行标题（无则空）
            name = ""
            for line in method_file.read_text(encoding="utf-8").splitlines():
                if line.strip().startswith("# "):
                    name = line.strip()[2:].strip()
                    break
        return text[:METHOD_TEXT_MAX_CHARS], name
    except Exception:  # noqa: BLE001 - 读取失败降级为空，不阻断
        return "", ""


def _strip_template_title(text: str) -> str:
    """去除模板文件首行 '# 名称' 标题（标题不入注入正文）。"""
    lines = text.splitlines()
    if lines and lines[0].strip().startswith("# "):
        return "\n".join(lines[1:])
    return text
