"""Dashboard 只读可视化命令（增量 B）

``dashboard`` 是纯只读全局命令：聚合项目结构化产物 → 渲染自包含 HTML（方案甲）
或输出聚合 JSON（``--json``），亦可起本地只读 HTTP 服务（``--serve``，方案乙）。

严格对齐 ``doctor.py`` 的全局只读命令范式：
- ``@command(global_=True)``，由 ``commands/__init__.py`` glob 自动注册，无需手改。
- **不调用** ``enforce_gate``：未初始化项目亦可用，仅依赖逐源 ``try/except`` 降级。
- 仅用标准库（http.server / json / pathlib）+ 已依赖的 jinja2 / typer / rich。
- 复用 ``emit_result`` 的 ``--json`` 契约：成功 ``{success:true, panels:{...}}``；
  极端崩溃才走 ``{success:false, error:{code,message}}`` + 退出码 1。

只读性：``--output`` 默认写 CWD 下的 ``dashboard.html``，**绝不写入项目目录**；
``--serve`` 全程只读、不落盘，Ctrl-C 关闭不残留进程。
"""

from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from agent.cli._app import command, console, typer
from agent.cli._shared import emit_result
from agent.core.infra.dashboard_aggregator import DashboardAggregator, DashboardData


# ============================================================
# 渲染器（方案甲）
# ============================================================
class DashboardRenderer:
    """用 Jinja2 加载 ``dashboard.j2``，注入 ``to_payload()`` 渲染自包含 HTML。"""

    def __init__(self) -> None:
        _here = Path(__file__).parent  # agent/cli/commands
        self.templates_dir = _here.parent / "templates"  # agent/cli/templates
        self.env = Environment(
            loader=FileSystemLoader(str(self.templates_dir)),
            # 数据经 |tojson 注入，文本字段由模板侧 |e 转义；HTML 片段为设计内内容
            autoescape=False,
        )

    def render(self, data: DashboardData, template_name: str = "dashboard.j2") -> str:
        """渲染并返回完整 HTML 字符串。"""
        tpl = self.env.get_template(template_name)
        return tpl.render(payload=data.to_payload())


# ============================================================
# 本地只读服务（方案乙，可选）
# ============================================================
class DashboardServer:
    """标准库 ``http.server`` 起本地只读服务（零新依赖）。

    每次 ``GET /`` 实时重聚合（反映最新 ``.state/``），绑定 ``127.0.0.1`` 仅本机
    访问，全程只读不落盘，Ctrl-C ``shutdown()`` 不残留进程。
    """

    def __init__(self, project_dir: Path, port: int = 8080) -> None:
        self.project_dir = Path(project_dir)
        self.port = port

    def serve(self) -> None:
        from http.server import BaseHTTPRequestHandler, HTTPServer

        aggregator = DashboardAggregator(self.project_dir)
        renderer = DashboardRenderer()

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                try:
                    data = aggregator.aggregate()
                    html = renderer.render(data)
                except Exception as e:  # noqa: BLE001 - 渲染兜底，避免服务崩溃
                    html = (
                        "<html><body><h1>Dashboard 渲染失败</h1>"
                        f"<pre>{e}</pre></body></html>"
                    )
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(html.encode("utf-8"))

            def log_message(self, *args: object) -> None:  # 静默访问日志
                pass

        httpd = HTTPServer(("127.0.0.1", self.port), Handler)
        try:
            print(
                f"Dashboard 只读服务已启动： http://localhost:{self.port}  (Ctrl-C 关闭)"
            )
            httpd.serve_forever()
        except KeyboardInterrupt:
            httpd.shutdown()
            httpd.server_close()


