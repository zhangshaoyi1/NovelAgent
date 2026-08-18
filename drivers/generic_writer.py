"""NovelAgent 通用写作驱动（由《极品医仙》正式驱动重构而来，配置驱动、可换书复用）。

本脚本把"某一本小说"相关的全部硬编码常量抽进 TOML 配置文件，
只需复制配置文件、改几个值，即可驱动下一本小说，无需改代码。

用法
----
    # 默认读取同目录 driver_config.toml（里面已填好《极品医仙》的值）
    python drivers/generic_writer.py

    # 换书：复制配置、改名，指定 --config 即可
    python drivers/generic_writer.py --config driver_config.<书名>.toml

行为
----
- 循环调用真实 `write --json` 产出章节；满足"完结条件"即正式完结：
    * 支线模式（配置了 [arcs].ids）：写完最后一条支线即完结；
    * 简单模式（未配置支线、设置了 run.max_chapters）：写到 max_chapters 即完结。
- 分批续写：每批攒满 run_target_chars 即正常退出并保存日志；
  重新运行本脚本会从日志断点续写（含 arc_index / total_chars），无需从头。
- 每 adjust.every 章执行 adjust-relation / adjust-route，让关系网/路线随真实剧情演化；
  进入收官弧（arcs 模式下 cur_arc >= adjust.tail_from_arc_index）后，
  adjust 意图自动切换为"收束伏笔、向结局演化"语气。
- 每个命令均真实 LLM 调用；限流/超时/草稿阻塞按指数退避重试（最多 9 次）；
  仅结构性硬错误（配置/校验/异常）跳过重试。
- 完结后按 novel_complete 开关自动 export TXT + 生成 dashboard（失败不阻断）。
- 全程 JSON 日志（paths.log），供使用记录与断点续写。

依赖：Python 3.11+（内置 tomllib 解析 TOML）。
"""
from __future__ import annotations

import json
import subprocess
import os
import sys
import time
import argparse

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    tomllib = None


# ---------------------------------------------------------------------------
# 模块级全局（在 main() 解析配置后填充）
# ---------------------------------------------------------------------------
CFG: dict = {}   # 解析后的配置（绝对路径 + 参数）
LOG: dict = {}   # 运行日志（断点续写状态）


# ---------------------------------------------------------------------------
# 配置加载与解析
# ---------------------------------------------------------------------------
def load_config(path: str) -> dict:
    """读取并解析 TOML 配置。"""
    if tomllib is None:
        sys.exit("解析 TOML 需要 Python 3.11+（内置 tomllib）。请升级解释器。")
    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except FileNotFoundError:
        sys.exit(f"配置文件不存在：{path}")
    except Exception as e:  # TOMLDecodeError 等
        sys.exit(f"解析配置失败 {path}：{e}")


def _norm(p: str) -> str:
    """统一为当前系统的路径分隔符。"""
    return p.replace("/", os.sep)


def resolve(raw: dict) -> dict:
    """把 TOML 原始配置解析为驱动内部使用的绝对路径与参数。"""
    proj = raw.get("project", {})
    paths = raw.get("paths", {})
    run = raw.get("run", {})
    arcs = raw.get("arcs", {})
    adj = raw.get("adjust", {})
    nc = raw.get("novel_complete", {})

    base_dir = _norm(proj.get("base_dir", r"D:/project/NovelAgent"))
    projects_rel = _norm(proj.get("projects_rel", "小说/projects"))
    name = proj.get("name", "")
    if not name:
        sys.exit("配置 [project].name 不能为空（即小说 projects 目录名）。")

    project_dir = os.path.join(base_dir, projects_rel, name)
    state = os.path.join(project_dir, "state.json")

    log = paths.get("log", "")
    if not log:
        # 默认日志：工作区根 / 小说 / _driver_log_<书名>.json
        log = os.path.join(base_dir, _norm("小说"), f"_driver_log_{name}.json")
    elif not os.path.isabs(log):
        log = os.path.join(base_dir, _norm(log))

    python = paths.get("python", sys.executable)
    # src 布局：Python 包位于本脚本上级目录的 src/（脚本在 drivers/，上级即 agent/）
    agent_src = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"
    )

    arc_ids = list(arcs.get("ids", []))
    per_arc = list(arcs.get("per_arc_chapters", []))
    if arc_ids and len(arc_ids) != len(per_arc):
        sys.exit(
            f"[arcs].ids 与 [arcs].per_arc_chapters 长度不一致"
            f"（{len(arc_ids)} vs {len(per_arc)}）。"
        )

    return {
        "name": name,
        "base_dir": base_dir,
        "project_dir": project_dir,
        "state": state,
        "log": log,
        "python": python,
        "agent_src": agent_src,
        "cwd": base_dir,
        # run
        "run_target_chars": int(run.get("run_target_chars", 50_000)),
        "hard_cap_chars": int(run.get("hard_cap_chars", 450_000)),
        "cooldown_sec": int(run.get("cooldown_sec", 45)),
        "max_chapters": int(run.get("max_chapters", 0)),
        # arcs
        "arc_ids": arc_ids,
        "per_arc_chapters": per_arc,
        # adjust
        "adjust_every": int(adj.get("every", 5)),
        "tail_from_arc_index": int(adj.get("tail_from_arc_index", 999)),
        # novel_complete
        "export_txt": bool(nc.get("export_txt", True)),
        "dashboard": bool(nc.get("dashboard", True)),
    }


