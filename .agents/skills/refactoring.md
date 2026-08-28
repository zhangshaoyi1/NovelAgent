# Refactoring Skill - 重构检查清单

> 目标：确保重构安全、可逆、可验证。

---

## 重构流程

### Step 1: 理解当前代码

- 阅读完整文件，不要只读修改部分
- 理解当前模块的**依赖关系**（谁依赖它、它依赖谁）
- 确认当前模块的**测试覆盖**情况

### Step 2: 规划迁移路径

- 确定目标架构（新文件 / 新包结构）
- 设计向后兼容层（薄包装 + 废弃警告）
- 不要一次性迁移过大，分批进行

### Step 3: 创建兼容层

- 旧文件保留为薄包装层，导入新位置并抛出废弃警告
- 示例：
  ```python
  import warnings
  from agent.client import LLMClient as _NewLLMClient

  class LLMClient(_NewLLMClient):
      def __init__(self, *args, **kwargs):
          warnings.warn(
              "agent.core.llm_client 已废弃，请改用 agent.client",
              DeprecationWarning, stacklevel=2,
          )
          super().__init__(*args, **kwargs)
  ```

### Step 4: 重写导入

- 更新所有引用旧路径的代码
- 使用 `Grep` 搜索所有旧导入模式
- 逐个文件更新，不要批量替换（容易遗漏）

### Step 5: 验证功能

- 运行相关测试：`pytest -k <module> -v`
- 运行全量测试：`pytest`
- 检查是否有 DeprecationWarning 被触发

### Step 6: 清理旧文件

- 确认所有旧导入路径都已更新
- 删除旧文件（确认无任何引用）
- 运行全量测试再次确认

---

## 安全检查清单

- [ ] 是否创建了向后兼容包装层？
- [ ] 所有旧导入路径都已更新？
- [ ] 旧文件删除前确认无引用？
- [ ] 全量测试通过？
- [ ] 架构不变性没有被破坏？
- [ ] 记录了决策（Agent Note）？