"""NovelAgent Web UI（FastAPI + Jinja2 + SSE）。

页面（服务端渲染 + HTMX 局部刷新）：
- /                       工作台：项目列表 / 新建
- /p/{name}              项目空间：状态机进度 + 当前可用操作
- /p/{name}/guide        引导向导：按状态机阶段走通创作闭环
- /p/{name}/write        实时写作间：写章 SSE 进度 + 成本视图
- /p/{name}/dashboard    看板：成本 / 评测 / 模型路由 / MCP
- /p/{name}/files        文件浏览
- /p/{name}/file?path=   单文件查看

API：
- POST /api/projects     新建项目（非交互 start）
- POST /api/run          通用命令运行（command + args）
- GET  /api/runs/{id}/events  SSE 事件流
- GET  /api/state/{name} 项目状态 JSON（前端轮询刷新用）
- GET  /api/genres       题材列表
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from agent.web import runner, state
from agent.agents.registry import get_groups, roster_summary, RosterCategory
from agent.core.story.philosophy import get_philosophy
from agent.workflows.m8_mode import ModeController, autonomy_label
from agent.core.story.relation_manager import (
    RelationManager,
    WorldNode,
    WorldEdge,
    NODE_KIND_LABELS,
    NODE_KIND_COLORS,
)

# 触发所有 CLI 命令的 @command 注册（含动态注册的 compose / autowrite 等），
# 使 Web 端的 available_commands 与 CLI 保持一致。
import agent.cli.commands  # noqa: F401  副作用：命令元数据登记

app = FastAPI(title="NovelAgent Web UI", version="0.1.0")

_HERE = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(_HERE / "templates"))
app.mount("/static", StaticFiles(directory=str(_HERE / "static")), name="static")


# ============================================================
# 页面路由
# ============================================================
@app.get("/", response_class=HTMLResponse)
def home(request: Request) -> HTMLResponse:
    projects = state.list_projects()
    return templates.TemplateResponse(
        request,
        "home.html",
        {"request": request, "projects": projects, "philosophy": get_philosophy()},
    )


@app.get("/p/{name}", response_class=HTMLResponse)
def project(request: Request, name: str) -> HTMLResponse:
    ps = state.get_project_state(name)
    meta = state.get_command_meta()
    return templates.TemplateResponse(
        request,
        "project.html",
        {
            "request": request,
            "name": name,
            "ps": ps,
            "meta": meta,
            "flow": state.STATE_FLOW,
            "conflict_pending": state.get_conflicts(name)["pending"],
        },
    )


@app.get("/p/{name}/conflicts", response_class=HTMLResponse)
def conflicts_page(request: Request, name: str) -> HTMLResponse:
    ps = state.get_project_state(name)
    return templates.TemplateResponse(
        request,
        "conflicts.html",
        {"request": request, "name": name, "ps": ps},
    )


@app.get("/p/{name}/guide", response_class=HTMLResponse)
def guide(request: Request, name: str) -> HTMLResponse:
    ps = state.get_project_state(name)
    genres = state.list_genres()
    return templates.TemplateResponse(
        request,
        "guide.html",
        {
            "request": request,
            "name": name,
            "ps": ps,
            "genres": genres,
            "flow": state.STATE_FLOW,
        },
    )


@app.get("/p/{name}/write", response_class=HTMLResponse)
def writer(request: Request, name: str) -> HTMLResponse:
    ps = state.get_project_state(name)
    return templates.TemplateResponse(
        request, "writer.html", {"request": request, "name": name, "ps": ps}
    )


@app.get("/p/{name}/dashboard", response_class=HTMLResponse)
def dashboard(request: Request, name: str) -> HTMLResponse:
    ps = state.get_project_state(name)
    summary = state.get_summary(name)
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {"request": request, "name": name, "ps": ps, "summary": summary},
    )


@app.get("/p/{name}/files", response_class=HTMLResponse)
def files_page(request: Request, name: str) -> HTMLResponse:
    files = state.list_project_files(name)
    return templates.TemplateResponse(
        request, "files.html", {"request": request, "name": name, "files": files}
    )


@app.get("/p/{name}/file", response_class=HTMLResponse)
def file_view(request: Request, name: str, path: str = "") -> HTMLResponse:
    content = state.read_project_file(name, path)
    if content is None:
        raise HTTPException(status_code=404, detail="文件不存在或越权访问")
    return templates.TemplateResponse(
        request,
        "file.html",
        {"request": request, "name": name, "path": path, "content": content},
    )


@app.get("/p/{name}/team", response_class=HTMLResponse)
def team_page(request: Request, name: str) -> HTMLResponse:
    """Agent 阵容页面：展示「编制完整创作团队」"""
    groups = [
        {
            "category": g.category.value,
            "tagline": g.tagline,
            "agents": [
                {
                    "glyph": a.glyph,
                    "name": a.name,
                    "responsibility": a.responsibility,
                    "engine": a.engine,
                    "trait": a.trait,
                }
                for a in g.agents
            ],
        }
        for g in get_groups()
    ]
    return templates.TemplateResponse(
        request,
        "team.html",
        {
            "request": request,
            "name": name,
            "summary": roster_summary(),
            "groups": groups,
            "total": sum(len(g["agents"]) for g in groups),
        },
    )


@app.get("/p/{name}/graph", response_class=HTMLResponse)
def graph_page(request: Request, name: str) -> HTMLResponse:
    """可拖拽世界关系图谱页面（vis-network 力导向，节点按类型着色）"""
    return templates.TemplateResponse(
        request, "graph.html", {"request": request, "name": name}
    )


@app.get("/api/relations/{name}")
def api_relations(name: str) -> JSONResponse:
    """读取世界关系图谱（JSON，供前端 vis-network 消费）"""
    rm = RelationManager(state.project_path(name))
    rm.load()
    return JSONResponse(
        {
            "nodes": [n.to_dict() for n in rm.graph.nodes],
            "edges": [e.to_dict() for e in rm.graph.edges],
            "kinds": NODE_KIND_LABELS,
            "colors": NODE_KIND_COLORS,
            "empty": not rm.exists(),
        }
    )


@app.post("/api/relations/{name}")
async def api_relations_update(request: Request, name: str) -> JSONResponse:
    """全量保存世界关系图谱（含拖拽坐标 / 新增节点与边）"""
    data = await request.json()
    rm = RelationManager(state.project_path(name))
    rm.load()
    if isinstance(data.get("nodes"), list):
        rm.graph.nodes = [WorldNode.from_dict(n) for n in data["nodes"]]
    if isinstance(data.get("edges"), list):
        rm.graph.edges = [WorldEdge.from_dict(e) for e in data["edges"]]
    rm.save()
    return JSONResponse(
        {"ok": True, "nodes": len(rm.graph.nodes), "edges": len(rm.graph.edges)}
    )


@app.post("/api/relations/{name}/seed")
def api_relations_seed(name: str) -> JSONResponse:
    """一键填充示例世界图谱（人物/势力/地点/物品/伏笔）"""
    rm = RelationManager(state.project_path(name))
    rm.seed_sample()
    return JSONResponse(
        {"ok": True, "nodes": len(rm.graph.nodes), "edges": len(rm.graph.edges)}
    )


@app.get("/api/roster")
def api_roster() -> JSONResponse:
    """Agent 阵容 JSON（前端消费）"""
    return JSONResponse(
        {
            "summary": roster_summary(),
            "groups": [
                {
                    "category": g.category.value,
                    "tagline": g.tagline,
                    "agents": [
                        {
                            "glyph": a.glyph,
                            "name": a.name,
                            "responsibility": a.responsibility,
                            "engine": a.engine,
                            "trait": a.trait,
                        }
                        for a in g.agents
                    ],
                }
                for g in get_groups()
            ],
        }
    )


# ============================================================
# JSON API
# ============================================================
@app.get("/api/genres")
def api_genres() -> JSONResponse:
    return JSONResponse(state.list_genres())


@app.get("/api/state/{name}")
def api_state(name: str) -> JSONResponse:
    return JSONResponse(state.get_project_state(name))


@app.post("/api/mode")
async def api_set_mode(name: str = Form(...), autonomy: int = Form(...)) -> JSONResponse:
    """设置双模式连续自主度（0-100），同步 legacy mode 字段"""
    ctrl = ModeController(project_dir=state.project_path(name))
    result = ctrl.set_autonomy(int(autonomy))
    level = ctrl.autonomy
    return JSONResponse(
        {"autonomy": level, "label": autonomy_label(level), "message": result.message}
    )


@app.get("/api/chapters/{name}")
def api_chapters(name: str) -> JSONResponse:
    return JSONResponse(state.get_chapters(name))


@app.post("/api/projects")
async def create_project(
    name: str = Form(...),
    title: str = Form(...),
    scope: str = Form("long"),
    genre: str = Form("xiuxian"),
    genres: str = Form(""),
    story_core: str = Form(""),
) -> JSONResponse:
    safe = runner.sanitize_project_name(name)
    pdir = state.project_path(safe)
    if pdir.exists() and any(pdir.iterdir()):
        raise HTTPException(status_code=400, detail="项目已存在且非空")
    pdir.mkdir(parents=True, exist_ok=True)
    # 多题材：--genres（逗号分隔）优先，兼容旧 --genre 单值
    if genres.strip():
        genre_argv = ["--genres", genres.replace("，", ",")]
    else:
        genre_argv = ["--genre", genre]
    argv = ["--title", title, "--scope", scope, *genre_argv, "--story-core", story_core]
    run_id = runner.run_manager.new_run(safe, "start", argv)
    asyncio.create_task(runner.run_manager.execute(run_id))
    return JSONResponse({"run_id": run_id, "project": safe})


@app.get("/api/conflicts/{name}")
def api_conflicts(name: str) -> JSONResponse:
    return JSONResponse(state.get_conflicts(name))


@app.post("/api/conflicts/{name}/resolve")
async def resolve_conflicts(
    name: str,
    decisions: str = Form(""),
) -> JSONResponse:
    """按裁决 JSON 运行 merge-genres --decisions（非交互写回 world.md）。"""
    safe = runner.sanitize_project_name(name)
    argv = []
    if decisions.strip():
        argv += ["--decisions", decisions]
    else:
        argv += ["--auto"]
    run_id = runner.run_manager.new_run(safe, "merge-genres", argv)
    asyncio.create_task(runner.run_manager.execute(run_id))
    return JSONResponse({"run_id": run_id})


@app.post("/api/run")
async def api_run(
    project: str = Form(...),
    command: str = Form(...),
    args: str = Form(""),
    argv_json: str = Form(""),
) -> JSONResponse:
    # argv_json 优先（结构化参数列表，避免 shell 引号转义问题）；否则按字符串切分
    if argv_json.strip():
        try:
            argv = json.loads(argv_json)
            if not isinstance(argv, list):
                raise ValueError("argv_json 必须是数组")
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=f"argv_json 解析失败：{e}")
    else:
        argv = runner.split_args(args)
    run_id = runner.run_manager.new_run(project, command, argv)
    asyncio.create_task(runner.run_manager.execute(run_id))
    return JSONResponse({"run_id": run_id})


@app.get("/api/runs/{run_id}/events")
async def run_events(run_id: str) -> StreamingResponse:
    if run_id not in runner.run_manager.runs:
        raise HTTPException(status_code=404, detail="运行不存在")

    async def gen() -> Any:
        async for ev in runner.run_manager.stream(run_id):
            yield (
                f"event: {ev['type']}\n"
                f"data: {json.dumps(ev['data'], ensure_ascii=False)}\n\n"
            )

    return StreamingResponse(gen(), media_type="text/event-stream")
