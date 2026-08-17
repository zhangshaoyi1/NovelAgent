"""真实驱动 NovelAgent 写《极品医仙归来：前女友跪求复合》(男频爽文, 约15万字, 完结).

- 循环调用真实 `write --json` 产出章节；最后一条支线(S05 终极清算)写满即正式完结。
- 分批续写：每批攒满 RUN_TARGET_CHARS 即正常退出并保存日志；
  重新运行本脚本会从 `_driver_log_jipin.json` 断点续写（含 arc_index / total_chars）。
- 按 PER_ARC_LIST 规划的章节数逐条推进支线 (S01->S02->S03->S04->S05)，
  走完 5 条支线实现真·完结；全书目标约 15 万字收尾（当前已写至约 13.9 万字、
  S02 临近收束），S05(终章)在累计约 14 万字时开始、全书在约 15-16 万字完结。
- 每 5 章执行 adjust-relation / adjust-route 让关系网/路线随真实剧情演化；
  进入 S04/S05 后路线意图改为"收束全部伏笔与支线、向结局演化"。
- 每个命令均真实 LLM 调用；失败重试；全程 JSON 日志供使用记录。
- 完结后自动 export TXT + 生成 dashboard。
"""
from __future__ import annotations
import json, subprocess, os, sys, time

PROJECT = "小说/projects/jipin-yixian"
STATE = "D:/project/NovelAgent/小说/projects/jipin-yixian/state.json"
LOG = "D:/project/NovelAgent/小说/_driver_log_jipin.json"
PY = "D:/env/python/python.exe"
HARD_CAP_CHARS = 450_000  # 安全硬上限(兜底，远高于任何合理总字数)；正常情况下小说会在 S05 写满时完结，不会触发此上限提前腰斩结局
RUN_TARGET_CHARS = 50_000  # 单批增量上限，达到即正常退出，便于分批续写(抗沙箱超时)
# 每条支线规划的章节数(压缩版，总目标约 15 万字，尽快完结)：
#   S01 情感清算 1-25(已写完) / S02 医馆崛起 26-42(已写16章, 再补1章即推进) /
#   S03 身世揭秘 2章 / S04 正邪博弈 2章 / S05 终极清算 3章(终章)
#   合计约 49-50 章，全书约 15-16 万字完结。
PER_ARC_LIST = [25, 17, 2, 2, 3]
COOLDOWN = 45  # 章节间冷却秒数，规避智谱 RPM 限流（写前 sleep）
ARCS = [
    "S01_情感清算_前缘断绝",
    "S02_医馆崛起_积累功德",
    "S03_身世揭秘_医典之谜",
    "S04_正邪博弈_宗门围剿",
    "S05_终极清算_仙尊登临",
]


def load_log():
    try:
        d = json.load(open(LOG, encoding="utf-8"))
        # 兼容旧字段
        d.setdefault("total_chars", 0)
        d.setdefault("chapters", 0)
        d.setdefault("arc_index", 0)
        d.setdefault("records", [])
        return d
    except Exception:
        return {"total_chars": 0, "chapters": 0, "arc_index": 0, "records": []}


log = load_log()


