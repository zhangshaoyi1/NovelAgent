# 问题 #13：项目物理位置漂移（`D:/project/agent/projects` → `D:/project/NovelAgent/小说/projects`）

| 项 | 内容 |
|---|---|
| 分类 | 工程 / 路径 |
| 严重度 | 中（间接引发 safe-delete 失败与配置错位，且造成日志/状态路径不一致） |
| 状态 | 已解决（重构迁移后统一到 `D:/project/NovelAgent/小说/projects/...`） |
| 关联文件 | 小说项目 `projects/jipin-yixian/`、驱动 `STATE`/`PROJECT` 常量、`_driver_log_jipin.json` 中的旧路径 |
| 证据 | 运行日志里 safe-delete 目标为 `D:/project/agent/projects/jipin-yixian/.state/draft.wip*`；重构后驱动常量改为 `小说/projects/jipin-yixian` |

## 1. 问题描述
小说项目最初放在 `D:/project/agent/projects/jipin-yixian/`。在整体重构（见 issue-11）把它迁移到 `D:/project/NovelAgent/小说/projects/jipin-yixian/` 后，**部分路径引用没有同步更新**，导致一个过渡期内出现「旧路径残留」与「路径不一致」。

## 2. 现象 / 证据
- `_driver_log_jipin.json` 中 safe-delete 失败记录的目标路径仍是：
  `D:\project\agent\projects\jipin-yixian\.state\draft.wip.bak`
  说明这些清理尝试发生在**迁移前 / 迁移早期**，项目还在旧位置。
- 驱动常量后续统一为：
  `PROJECT = "小说/projects/jipin-yixian"`（相对 `D:/project/NovelAgent`）
  `STATE = "D:/project/NovelAgent/小说/projects/jipin-yixian/state.json"`
- 日志后半段 `clean_draft` 才成功（`{"removed":"draft.wip"}`），与规范化路径时间线吻合。

## 3. 根因
- 重构是「移动目录 + 改写常量」的组合操作，期间有日志 / 状态文件残留旧绝对路径。
- 旧位置 `D:/project/agent/` 同时还是 WorkBuddy 的工作区根，容易与 NovelAgent 项目根混淆（见 issue-09 也部分源于此混淆）。

## 4. 解决方案（当前）
- 驱动常量 `PROJECT` / `STATE` / `LOG` 全部指向 `D:/project/NovelAgent/小说/...` 规范位置。
- 旧的 `D:/project/agent/` 现已清空为仅含 `.workbuddy/`（WorkBuddy 工作区），不再混入 NovelAgent 项目文件。
- 项目外的安全备份（`/tmp/novelagent_backup_20260817/`）已按用户授权删除（见自动化归档记录）。

## 5. 影响
- 路径漂移是 **issue-02（safe-delete 失败）** 的放大器：旧位置的 safe-delete 目标解析更易触发「回收站不可用 / 路径不在白名单」而失败关闭。
- 若常量未同步，会导致 `write` 写到新位置、`clean_draft` 删旧位置，草稿永远清不掉。

## 6. 改进建议
- **路径单一来源**：项目根通过环境变量 / 配置文件注入，不要在脚本里硬编码多处绝对路径；驱动用「相对 NovelAgent 根」的常量即可。
- **迁移脚本化**：目录大搬家应配合一次「全仓路径引用替换」脚本，避免手工漏改。
- **区分工作区与项目根**：明确 `D:/project/agent` 是 WorkBuddy 工作区、`D:/project/NovelAgent` 是 NovelAgent 项目根，文档写明，杜绝混淆。

## 7. 复现 / 验证
```bash
# 确认小说项目当前唯一位置
ls -d "D:/project/NovelAgent/小说/projects/jipin-yixian"
# 确认驱动常量指向规范位置
grep -n "PROJECT\|STATE\|LOG" "D:/project/NovelAgent/agent/drivers/_write_driver_jipin.py"
```
