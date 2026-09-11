# Agent Note: 拆解 cli↔web 循环依赖，R4 由「整层跳过」改为方向矩阵

Status: implemented

## Problem

`cli/commands/web.py` 为拿到 FastAPI 应用对象而 `from agent.web.app import app as fastapi_app`，
于是产生一条 **cli → web** 的源码级依赖；而 `web/` 侧本身要读 CLI 命令元数据（用于「高级命令」区
等入口），即 **web → cli**。两条边合起来就是 **`cli ↔ web` 循环依赖**——架构文档 §3.7 明写
「`cli/` 与 `core/` 不得 import `web/`」，但这条不变式当时**只写在文档里**。

更值得记录的是护栏侧的失效方式：`tests/architecture/test_redlines_agent.py` 的 R4 用例（业务层不得
import `agent.cli` / `agent.web`）在实现上直接 **跳过整层**：

```python
if rel.startswith(("cli/", "web/")) or rel in allowed:
    continue
```

也就是说 `cli/` 与 `web/` 之间**任何方向都不检查**。这不是判据写错，而是**这段范围根本没有被检查**——
一条 README 级别的不变式，在一年内都不会被任何测试打断，随时可能被某个"顺手 import"固化下来。

## Decision

**① 用「运行时按字符串解析」拆环，而不是加豁免。**
`cli/commands/web.py` 不再 import `agent.web.app`，改为把 import string 交给 uvicorn 在运行时解析：

```python
# 架构不变性 §3.7 / R4：cli/ 不得 import web/（依赖只能向下）。
# 因此这里不 import agent.web.app，改由 uvicorn 在运行时按 import string 解析，
# 反向依赖（web → cli 读命令元数据）保持不变，环被拆成单向。
uvicorn.run("agent.web.app:app", host=host, port=port)
```

`web → cli` 是**设计内单向**（Web 是 CLI 的上层封装，经子进程调 CLI、读命令元数据），因此只需禁掉
`cli → web` 一个方向，环即消失；功能一点没少。

**② R4 从「整层跳过」改为接入层方向矩阵。** `test_redlines_agent.py` 新增模块级常量：

```python
R4_CLI_FORBIDDEN = ("agent.web",)
```

- `cli/` 下的文件：只禁 `R4_CLI_FORBIDDEN`（即 `agent.web`）；
- `web/` 下的文件：跳过（`web → cli` 为设计内单向，不构成违规）；
- 业务层（`workflows/` `core/` `agents/` 等）：仍然禁 `agent.cli` 与 `agent.web` 双向。

**③ 探针验证**：在 `cli/` 下临时新增 `import agent.web`，R4 立即 FAIL；撤回后通过——确认这条红线**会红**，
而不是又一个永远绿灯的护栏。

**④ 上升为架构不变性**：《项目文档/架构文档.md》§3.4 新增第 8 条「接入层单向原则」，并在 §3.4 表后
补一张「接入层内部方向矩阵」表（`cli/` 禁止 `agent.web`；`web/` 允许 `agent.cli`）；§11.1 评审结论同步。

## Alternatives considered

**### Why not 把 cli/ 加进 R4 的豁免白名单？**
最省事的写法，但等于**把循环依赖登记为"已知可接受"**，环本身还在，只是没人再报警；且附录 B 迁移约束
明令「不保留新旧并存过渡态」，新增豁免与迁移方向相反——否决。

**### Why not 把 FastAPI app 抽到第三个模块，让双方都 import 它？**
若新模块仍放在 `web/` 下，`cli → web` 的边只是换了个文件名，红线判据（包前缀）照样命中；若放在
`cli/` 或顶层，则 `web → 新模块` 成立而 `新模块 → web.app` 又成了新边，等于把环挪了个位置。真正的
解法是**让编译期依赖消失**，而不是给它找中间人——否决。

**### Why not 函数体内延迟 import？**
```python
def web(...):
    from agent.web.app import app as fastapi_app
```
源码里仍存在 `agent.web` 这条 import 边，静态扫描会命中，必须为它开豁免——跟方案一同一个坑；
而且"延迟 import"只把错误推迟到运行时，与 import string 的代价相同，却保不住单向矩阵的干净——否决。

**### Why not 反向：让 web/ 不再依赖 cli，从而彻底解耦？**
`web` 读命令元数据是为了「高级命令」区与门禁展示，改造成本远大于拆一条边；且这是**设计内**的上层依赖
（上层依赖下层天经地义），没必要为了对称而砍掉——否决。

## Consequences

- `cli ↔ web` 环拆成单向，方向矩阵由测试增量守护；此前"整层跳过"造成的检查盲区被填上。
- **代价（需知道）**：`uvicorn.run(import_string)` 把「模块不存在/导入报错」的发现时机从**编译期**推迟到
  **运行期**（启动 Web 时才炸）。收益是源码级依赖边消失、环被真正拆掉；若日后要找回早期报错，可在
  `web` 命令入口先用 `importlib.util.find_spec("agent.web.app")` 做一次轻量探测，**不必**恢复 import。
- 同类场景的可复用判据：**凡是"框架/容器需要反向加载入口"的情况，优先用 import string 而不是 import 语句**
  （已在架构文档 §3.4 第 8 条沉淀）。
- 该批次只动 `cli/commands/web.py` 与 R4 用例两个文件（`src/agent/cli/commands/web.py`、
  `tests/architecture/test_redlines_agent.py`），不影响运行行为；全量测试 1990 passed / 9 skipped / 0 failed。