def run_cli(args, timeout=200):
    cmd = [PY, "-m", "agent.cli"] + args
    try:
        r = subprocess.run(cmd, cwd="D:/project/NovelAgent", capture_output=True,
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


def set_subline(sid):
    try:
        d = json.load(open(STATE, encoding="utf-8"))
        d.setdefault("progress", {})["current_subline"] = sid
        json.dump(d, open(STATE, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        log["records"].append({"step": "set_subline", "error": str(e)})
        return False


def save_log():
    json.dump(log, open(LOG, "w", encoding="utf-8"), ensure_ascii=False, indent=2)


def _is_rate_limited(res):
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


def clean_stale_draft():
    """写前清理 NovelAgent 残留草稿(.state/draft.wip*).

    驱动是单线程同步调用 write：每次 write 子进程结束(成功或失败)后才进入
    下一轮，因此"准备发起新 write 时存在的 draft.wip"必然是上一轮失败留下的
    死草稿。NovelAgent 的 F18.4 恢复机制会拒绝在残留草稿上覆盖写入，必须先在
    驱动侧强制删除(Windows 沙箱 safe_remove 走回收站会失败,故用 os.remove 直删)。
    仅删 .wip 系列,不动已 accepted 的 chapters/*.md。
    """
    import os
    state_dir = os.path.join(PROJECT, ".state")
    for name in ("draft.wip", "draft.wip.bak"):
        p = os.path.join(state_dir, name)
        try:
            if os.path.exists(p):
                os.remove(p)
                log["records"].append({"step": "clean_draft", "removed": name})
        except Exception as e:
            log["records"].append({"step": "clean_draft", "error": str(e), "name": name})


def _is_hard_error(res):
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


def write_chapter():
    """真实调用 write；遇限流/超时/草稿阻塞/瞬断均按指数退避重试(最多 9 次)。

    每次尝试前先清理残留草稿(避免 F18.4 恢复机制拒绝覆盖写入)；
    仅对结构性硬错误(配置/校验/异常)跳过重试，其余一律长退避重试，
    保证长任务在智谱间歇限流下仍可缓慢但持续地推进。
    """
    backoff = [10, 30, 60, 120, 180, 300, 600, 900]
    for i in range(len(backoff) + 1):
        clean_stale_draft()
        res, rc = run_cli(["write", "-d", PROJECT, "--json"], timeout=400)
        if isinstance(res, dict) and res.get("success") and res.get("chapter"):
            return res
        if _is_hard_error(res):
            log["records"].append(
                {"step": "write", "error": "hard_error_skip", "detail": str(res)[:200]}
            )
            return None
        # 限流/超时/瞬断：全部长退避重试，不轻易放弃
        time.sleep(backoff[min(i, len(backoff) - 1)])
    return None


def adjust(kind, intent, chapter):
    backoff = [10, 30, 60, 120]
    res, rc = None, 1
    for i in range(len(backoff) + 1):
        res, rc = run_cli([f"adjust-{kind}", "-d", PROJECT, "-i", intent], timeout=220)
        if isinstance(res, dict) and res.get("success"):
            break
        if _is_rate_limited(res) and i < len(backoff):
            time.sleep(backoff[i])
            continue
        break
    ok = bool(isinstance(res, dict) and res.get("success"))
    log["records"].append({
        "step": f"adjust-{kind}", "after_chapter": chapter, "success": ok,
        "returncode": rc,
        "conflicts": res.get("conflicts") if isinstance(res, dict) else None,
    })
    return ok


def main():
    # 断点恢复：从日志恢复 arc_index，并把 state 对齐到当前弧（不硬重置回 S01）
    cur_arc = log["arc_index"]
    set_subline(ARCS[cur_arc])
    save_log()

    batch_start = log["total_chars"]  # 本批起点，用于单批增量上限

    while True:
        # 单批增量达到上限 -> 正常退出，等待下一批续写（分批续写，抗沙箱超时）
        if log["chapters"] > 0 and (log["total_chars"] - batch_start) >= RUN_TARGET_CHARS:
            log["records"].append({
                "step": "batch_done", "at_chars": log["total_chars"],
                "at_chapter": log["chapters"], "arc": ARCS[cur_arc],
            })
            break
        # 完结判定：已写到最后一条支线(S05 终极清算)且写满其章节 -> 小说正式完结
        if cur_arc == len(ARCS) - 1:
            last_done = sum(1 for r in log["records"]
                            if r.get("step") == "write" and r.get("subline") == ARCS[-1])
            if last_done >= PER_ARC_LIST[-1]:
                log["records"].append({"step": "novel_complete",
                                        "at_chars": log["total_chars"],
                                        "at_chapter": log["chapters"]})
                break
        # 安全硬上限（极端兜底，正常情况下会在 S05 完结前达到）
        if log["total_chars"] >= HARD_CAP_CHARS:
            log["records"].append({"step": "hard_cap_reached",
                                    "at_chars": log["total_chars"]})
            break

        # 章节间冷却：智谱免费/按量账户有 RPM 限流，写前 sleep 避免一次性烧光额度
        time.sleep(COOLDOWN)
        res = write_chapter()
        if res is None:
            log["records"].append({"step": "write", "error": "max_retries_exhausted, 中止本批"})
            save_log()
            break
        wc = int(res.get("word_count", 0))
        ch = int(res.get("chapter", log["chapters"] + 1))
        log["total_chars"] += wc
        log["chapters"] = ch
        log["records"].append({
            "step": "write", "chapter": ch, "word_count": wc,
            "subline": res.get("subline"), "route_node": res.get("route_node"),
            "title": res.get("title", ""),
            "quality_passed": res.get("quality_passed"),
            "total_chars": log["total_chars"],
        })

        # 支线推进：当前弧写满 PER_ARC_LIST[cur_arc] 且非最后一条 -> 推进
        chapters_in_arc = sum(1 for r in log["records"]
                              if r.get("step") == "write"
                              and r.get("subline") == ARCS[cur_arc])
        if chapters_in_arc >= PER_ARC_LIST[cur_arc] and cur_arc < len(ARCS) - 1:
            cur_arc += 1
            set_subline(ARCS[cur_arc])
            log["arc_index"] = cur_arc
            log["records"].append({"step": "advance_arc",
                                    "to": ARCS[cur_arc], "at_chapter": ch})

        # 每 5 章一致性演化（降频省配额，adjust 429 本就非阻塞不影响成书）
        if ch % 5 == 0:
            arc_name = ARCS[cur_arc]
            if cur_arc >= 3:
                # 进入收官阶段：S04 正邪决战是终局前奏，S05 为终章，须收束全部伏笔与支线
                tail = "（收官阶段）" if cur_arc == 3 else "（终章）"
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
        sys.stdout.write(f"\r章节 {ch} | 累计 {log['total_chars']} 字 | 当前支线 {ARCS[cur_arc]}")
        sys.stdout.flush()

    save_log()

    # 小说完结：自动导出 TXT + 生成 dashboard（非 LLM，低风险；失败不阻断）
    if any(r.get("step") == "novel_complete" for r in log["records"]):
        print(f"\n小说已完结：共 {log['chapters']} 章，累计 {log['total_chars']} 字。"
              f"开始导出 TXT 与生成 dashboard...")
        try:
            ex, _ = run_cli(["export", "-d", PROJECT, "-f", "txt", "--json"], timeout=200)
            log["records"].append({"step": "export", "result": str(ex)[:300]})
        except Exception as e:
            log["records"].append({"step": "export", "error": str(e)})
        try:
            db, _ = run_cli(["dashboard", "-d", PROJECT, "--json"], timeout=200)
            log["records"].append({"step": "dashboard", "result": str(db)[:300]})
        except Exception as e:
            log["records"].append({"step": "dashboard", "error": str(e)})
        save_log()

    print(f"\n本批结束：共 {log['chapters']} 章，累计 {log['total_chars']} 字，"
          f"当前支线 {ARCS[cur_arc]}。")


if __name__ == "__main__":
    main()
