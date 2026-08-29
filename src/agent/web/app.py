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
- GET  /api/runs/{id}        run 状态查询（前端轮询兜底，防 SSE 丢事件假死）
- GET  /api/state/{name} 项目状态 JSON（前端轮询刷新用）
- GET  /api/genres       题材列表
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any

import markdown as _md
import frontmatter
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from agent.web import runner, state
from agent.agents.registry import get_groups, roster_summary, RosterCategory
from agent.core.story.meta.philosophy import get_philosophy
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


def _md_filter(text: str | None) -> str:
    """把阶段产物 markdown 渲染为 HTML（剥离 YAML frontmatter，仅渲染正文）。

    供模板 `{{ content|md|safe }}` 使用：生成/回显的内容默认以富文本预览呈现。
    """
    if not text:
        return ""
    try:
        body = frontmatter.loads(text).content or text
    except Exception:  # noqa: BLE001 - 无 frontmatter / 解析失败降级为原文
        body = text
    try:
        return _md.markdown(body, extensions=["tables", "fenced_code", "sane_lists"])
    except Exception:  # noqa: BLE001 - 渲染失败降级为转义原文，不阻断页面
        return _md.markdown(body)


templates.env.filters["md"] = _md_filter


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
    next_action = state.build_next_action(ps["state"], ps["available_commands"])
    return templates.TemplateResponse(
        request,
        "project.html",
        {
            "request": request,
            "name": name,
            "ps": ps,
            "meta": meta,
            "state_meta": state.STATE_META,
            "next": next_action,
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


# ============================================================
# 引导向导：7 个阶段各一页，整体样式统一（可点进度条 + 上/下一步）
# ============================================================

# 7 个阶段页面（key 用于路由 / 阶段数据对齐）
GUIDE_PHASES = [
    {"key": "world", "num": "1", "label": "开新书", "state": "INIT", "desc": "创建项目并生成世界观设定集"},
    {"key": "discussion", "num": "2", "label": "脉络讨论", "state": "DISCUSSING", "desc": "与 Agent 讨论故事脉络，产出讨论纪要"},
    {"key": "architecture", "num": "3", "label": "故事架构", "state": "ARCHITECTING", "desc": "生成整体故事架构初稿，可反馈迭代"},
    {"key": "confirm", "num": "4", "label": "确认架构", "state": "ARCH_CONFIRMED", "desc": "确认后解锁下游（大纲 / 角色 / 写作）"},
    {"key": "outline", "num": "5", "label": "创作大纲", "state": "OUTLINING", "desc": "生成章节大纲"},
    {"key": "characters", "num": "6", "label": "角色设计", "state": "CHARACTER_DESIGN", "desc": "设计主要角色与关系"},
    {"key": "write", "num": "7", "label": "写章节", "state": "WRITING", "desc": "逐章推进正文"},
]

# 状态机状态 → 引导阶段 key（用于 /guide 重定向到当前阶段页）
STATE_TO_GUIDE_KEY = {
    "INIT": "world",
    "CONFIGURING": "world",
    "DISCUSSING": "discussion",
    "ARCHITECTING": "architecture",
    "ARCH_REVISION": "architecture",
    "ARCH_CONFIRMED": "confirm",
    "OUTLINING": "outline",
    "CHARACTER_DESIGN": "characters",
    "WRITING": "write",
    "PAUSED": "write",
    "COMPLETED": "write",
}

GUIDE_PHASE_BY_KEY = {p["key"]: p["label"] for p in GUIDE_PHASES}


def _guide_stages(name: str) -> list[dict[str, Any]]:
    """构建 7 个阶段页面的数据（含产物回显 / 可用命令 / 确认态）。"""
    ps = state.get_project_state(name)
    avail = ps["available_commands"]
    world = state.read_project_file(name, "world.md") or ""
    discussion = state.read_project_file(name, "discussion.md") or ""
    architecture = state.read_project_file(name, "architecture.md") or ""
    outline = state.read_project_file(name, "outline.md") or ""
    chars: list[dict[str, Any]] = []
    cdir = state.project_path(name) / "characters"
    if cdir.exists():
        chars = [
            {
                "rel": "characters/" + p.name,
                "name": p.stem,
                "content": state.read_project_file(name, "characters/" + p.name) or "",
            }
            for p in sorted(cdir.glob("*.md"))
        ]
    write_avail = "/write" in avail
    return [
        {"key": "world", "num": "①", "label": "设定世界", "desc": "世界观设定集（题材 / 核心梗 / 世界规则）",
         "cmd": "/start", "file": "world.md", "content": world, "editable": bool(world),
         "generate": "/start" in avail, "gen_label": "生成世界观", "gen_argv": []},
        {"key": "discussion", "num": "②", "label": "脉络讨论", "desc": "与 Agent 讨论故事脉络，产出讨论纪要",
         "cmd": "/discuss", "file": "discussion.md", "content": discussion, "editable": bool(discussion),
         "generate": "/discuss" in avail, "gen_label": "开始讨论",
         "gen_argv": ["--message", "请基于当前世界观设定，提出3-5个关键创作问题并给出初步方向建议"]},
        {"key": "architecture", "num": "③", "label": "故事架构", "desc": "整体故事架构初稿，可反馈迭代",
         "cmd": "/architecture", "file": "architecture.md", "content": architecture, "editable": bool(architecture),
         "generate": "/architecture" in avail, "gen_label": "生成架构", "gen_argv": []},
        {"key": "confirm", "num": "④", "label": "架构确认", "desc": "确认后解锁下游（大纲 / 角色 / 写作）",
         "cmd": "/confirm-architecture", "file": None, "content": architecture, "editable": False,
         "generate": "/confirm-architecture" in avail, "gen_label": "确认并解锁", "gen_argv": ["--yes"]},
        {"key": "outline", "num": "⑤", "label": "创作大纲", "desc": "章节大纲",
         "cmd": "/outline", "file": "outline.md", "content": outline, "editable": bool(outline),
         "generate": "/outline" in avail, "gen_label": "生成大纲", "gen_argv": []},
        {"key": "characters", "num": "⑥", "label": "角色设计", "desc": "主要角色卡与关系",
         "cmd": "/design-characters", "file": None, "content": None, "editable": bool(chars), "chars": chars,
         "generate": "/design-characters" in avail, "gen_label": "设计角色", "gen_argv": []},
        {"key": "write", "num": "⑦", "label": "写章节", "desc": "进入实时写作间逐章推进正文",
         "cmd": "/write", "file": None, "content": None, "editable": False,
         "generate": write_avail, "gen_label": "写下一章", "gen_argv": []},
    ]


def _guide_ctx(name: str, stage_key: str) -> dict[str, Any]:
    """单个引导阶段的页面上下文（阶段卡 + 上/下一步 + 进度条状态）。"""
    ps = state.get_project_state(name)
    stages = _guide_stages(name)
    keys = [p["key"] for p in GUIDE_PHASES]
    if stage_key not in keys:
        stage_key = STATE_TO_GUIDE_KEY.get(ps["state"], "world")
    stage = next((s for s in stages if s["key"] == stage_key), stages[0])
    cur_idx = keys.index(stage_key)
    prev_key = keys[cur_idx - 1] if cur_idx > 0 else None
    next_key = keys[cur_idx + 1] if cur_idx < len(keys) - 1 else None
    phase = next((p for p in GUIDE_PHASES if p["key"] == stage_key), GUIDE_PHASES[0])
    return {
        "name": name,
        "ps": ps,
        "phases": GUIDE_PHASES,
        "phase_by_key": GUIDE_PHASE_BY_KEY,
        "phase": phase,
        "stage": stage,
        "stage_status": state.stage_status_map(name),
        "flow": state.STATE_FLOW,
        "prev_key": prev_key,
        "next_key": next_key,
        "genres": state.list_genres(),
        "projects": state.list_projects(),
        "avail": ps["available_commands"],
    }


@app.get("/p/{name}/guide", response_class=HTMLResponse)
def guide(request: Request, name: str) -> HTMLResponse:
    """引导向导入口：按当前状态重定向到对应阶段页面。"""
    ps = state.get_project_state(name)
    key = STATE_TO_GUIDE_KEY.get(ps["state"], "world")
    return RedirectResponse(url=f"/p/{name}/guide/{key}")


@app.get("/p/{name}/guide/{stage}", response_class=HTMLResponse)
def guide_stage(request: Request, name: str, stage: str) -> HTMLResponse:
    """单个引导阶段页面（7 个阶段各一页，样式统一）。"""
    ctx = _guide_ctx(name, stage)
    ctx["request"] = request
    return templates.TemplateResponse(request, "guide_stage.html", ctx)


@app.post("/p/{name}/save-stage")
async def save_stage(
    request: Request,
    name: str,
    rel: str = Form(...),
    content: str = Form(""),
) -> JSONResponse:
    """将某个阶段产物（回显编辑后的内容）写回本地。"""
    ps = state.get_project_state(name)
    ok, msg = state.write_project_file(name, rel, content, ps["available_commands"])
    return JSONResponse({"ok": ok, "message": msg})


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


@app.get("/api/stages/{name}")
def api_stages(name: str) -> JSONResponse:
    """全部内容阶段状态（已确认 / 受影响·待复核 / 原因），供前端刷新标签。"""
    return JSONResponse(state.all_stage_status(name))


@app.post("/api/stages/{name}/confirm")
async def api_confirm_stage(name: str, stage: str = Form(...)) -> JSONResponse:
    """确认（或重新确认）某阶段：记录上游基线，消除受影响标记。"""
    ok, msg = state.confirm_stage(name, stage)
    return JSONResponse({"ok": ok, "message": msg})


@app.get("/api/review/{name}")
async def api_review(name: str, stage: str = "") -> JSONResponse:
    """生成复核检查单：上游改动后，LLM 找下游阶段的未覆盖 / 冲突条目。

    同步调用 M19（LLM 分析），结果存入 stages.json 供逐条裁决。
    """
    if stage not in state.STAGE_KEYS:
        return JSONResponse({"ok": False, "message": f"未知阶段：{stage}"})
    changed = state.changed_upstreams(name, stage)
    if not changed:
        return JSONResponse({"ok": False, "message": "该阶段无上游改动，无需复核"})
    try:
        from agent.workflows.m19_review_sync import M19ReviewSyncWorkflow

        wf = M19ReviewSyncWorkflow(project_dir=state.project_path(name))
        # 历史已采纳条目（持久化 adopted_history）+ 当前检查单中已采纳状态，合并去重，
        # 保证重新生成时既有决策不丢失，避免重复提出同一问题。
        prev_map = {h.get("target"): h for h in state.adopted_history(name, stage)}
        for it in state.review_items(name, stage):
            if it.get("status") == "accepted" and it.get("target") not in prev_map:
                prev_map[it.get("target", "")] = {
                    "kind": str(it.get("kind", "conflict")),
                    "target": str(it.get("target", "")),
                    "issue": str(it.get("issue", "")),
                    "suggestion": str(it.get("suggestion", "")),
                }
        prev = list(prev_map.values())
        result = wf.review(
            target_stage=stage, changed_upstreams=changed, previous_adopted=prev
        )
    except Exception as e:  # noqa: BLE001 - LLM 失败降级不阻断
        return JSONResponse({"ok": False, "message": f"复核生成失败：{e}"})
    items = state.save_review_items(
        name, stage, [f.to_dict() for f in result.findings], result.summary
    )
    # 写作边界：上游阶段改动同样可能影响已写章节，列出清单供作者抽查
    chapters = state.get_chapters(name)
    return JSONResponse(
        {
            "ok": True,
            "stage": stage,
            "changed_upstreams": [state.STAGE_LABEL.get(k, k) for k in changed],
            "summary": result.summary,
            "items": items,
            "adopted": state.adopted_history(name, stage),
            "affected_chapters": [{"num": c["num"], "name": c["name"]} for c in chapters],
        }
    )


@app.get("/api/review/{name}/items")
def api_review_items(name: str, stage: str = "") -> JSONResponse:
    """读取已保存的复核检查单条目（不触发 LLM，供面板重开时回显）。"""
    if stage not in state.STAGE_KEYS:
        return JSONResponse({"ok": False, "message": f"未知阶段：{stage}"})
    return JSONResponse(
        {
            "ok": True,
            "stage": stage,
            # 是否仍需复核（自上次复核后上游又有新改动）→ 前端据此决定是否重新生成
            "needs_review": state.stage_status(name, stage)["affected"],
            "summary": state.review_summary(name, stage),
            "items": state.review_items(name, stage),
            "adopted": state.adopted_history(name, stage),
            "affected_chapters": [
                {"num": c["num"], "name": c["name"]} for c in state.get_chapters(name)
            ],
        }
    )


@app.post("/api/review/{name}/decision")
async def api_review_decision(
    name: str,
    stage: str = Form(...),
    item_id: str = Form(...),
    action: str = Form(...),
) -> JSONResponse:
    """逐条裁决复核发现：采纳 / 忽略。"""
    ok, msg = state.review_decision(name, stage, item_id, action)
    return JSONResponse({"ok": ok, "message": msg})


@app.get("/api/qa/{name}")
def api_qa_templates(name: str) -> JSONResponse:
    """返回全部内容阶段的问答模板 + 已保存的问答结果（供问答面板渲染）。"""
    from agent.web.qa_templates import QA_TEMPLATES

    result = {}
    for key, tpl in QA_TEMPLATES.items():
        result[key] = {
            "title": tpl["title"],
            "questions": tpl["questions"],
            "saved": state.load_qa(name, key),
        }
    return JSONResponse(result)


@app.post("/api/qa/{name}")
async def api_qa_save(
    name: str,
    stage: str = Form(...),
    payload: str = Form(...),
) -> JSONResponse:
    """保存某阶段的问答结果（payload 为 JSON：{answers, skipped, supplementary}）。"""
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return JSONResponse({"ok": False, "message": "问答数据解析失败"})
    if stage not in state.STAGE_KEYS:
        return JSONResponse({"ok": False, "message": f"未知阶段：{stage}"})
    ok, msg = state.save_qa(
        name,
        stage,
        data.get("answers"),
        data.get("skipped"),
        data.get("supplementary"),
    )
    return JSONResponse({"ok": ok, "message": msg})


@app.post("/api/projects")
async def create_project(
    name: str = Form(...),
    title: str = Form(...),
    scope: str = Form("long"),
    total_words: str = Form(""),
    chapter_length: str = Form(""),
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
    if total_words.strip():
        argv += ["--total-words", total_words.strip()]
    if chapter_length.strip():
        argv += ["--chapter-length", chapter_length.strip()]
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


@app.get("/api/runs/{run_id}")
def run_status(run_id: str) -> JSONResponse:
    """run 状态查询：供前端在 SSE 失效/丢事件时轮询兜底，弥补「假死」体验。"""
    run = runner.run_manager.runs.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="运行不存在")
    data: dict[str, Any] = {
        "done": run["done"],
        "exit_code": run["exit_code"],
        "project": run["project"],
        "command": run["command"],
        "logs": run.get("logs", []),
    }
    if run["done"]:
        try:
            data["state"] = state.get_project_state(run["project"]).get("state")
        except Exception:  # noqa: BLE001 - 状态读取失败不阻断返回
            data["state"] = None
    return JSONResponse(data)