# ---------------------------------------------------------------------------
# 日志（断点续写）
# ---------------------------------------------------------------------------
def load_log() -> dict:
    try:
        d = json.load(open(CFG["log"], encoding="utf-8"))
        d.setdefault("total_chars", 0)
        d.setdefault("chapters", 0)
        d.setdefault("arc_index", 0)
        d.setdefault("records", [])
        return d
    except Exception:
        return {"total_chars": 0, "chapters": 0, "arc_index": 0, "records": []}


def save_log() -> None:
    json.dump(LOG, open(CFG["log"], "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# CLI 调用（注入 PYTHONPATH=src）
# ---------------------------------------------------------------------------
def run_cli(args, timeout=200):
    cmd = [CFG["python"], "-m", "agent.cli"] + args
    try:
        env = dict(os.environ)
        env["PYTHONPATH"] = CFG["agent_src"] + os.pathsep + env.get("PYTHONPATH", "")
        r = subprocess.run(cmd, cwd=CFG["cwd"], env=env, capture_output=True,
                           text=True, timeout=timeout, encoding="utf-8")
        out = r.stdout.strip()
        try:
            return json.loads(out), r.returncode
        except Exception:
            return {"_raw": out[:500], "_stderr": r.stderr.strip()[:300]}, r.returncode
    except subprocess.TimeoutExpired:
        return {"success": False, "error": {"code": "timeout"}}, 124
    except Exception as e:
        return {"success": False, "error": {"code": "exception", "message": str(e)}}, 1


# ---------------------------------------------------------------------------
# 状态机 / 草稿清理辅助
# ---------------------------------------------------------------------------
def set_subline(sid: str) -> bool:
    """把当前支线对齐写入 state.json（断点续写时不硬重置回第一条）。"""
    try:
        d = json.load(open(CFG["state"], encoding="utf-8"))
        d.setdefault("progress", {})["current_subline"] = sid
        json.dump(d, open(CFG["state"], "w", encoding="utf-8"),
                  ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        LOG["records"].append({"step": "set_subline", "error": str(e)})
        return False


def clean_stale_draft() -> None:
    """写前清理 NovelAgent 残留草稿(.state/draft.wip*)。

    驱动是单线程同步调用 write：每次 write 子进程结束(成功或失败)后才进入
    下一轮，因此"准备发起新 write 时存在的 draft.wip"必然是上一轮失败留下的
    死草稿。NovelAgent 的 F18.4 恢复机制会拒绝在残留草稿上覆盖写入，必须先在
    驱动侧强制删除(Windows 沙箱 safe_remove 走回收站会失败,故用 os.remove 直删)。
    仅删 .wip 系列,不动已 accepted 的 chapters/*.md。
    """
    state_dir = os.path.join(CFG["project_dir"], ".state")
    for name in ("draft.wip", "draft.wip.bak"):
        p = os.path.join(state_dir, name)
        try:
            if os.path.exists(p):
                os.remove(p)
                LOG["records"].append({"step": "clean_draft", "removed": name})
        except Exception as e:
            LOG["records"].append({"step": "clean_draft", "error": str(e), "name": name})


# ---------------------------------------------------------------------------
# 错误分类
# ---------------------------------------------------------------------------
def _is_rate_limited(res) -> bool:
    """判断是否为速率限制(429)/超时/草稿阻塞类错误，便于退避重试。

    注意：write 子命令在限流或超时时，stdout 往往不是结构化的
    {"error":{"message":...}}，而是 rich UI 文本或空串，错误落在 _raw/_stderr。
    因此必须扫描原始输出，否则会误判为"非限流"而直接放弃重试。
    """
    if not isinstance(res, dict):
        return False
    err = res.get("error")
    msg = str(err.get("message", "")) if isinstance(err, dict) else str(err)
    raw = str(res.get("_raw", "")) + " " + str(res.get("_stderr", ""))
    blob = (msg + " " + raw).lower()
    return (
        ("429" in msg)
        or ("速率限制" in msg)
        or ("rate limit" in msg.lower())
        or ("429" in raw)
        or ("速率限制" in raw)
        or ("rate limit" in raw.lower())
        or ("request timed out" in blob)
        or ("timeout" in blob)
        or ("draft" in blob)  # 残留草稿阻塞，清理后可重试
    )


def _is_hard_error(res) -> bool:
    """判断是否为结构性硬错误(不应重试)：缺文件/配置/导入/校验拦截/异常栈。"""
    if not isinstance(res, dict):
        return False
    blob = (
        str(res.get("error", ""))
        + " "
        + str(res.get("_raw", ""))
        + " "
        + str(res.get("_stderr", ""))
    ).lower()
    return any(
        k in blob
        for k in (
            "pre_validation",
            "traceback",
            "no such file",
            "filenotfound",
            "module not found",
            "keyerror",
            "attributeerror",
            "does not exist",
            "typeerror",
            "valueerror",
        )
    )


# ---------------------------------------------------------------------------
# 写一个章节（含重试）
# ---------------------------------------------------------------------------
def write_chapter():
    """真实调用 write；遇限流/超时/草稿阻塞/瞬断均按指数退避重试(最多 9 次)。

    每次尝试前先清理残留草稿(避免 F18.4 恢复机制拒绝覆盖写入)；
    仅对结构性硬错误(配置/校验/异常)跳过重试，其余一律长退避重试。
    """
    backoff = [10, 30, 60, 120, 180, 300, 600, 900]
    for i in range(len(backoff) + 1):
        clean_stale_draft()
        res, rc = run_cli(["write", "-d", CFG["project_dir"], "--json"], timeout=400)
        if isinstance(res, dict) and res.get("success") and res.get("chapter"):
            return res
        if _is_hard_error(res):
            LOG["records"].append(
                {"step": "write", "error": "hard_error_skip", "detail": str(res)[:200]}
            )
            return None
        time.sleep(backoff[min(i, len(backoff) - 1)])
    return None


def adjust(kind: str, intent: str, chapter: int) -> bool:
    backoff = [10, 30, 60, 120]
    res, rc = None, 1
    for i in range(len(backoff) + 1):
        res, rc = run_cli([f"adjust-{kind}", "-d", CFG["project_dir"], "-i", intent],
                          timeout=220)
        if isinstance(res, dict) and res.get("success"):
            break
        if _is_rate_limited(res) and i < len(backoff):
            time.sleep(backoff[i])
            continue
        break
    ok = bool(isinstance(res, dict) and res.get("success"))
    LOG["records"].append({
        "step": f"adjust-{kind}", "after_chapter": chapter, "success": ok,
        "returncode": rc,
        "conflicts": res.get("conflicts") if isinstance(res, dict) else None,
    })
    return ok


# ---------------------------------------------------------------------------
# 主循环
# ---------------------------------------------------------------------------
def _count_writes_in_subline(subline: str) -> int:
    return sum(
        1 for r in LOG["records"]
        if r.get("step") == "write" and r.get("subline") == subline
    )


def main() -> None:
    global CFG, LOG
    ap = argparse.ArgumentParser(description="NovelAgent 通用写作驱动")
    ap.add_argument(
        "--config", "-c",
        default=os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "driver_config.toml"),
        help="TOML 配置文件路径（默认同目录 driver_config.toml）",
    )
    args = ap.parse_args()

    CFG = resolve(load_config(args.config))
    LOG = load_log()

    # 断点恢复：从日志恢复 arc_index，并把 state 对齐到当前弧（不硬重置回第一条）
    has_arcs = bool(CFG["arc_ids"])
    cur_arc = LOG["arc_index"] if has_arcs else 0
    if has_arcs:
        set_subline(CFG["arc_ids"][cur_arc])
    save_log()

    batch_start = LOG["total_chars"]  # 本批起点，用于单批增量上限

    while True:
        # 单批增量达到上限 -> 正常退出，等待下一批续写（分批续写，抗沙箱超时）
        if LOG["chapters"] > 0 and (LOG["total_chars"] - batch_start) >= CFG["run_target_chars"]:
            LOG["records"].append({
                "step": "batch_done", "at_chars": LOG["total_chars"],
                "at_chapter": LOG["chapters"],
                "arc": CFG["arc_ids"][cur_arc] if has_arcs else None,
            })
            break

        # 完结判定
        completed = False
        if has_arcs:
            if cur_arc == len(CFG["arc_ids"]) - 1:
                last_done = _count_writes_in_subline(CFG["arc_ids"][-1])
                if last_done >= CFG["per_arc_chapters"][-1]:
                    completed = True
        elif CFG["max_chapters"] > 0:
            if LOG["chapters"] >= CFG["max_chapters"]:
                completed = True
        if completed:
            LOG["records"].append({
                "step": "novel_complete", "at_chars": LOG["total_chars"],
                "at_chapter": LOG["chapters"],
            })
            break

        # 安全硬上限（极端兜底）
        if LOG["total_chars"] >= CFG["hard_cap_chars"]:
            LOG["records"].append({"step": "hard_cap_reached",
                                   "at_chars": LOG["total_chars"]})
            break

        # 章节间冷却：规避 LLM 服务商 RPM 限流（写前 sleep）
        time.sleep(CFG["cooldown_sec"])
        res = write_chapter()
        if res is None:
            LOG["records"].append({"step": "write",
                                   "error": "max_retries_exhausted, 中止本批"})
            save_log()
            break
        wc = int(res.get("word_count", 0))
        ch = int(res.get("chapter", LOG["chapters"] + 1))
        LOG["total_chars"] += wc
        LOG["chapters"] = ch
        LOG["records"].append({
            "step": "write", "chapter": ch, "word_count": wc,
            "subline": res.get("subline"), "route_node": res.get("route_node"),
            "title": res.get("title", ""),
            "quality_passed": res.get("quality_passed"),
            "total_chars": LOG["total_chars"],
        })

        # 支线推进：当前弧写满且非最后一条 -> 推进（仅支线模式）
        if has_arcs:
            arc_name = CFG["arc_ids"][cur_arc]
            chapters_in_arc = _count_writes_in_subline(arc_name)
            if chapters_in_arc >= CFG["per_arc_chapters"][cur_arc] and cur_arc < len(CFG["arc_ids"]) - 1:
                cur_arc += 1
                set_subline(CFG["arc_ids"][cur_arc])
                LOG["arc_index"] = cur_arc
                LOG["records"].append({"step": "advance_arc",
                                       "to": CFG["arc_ids"][cur_arc], "at_chapter": ch})

        # 每 N 章一致性演化（降频省配额，adjust 429 本就非阻塞不影响成书）
        if CFG["adjust_every"] > 0 and ch % CFG["adjust_every"] == 0:
            arc_name = CFG["arc_ids"][cur_arc] if has_arcs else ""
            use_tail = has_arcs and cur_arc >= CFG["tail_from_arc_index"]
            if use_tail:
                tail = "（收官阶段）" if cur_arc == CFG["tail_from_arc_index"] else "（终章）"
                adjust("relation", f"第{ch-2}-{ch}章处于支线「{arc_name}」{tail}，"
                       f"请据已写章节收束关系网与关键质变，为结局铺路。", ch)
                adjust("route", f"支线「{arc_name}」已推进至第{ch}章{tail}，"
                       f"须在剩余章节内逐步收束所有伏笔与支线，推动剧情向最终决战与"
                       f"结局演化，不得再开新支线。", ch)
            else:
                adjust("relation", f"第{ch-2}-{ch}章处于支线「{arc_name}」，"
                       f"请据已写章节更新关系网与关键质变。", ch)
                adjust("route", f"支线「{arc_name}」已推进至第{ch}章，"
                       f"请据剧情演化主角成长路线。", ch)

        save_log()
        arc_disp = CFG["arc_ids"][cur_arc] if has_arcs else f"第{ch}章"
        sys.stdout.write(f"\r章节 {ch} | 累计 {LOG['total_chars']} 字 | 当前支线 {arc_disp}")
        sys.stdout.flush()

    save_log()

    # 小说完结：自动导出 TXT + 生成 dashboard（非 LLM，低风险；失败不阻断）
    if any(r.get("step") == "novel_complete" for r in LOG["records"]):
        print(f"\n小说已完结：共 {LOG['chapters']} 章，累计 {LOG['total_chars']} 字。"
              f"开始导出 TXT 与生成 dashboard...")
        if CFG["export_txt"]:
            try:
                ex, _ = run_cli(["export", "-d", CFG["project_dir"], "-f", "txt", "--json"],
                                timeout=200)
                LOG["records"].append({"step": "export", "result": str(ex)[:300]})
            except Exception as e:
                LOG["records"].append({"step": "export", "error": str(e)})
        if CFG["dashboard"]:
            try:
                db, _ = run_cli(["dashboard", "-d", CFG["project_dir"], "--json"], timeout=200)
                LOG["records"].append({"step": "dashboard", "result": str(db)[:300]})
            except Exception as e:
                LOG["records"].append({"step": "dashboard", "error": str(e)})
        save_log()

    print(f"\n本批结束：共 {LOG['chapters']} 章，累计 {LOG['total_chars']} 字，"
          f"当前支线 {CFG['arc_ids'][cur_arc] if has_arcs else '（简单模式）'}。")


if __name__ == "__main__":
    main()
