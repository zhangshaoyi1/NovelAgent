# 问题 #06：setuptools `package-dir` hack 生成空 wheel / editable 不暴露 `agent`

| 项 | 内容 |
|---|---|
| 分类 | 工程 / 打包 |
| 严重度 | 高（直接决定 `agent` 包能否被安装、导入、分发） |
| 状态 | 已解决（改为 src 布局） |
| 关联文件 | `pyproject.toml`（早期 `package-dir={"agent":"."}` → 现 `where=["src"]`）、`src/agent/`、`README.md` |
| 证据 | 早期 wheel 仅 2383 字节（空）、`pip install -e .` 后仍 `import agent` 失败；改 src 布局后 wheel 约 240KB、`import agent` 正常 |

## 1. 问题描述
最初为了「让仓库根目录直接是 Python 包 `agent`」，在 `pyproject.toml` 里用了：
```toml
[tool.setuptools]
package-dir = {"agent" = "."}
```
期望把根目录当作 `agent` 包。结果构建出的 wheel **只有 2383 字节（空包）**，editable 安装后全局也**无法 `import agent`**——包根本没被打进去。

## 2. 现象 / 证据
- `pip wheel` / `pip install` 产物：`agent-0.1.0-py3-none-any.whl` 仅 2383 字节，解压后无 `agent/` 代码。
- 中性目录（如 `/tmp`）下 `python -c "import agent"` → `ModuleNotFoundError: No module named 'agent'`。
- 改为 src 布局后：`agent/src/agent/` 为包，`[tool.setuptools.packages.find] where=["src"]`，重新构建 wheel ≈ 240KB，`import agent` 正常，`novel-agent --help` 可用。

## 3. 根因
`setuptools` 的 `package-dir = {"agent": "."}` 把「包 `agent` 的源码根」指向仓库根，但仓库根里**没有名为 `agent` 的目录**，只有散落的 `cli/ core/ ...`。setuptools 找不到名为 `agent` 的包，于是构建了一个不含代码的空 wheel；editable 安装同理不注册 `agent`。

## 4. 解决方案（当前）
采用标准 **src 布局**：
```
agent/                 # 仓库根
├── pyproject.toml
└── src/agent/         # Python 包（cli/ core/ workflows/ skills/ templates/ state_schema/ ...）
```
`pyproject.toml`：
```toml
[tool.setuptools.packages.find]
where = ["src"]
include = ["agent*"]
```
并把 `cli/ core/ ...` 用 `git mv` 迁入 `src/agent/`（保留改名历史），`templates/` 补 `__init__.py` 以便 wheel 发布。

## 5. 影响
- 仓库从「根即包」变为「src 布局」，更规范、可干净构建与分发。
- 所有导入路径不变（`import agent.cli` 等），仅物理位置变化；驱动脚本通过 `PYTHONPATH=src` 适配（见 issue-08）。

## 6. 改进建议
- **将此作为项目模板固化**：新仓库一律 src 布局，禁止再用 `package-dir` hack；在 `README.md` 明确说明。
- **增加构建校验**：CI 里加一步「构建 wheel 并检查体积 > 某阈值 / 含 `agent` 模块」，防止回归到空 wheel。

## 7. 复现 / 验证
```bash
cd D:/project/NovelAgent/agent
D:/env/python/python.exe -m pip install -e . --no-deps
D:/env/python/python.exe -c "import agent; print(agent.__file__)"   # 应输出 .../src/agent/__init__.py
D:/env/python/python.exe -m agent.cli --help
```
