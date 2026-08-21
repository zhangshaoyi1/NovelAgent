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
        request, "home.html", {"request": request, "projects": projects}
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
        },
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


# ============================================================
# JSON API
# ============================================================
@app.get("/api/genres")
def api_genres() -> JSONResponse:
    return JSONResponse(state.list_genres())


@app.get("/api/state/{name}")
def api_state(name: str) -> JSONResponse:
    return JSONResponse(state.get_project_state(name))


@app.get("/api/chapters/{name}")
def api_chapters(name: str) -> JSONResponse:
    return JSONResponse(state.get_chapters(name))


@app.post("/api/projects")
async def create_project(
    name: str = Form(...),
    title: str = Form(...),
    scope: str = Form("long"),
    genre: str = Form("xiuxian"),
    story_core: str = Form(""),
) -> JSONResponse:
    safe = runner.sanitize_project_name(name)
    pdir = state.project_path(safe)
    if pdir.exists() and any(pdir.iterdir()):
        raise HTTPException(status_code=400, detail="项目已存在且非空")
    pdir.mkdir(parents=True, exist_ok=True)
    argv = ["--title", title, "--scope", scope, "--genre", genre, "--story-core", story_core]
    run_id = runner.run_manager.new_run(safe, "start", argv)
    asyncio.create_task(runner.run_manager.execute(run_id))
    return JSONResponse({"run_id": run_id, "project": safe})


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
