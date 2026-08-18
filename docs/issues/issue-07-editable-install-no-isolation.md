# 问题 #07：editable 安装 `--no-build-isolation` 因缺 `wheel` 失败

| 项 | 内容 |
|---|---|
| 分类 | 工程 / 打包 |
| 严重度 | 低（有简单绕过） |
| 状态 | 已解决 |
| 关联文件 | `pyproject.toml`（`build-system.requires` 含 `wheel`）、`README.md` |
| 证据 | `pip install -e . --no-build-isolation` → `error: invalid command 'bdist_wheel'` |

## 1. 问题描述
为了复用已装依赖、加快构建，尝试：
```bash
pip install -e . --no-build-isolation
```
报错：`error: invalid command 'bdist_wheel'`。

## 2. 现象 / 证据
- 报错发生在构建后端尝试生成 wheel 时，当前 Python 环境缺少 `wheel` 包，且 `--no-build-isolation` 关闭了构建隔离（不会自动装构建依赖），于是 `bdist_wheel` 命令不可用。

## 3. 根因
基础 Python 环境没有 `wheel`；关闭构建隔离后，setuptools 无法调用 `bdist_wheel`。

## 4. 解决方案（当前）
去掉 `--no-build-isolation`，让 pip 在隔离环境中自动安装 `build-system.requires` 里的 `setuptools` / `wheel`：
```bash
pip install -e . --no-deps
```
（`--no-deps` 跳过运行依赖安装，因为运行环境已具备；如需完整可去掉 `--no-deps`。）

## 5. 影响
- 极小。改用隔离构建后 editable 安装成功，`import agent` 正常。

## 6. 改进建议
- **文档写明推荐命令**：在 `README.md` 固化 `pip install -e . --no-deps`（开发）与 `pip install ./agent`（发布），避免他人再踩 `--no-build-isolation` 坑。
- 构建依赖已在 `pyproject.toml` 的 `build-system.requires` 声明，保持即可。

## 7. 复现 / 验证
```bash
cd D:/project/NovelAgent/agent
D:/env/python/python.exe -m pip install -e . --no-deps
D:/env/python/python.exe -m agent.cli --help
```
