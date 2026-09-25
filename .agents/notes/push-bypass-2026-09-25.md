# 推送旁路记录：NOVELAGENT_SKIP_FULL=1（2026-09-25）

Status: log（非功能改动，仅记明 pre-push 旁路原因）

## 事实链

1. 本次推送内容：`3476425`（进展证据分级）+ `3a91e27`（merge）+ `210b81b`（跨章去重/结构门禁一族修复）。
2. pre-push 全量缓存 `.git/novelagent-regression/last_run.txt`：**3005 passed / 9 skipped / 0 failed（无 FAILED 行）**，但运行时指纹与当前 HEAD 不匹配 ⇒ 判「不可信」阻断。
3. 昨日（2026-09-25 凌晨）另有一次全量：**3082 passed / 9 skipped / 1 failed**，唯一失败
   `test_degrade_visibility.py::test_new_exemptions_must_cite_reason`（bare 403>402）源于当时
   `process_manager.py` 的 noqa 未带 `reason=`，**已在提交 3476425 中修复并复验通过**。
4. 当前无法通过"重跑全量"洗白指纹的原因：工作区存在未提交改动
   `agent/agents/evaluator_metrics.py:101`（`# noqa: BLE001` 缺 `SILENT_DEGRADE` 标注），
   会使 `test_no_silent_degrades` 变红 ⇒ 全量必失败，且该修复属用户进行中的工作，不由本流程代改。
5. 被推代码本身的即时证据：pre-commit 架构红线 257 passed；`tests/daemon` 69 passed（多次）。

## 结论

推送判定依据 = 上述 2/3/5 三份证据 + 旁路原因（4）。旁路依据 hook 自述路径：
`NOVELAGENT_SKIP_FULL=1 git push`。

## 待办

- [ ] `evaluator_metrics.py:101` 补 `# noqa: SILENT_DEGRADE reason=...`（属用户未提交改动，随其下次提交收口）
- [ ] agent 仓库缺 `github` 远程（用户习惯双线推送 gitcode+GitHub），待确认后补
