import subprocess, json, time, os

PY = r"D:/env/python/python.exe"
CWD = r"D:/project/NovelAgent"
PROJ = r"小说/projects/deep-well"
TARGET = 50000
MAX_CHAPTERS = 25
ADJUST_EVERY = 3
LOG = r"D:/project/NovelAgent/小说/_driver_log.json"
START = time.time()


def run_cli(*args):
    cmd = [PY, "-m", "agent.cli", *args]
    p = subprocess.run(cmd, cwd=CWD, capture_output=True, text=True, encoding="utf-8")
    out = (p.stdout or "").strip()
    err = (p.stderr or "").strip()
    result = None
    for line in out.splitlines():
        s = line.strip()
        if s.startswith("{") and s.endswith("}"):
            try:
                j = json.loads(s)
                if "success" in j:
                    result = j
                    break
            except Exception:
                pass
    if result is None:
        result = {"success": False, "raw_stdout": out[:1500],
                  "stderr": err[-1500:], "exit_code": p.returncode}
    return result, out, err


def save(total, chapter, records):
    with open(LOG, "w", encoding="utf-8") as f:
        json.dump({"total_chars": total, "chapters": chapter, "records": records},
                  f, ensure_ascii=False, indent=2)


records = []
total_chars = 0
chapter = 0

while chapter < MAX_CHAPTERS:
    res, out, err = run_cli("write", "-d", PROJ, "--json")
    rec = {"step": "write", "chapter": res.get("chapter"), "success": res.get("success"),
           "title": res.get("title"), "word_count": res.get("word_count"),
           "quality_passed": res.get("quality_passed"), "subline": res.get("subline"),
           "route_node": res.get("route_node"), "exit_code": res.get("exit_code"),
           "ts": round(time.time() - START, 1)}
    records.append(rec)
    save(total_chars, chapter, records)

    if not res.get("success"):
        # retry up to 2 times
        ok = False
        for attempt in range(2):
            print(f"[WRITE RETRY {attempt+1}] chapter={res.get('chapter')}")
            res, out, err = run_cli("write", "-d", PROJ, "--json")
            rec2 = {"step": "write_retry", "attempt": attempt + 1, "chapter": res.get("chapter"),
                    "success": res.get("success"), "word_count": res.get("word_count"),
                    "ts": round(time.time() - START, 1)}
            records.append(rec2)
            save(total_chars, chapter, records)
            if res.get("success"):
                ok = True
                break
        if not ok:
            print(f"[WRITE FAILED] last err={res.get('stderr') or res.get('raw_stdout')}")
            break

    wc = int(res.get("word_count") or 0)
    total_chars += wc
    chapter = int(res.get("chapter") or (chapter + 1))
    print(f"[WRITE] ch{chapter} title={res.get('title')} wc={wc} total={total_chars} subline={res.get('subline')}")

    if chapter % ADJUST_EVERY == 0:
        lo = chapter - ADJUST_EVERY + 1
        intent_rel = (f"根据第{lo}-{chapter}章的实际剧情，演化角色关系网："
                      f"标记已死亡、黑化、结盟或身份反转的角色，新增羁绊，"
                      f"并标注这些质变与既有伏笔的关联。")
        rj, _, _ = run_cli("adjust-relation", "-d", PROJ, "-i", intent_rel, "--json")
        records.append({"step": "adjust-relation", "after_chapter": chapter,
                        "success": rj.get("success"), "nodes_count": rj.get("nodes_count"),
                        "new_edges_count": rj.get("new_edges_count"),
                        "archived_edges_count": rj.get("archived_edges_count"),
                        "conflicts": rj.get("conflicts"), "ts": round(time.time() - START, 1)})
        print(f"[ADJUST-REL] ch{chapter} success={rj.get('success')} nodes={rj.get('nodes_count')} conflicts={rj.get('conflicts')}")
        intent_route = (f"根据第{lo}-{chapter}章的剧情推进，演化主角林默的成长路线："
                        f"保留已写节点，更新当前节点状态，必要时为后续节点补充新分支"
                        f"（旧分支归档为 archived_alt）。")
        rr, _, _ = run_cli("adjust-route", "-d", PROJ, "-i", intent_route, "--json")
        records.append({"step": "adjust-route", "after_chapter": chapter,
                        "success": rr.get("success"), "current_node_id": rr.get("current_node_id"),
                        "new_nodes_count": rr.get("new_nodes_count"),
                        "conflicts": rr.get("conflicts"), "ts": round(time.time() - START, 1)})
        print(f"[ADJUST-ROUTE] ch{chapter} success={rr.get('success')} node={rr.get('current_node_id')}")

    save(total_chars, chapter, records)
    if total_chars >= TARGET:
        print(f"[TARGET REACHED] total={total_chars} chapters={chapter}")
        break

print(f"DONE total_chars={total_chars} chapters={chapter} elapsed={round(time.time()-START,1)}s")
save(total_chars, chapter, records)
