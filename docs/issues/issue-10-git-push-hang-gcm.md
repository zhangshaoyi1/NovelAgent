# 问题 #10：`git push` 在沙箱挂死（Git Credential Manager 交互式授权）

| 项 | 内容 |
|---|---|
| 分类 | 工程 / 工具链 |
| 严重度 | 高（表现为「命令卡死无输出」，极易误判为网络故障） |
| 状态 | 已解决（`GIT_TERMINAL_PROMPT=0` 改用缓存凭据） |
| 关联文件 | 远程配置（`origin` = `https://gitcode.com/shaoyizhang/NovelAgentNew.git`）、本机 git 凭据管理器 |
| 证据 | 默认 `git push -u origin master` 静默 6+ 分钟无输出；加 `GIT_TERMINAL_PROMPT=0` 后秒过 `[new branch] master -> master` |

## 1. 问题描述
把 `agent` 仓库推送到 gitcode 新远程时，直接 `git push` **长时间无输出、无报错、不返回**——看起来像网络不通或卡死。

## 2. 现象 / 证据
- 默认推送：进程静默挂起 6+ 分钟，最终只能手动终止。
- 诊断：`curl` 访问 `https://gitcode.com/...` 仅 0.56s 返回 HTTP 302 → **网络是通的**。
- 网络通畅却挂死，根因是 **Git Credential Manager（GCM）** 尝试弹**交互式授权对话框**，而沙箱无桌面环境，对话框无法显示也无法输入，于是无限等待。
- 加 `GIT_TERMINAL_PROMPT=0` 禁用交互提示后，GCM 改用**主机级缓存凭据**（gitcode 凭据按 host 作用域，旧 `NovelAgent.git` 的缓存对 `NovelAgentNew.git` 同样有效），`git push` 立即成功。

## 3. 根因
- 沙箱无 GUI，GCM 的弹窗授权无法完成，且没有非交互凭据兜底时就会挂起。
- 默认 `git` 会尊重 `GIT_TERMINAL_PROMPT`；不设 0 时它会尝试交互。

## 4. 解决方案（当前）
推送前禁用交互提示，让 GCM 走缓存凭据：
```bash
GIT_TERMINAL_PROMPT=0 git -C "D:/project/NovelAgent/agent" push -u origin master
```
- 已验证：远程 `master` 成功创建，本地 `92c072b` 与 `origin/master` 对齐。
- 远程 URL 已改为 `https://gitcode.com/shaoyizhang/NovelAgentNew.git`。

## 5. 影响
- 若不处理，推送永远卡死，无法把代码上云；且症状「静默无输出」极具迷惑性。
- 注意：缓存凭据**有有效期**，过期后可能再次需要凭据；届时需在能交互的环境刷新，或改用 **deploy token / SSH key**。

## 6. 改进建议
- **优先用 SSH 或 deploy token**：在自动化 / 沙箱场景，给远程配 SSH（`git@gitcode.com:...`）或 HTTPS deploy token，彻底绕开 GCM 交互。
- **固化命令**：把 `GIT_TERMINAL_PROMPT=0` 写入项目推送文档；或在本机 `git config` 设 `credential.*` 使用 cache / store 模式。
- 推送前先用 `GIT_TERMINAL_PROMPT=0 git ls-remote <url>` 做**有界探测**（设 timeout），避免再次被挂死误导。

## 7. 复现 / 验证
```bash
# 有界探测（约 30s 超时），确认可达 + 凭据可用
GIT_TERMINAL_PROMPT=0 timeout 30 git -C "D:/project/NovelAgent/agent" ls-remote https://gitcode.com/shaoyizhang/NovelAgentNew.git
# 推送
GIT_TERMINAL_PROMPT=0 git -C "D:/project/NovelAgent/agent" push -u origin master
```
