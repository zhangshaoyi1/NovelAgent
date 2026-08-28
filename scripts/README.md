# scripts 目录说明

本目录默认留空，存放临时维护脚本。

## 关于已移走的脚本

所有 7 个原脚本已移到 **`D:\project\NovelAgent\back\scripts_deprecated\`**。

### 为什么移出？

所有这些脚本的功能都已经被**项目现有的 guardrails 系统和 CLI 命令覆盖**：

| 原脚本 | 已由什么功能覆盖 |
|--------|-----------------|
| `compose.py` | CLI `compose` 命令 (`agent/src/agent/cli/commands/compose.py`) + `agent.core.compose_runner` |
| `scan_english.py` + `fix_english.py` | G14 成书质量护栏（`agent.core.guardrails`），写时门禁会自动检测英文残留 |
| `scan_dup.py` + `fix_dup.py` + `fix_dup_highsim.py` | G14 成书质量护栏，写时门禁会自动检测跨章重复段落 |
| `fix_titles.py` | G14 成书质量护栏，写时门禁会自动检测占位标题 |

原有脚本是**早期开发阶段的一次性工具**，硬编码具体小说路径，不适合整合到主流程中。保留在项目中会误导用户以为需要单独运行。

### 什么时候会用到备份的脚本

仅在以下情况下，才需要从 `back/scripts_deprecated` 取出：
1. 批量修复历史已写大量章节
2. 需要比 G14 门禁更激进的离线修复
3. 你要写一个新的批量修复工具，可以参考旧脚本的结构

### 新增维护脚本的说明

如果你需要写新的一次性维护脚本，请放在这里（`scripts/`），并在本 README 中添加说明。任务完成后，如果功能无需保留在源码中，建议移出到 `back/` 备份目录。
