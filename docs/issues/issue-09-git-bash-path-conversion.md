# 问题 #09：Git Bash 路径转换 `/d/...` → `C:\d\...` 导致 git / 原生命令失败

| 项 | 内容 |
|---|---|
| 分类 | 工程 / 工具链 |
| 严重度 | 中（反复咬人，尤其在重构与推送时） |
| 状态 | 已规避（统一用 Windows 风格 `D:/...` 绝对路径） |
| 关联文件 | 重构 / 推送操作涉及的任意 `git` 命令；本仓库 `README.md`、各 issue 中的命令示例 |
| 证据 | `git -C /d/project/NovelAgent/agent ...` → `fatal: cannot change to '/d/project/NovelAgent/agent'`（被转为 `C:\d\...`） |

## 1. 问题描述
在 WorkBuddy 的 Git Bash 里，`/d/project/...` 这类 Unix 风格路径会被 MSYS **自动转换**为 `C:\d\project\...`（因为 Windows 没有 `C:\d\` 这种盘符前缀），导致 `git`、`python` 等**原生 Windows 程序**找不到目录。而 `ls`、`cat` 等 MSYS 内置命令能正常识别 `/d/...`，造成「`ls` 能列、`git` 报错」的困惑。

## 2. 现象 / 证据
- `ls -la /d/project/NovelAgent/agent/` ✅ 正常列出。
- `git -C /d/project/NovelAgent/agent status` ❌ `fatal: cannot change to '/d/project/NovelAgent/agent'`。
- 早期 `python -m agent.cli` 用 `/d/...` 当 cwd 也报过 `can't open file`。

## 3. 根因
Git Bash（MSYS2）对**原生 Windows 可执行文件**的参数做 POSIX→Windows 路径转换。`/d/...` 被转成 `C:\d\...`，而系统盘是 `D:`，`C:\d\...` 不存在 → 失败。MSYS 内置命令不做此转换（或转换正确），所以表现不一致。

## 4. 解决方案（当前）
对所有 **git / python / 原生程序** 的参数与 cwd，**一律使用 Windows 风格绝对路径** `D:/project/NovelAgent/agent`（正斜杠、带盘符）。示例：
```bash
git -C "D:/project/NovelAgent/agent" status
D:/env/python/python.exe -m agent.cli --help
```
仅对纯 MSYS 命令（如 `ls`、`mkdir`）可用 `/d/...`，但为统一也建议写 `D:/...`。

## 5. 影响
- 不影响最终产物，但**严重拖慢排错**（每次都要先意识到是路径转换问题）。
- 重构（大量 `git mv`）、推送（`git push`）、运行（`python -m`）都曾被它绊住。

## 6. 改进建议
- **项目文档固化**：在 `README.md` / 贡献指南写明「本仓库在 Windows + Git Bash 下操作 git/python 一律用 `D:/...` 绝对路径」。
- 可提供一个薄封装脚本（如 `nag.sh`）：内部把 `/d/...` 归一化为 `D:/...` 再转发给 git，减少人为失误。
- 也可改用 PowerShell / Cmd 跑 git，避免 MSYS 转换（但会失去 Bash 生态）。

## 7. 复现 / 验证
```bash
git -C "/d/project/NovelAgent/agent" status        # 预期失败（被转 C:\d\...）
git -C "D:/project/NovelAgent/agent" status        # 预期成功
```