# ============================================================
# 命令
# ============================================================
@command(global_=True)
def dashboard(
    project_dir: str = typer.Option(
        "projects/my-novel", "--dir", "-d", help="小说项目目录"
    ),
    output: str = typer.Option(
        None, "--output", "-o", help="自包含 HTML 输出路径（默认写 CWD/dashboard.html，绝不写入项目目录）"
    ),
    serve: bool = typer.Option(
        False, "--serve", help="起本地只读 HTTP 服务（方案乙，Ctrl-C 关闭）"
    ),
    port: int = typer.Option(8080, "--port", help="--serve 监听端口"),
    json_output: bool = typer.Option(
        False, "--json", help="仅输出聚合 JSON 到 stdout"
    ),
) -> None:
    """可视化 Dashboard（增量 B，只读）

    聚合 relations/graph.md、protagonist_route.md、foreshadows.md、.state/ 与
    Doctor 健康诊断，渲染为自包含只读 HTML 或 JSON。dashboard 只读、**绝不修改**
    任何项目文件（含 .state/）。

    方案甲（默认）：``dashboard -d <dir> --output out.html`` → 生成可双击打开的自包含
    只读 HTML（含 mermaid 关系图 + 路线 + 伏笔 + 进度 + 可选面板占位），退出 0。
    --json：``dashboard -d <dir> --json`` → 输出 ``{success:true, panels:{...}}``；
    任一数据源损坏该面板降级、整体仍 ``success:true``。
    --serve（可选）：``dashboard -d <dir> --serve --port 8080`` 起本地只读服务。

    --json 输出字段：success(恒 True，表示聚合已执行) / panels{relations/route/
    foreshadows/progress/pacing/learnings/rag/health}
    """
    project_path = Path(project_dir)
    try:
        data = DashboardAggregator(project_path).aggregate()
    except Exception as e:
        emit_result(
            {
                "success": False,
                "error": {"code": "dashboard_failed", "message": str(e)},
            },
            json_mode=json_output,
        )
        raise typer.Exit(code=1) from e

    # --json：仅输出聚合 payload，不渲染 HTML
    if json_output:
        emit_result(
            {"success": True, "panels": data.to_payload()},
            json_mode=True,
        )
        return

    # 方案乙：本地只读 HTTP 服务（阻塞）
    if serve:
        DashboardServer(project_path, port=port).serve()
        return

    # 方案甲：渲染 HTML 到 --output（默认 CWD/dashboard.html，绝不进项目目录）
    html = DashboardRenderer().render(data)
    out_path = Path(output) if output else Path("dashboard.html")
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(html, encoding="utf-8")
    except OSError as e:  # 父目录不可建/无写权限时走错误契约，不裸 traceback
        if json_output:
            emit_result(
                {
                    "success": False,
                    "error": {
                        "code": "OUTPUT_WRITE_FAILED",
                        "message": f"写入 {out_path} 失败：{e}",
                    },
                },
                json_mode=True,
            )
        else:
            console.print(f"[bold red]✗ 写入 {out_path} 失败[/bold red]：{e}")
        raise typer.Exit(code=1) from e

    emit_result(
        {
            "success": True,
            "output": str(out_path),
            "panels_summary": _summary(data),
        },
        rich_render=lambda: _render(data, out_path),
    )


def _summary(data: DashboardData) -> dict[str, bool]:
    """面板可用性摘要（供 --json 之外的富文本/调试使用）。"""
    return {
        "relations": data.relations.available,
        "route": data.route.available,
        "foreshadows": data.foreshadows.available,
        "progress": data.progress.available,
        "pacing": data.pacing.available,
        "learnings": data.learnings.available,
        "rag": data.rag.available,
        "health": data.health.available,
    }


def _render(data: DashboardData, out_path: Path) -> None:
    """非 json 模式下的 rich 摘要渲染。"""
    console.print(f"[bold green]✓ Dashboard 已生成[/bold green] {out_path}")
    console.print(f"[dim]项目：{data.project_dir} · 生成于 {data.generated_at}[/dim]")
    s = _summary(data)
    parts = "  ".join(
        f"{name}={'✓' if ok else '—'}" for name, ok in s.items()
    )
    console.print(f"[dim]面板：{parts}[/dim]")
