from __future__ import annotations

from agent.cli._app import app, console, typer, command
from agent.cli._shared import *

@command(global_=True)
def inject_genre(
    name: str | None = typer.Argument(
        None, help="套路名（如 逆袭 / 绝境逆袭），支持模糊匹配；与 --clear 互斥"
    ),
    genre: str = typer.Option(
        "", "--genre", "-g", help="题材名（默认取 world.md 的 genre 字段）"
    ),
    clear: bool = typer.Option(
        False, "--clear", help="清除已注入的套路（忽略 name）"
    ),
    project_dir: str = typer.Option(
        "projects/my-novel", "--dir", "-d", help="小说项目目录"
    ),
) -> None:
    """E2 运行时注入题材套路到下一章写作

    将指定套路（来自题材包 tropes.md）写入独立的 ``.state/injected_tropes.json``
    （运行期上下文，不污染 state.json），下一章 write 时自动拼入 system prompt；
    生成后自动清除。

    使用示例：
      novel-agent inject-genre 逆袭 -d projects/my-novel
      novel-agent inject-genre --clear -d projects/my-novel
    """
    from pathlib import Path

    from agent.core.genre_pack import GenrePackRegistry
    from agent.core.injected_trope_store import InjectedTropeStore
    from agent.core.setting_manager import SettingManager

    project_path = Path(project_dir)
    enforce_gate(str(project_path), "inject_genre")
    if not (project_path / "world.md").exists():
        console.print(
            f"[bold red]✗[/bold red] {project_path / 'world.md'} 不存在，请先运行 start"
        )
        raise typer.Exit(code=1)

    store = InjectedTropeStore(project_path)

    if clear:
        store.clear()
        console.print("[bold green]✓ 已清除注入的套路[/bold green]")
        return

    if not name:
        console.print(
            "[bold red]✗[/bold red] 请指定要注入的套路名，或使用 --clear 清空"
        )
        raise typer.Exit(code=1)

    # 解析题材（默认取 world.md 的 genre 字段）
    sm_setting = SettingManager(project_path)
    genre_name = genre or sm_setting.load_world()["metadata"].get("genre", "") or "xiuxian"

    registry = GenrePackRegistry()
    try:
        trope = registry.load_trope(genre_name, name)
    except ValueError as e:
        console.print(f"[bold red]✗[/bold red] {e}")
        raise typer.Exit(code=1) from e

    # 累积注入（去重）
    current = store.add(trope.name)

    console.print(
        f"[bold green]✓ 已注入套路『{trope.name}』（题材 {genre_name}）[/bold green]"
    )
    console.print("[dim]下一章 write 将自动融入该套路，生成后自动清除[/dim]")
    console.print(f"[dim]当前注入：{', '.join(current)}[/dim]")
