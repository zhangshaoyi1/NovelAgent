"""写单章 Task：唯一的业务 Task（M0 演示用）

参考旧代码 WriterAgent 的写章逻辑，但重新实现为 Task 形式。
"""

from __future__ import annotations

from typing import Any

from llmagent.gateway.chat import Gateway
from llmagent.gateway.models import ChatRequest, ChatResponse, TaskHint
from llmagent.kernel.artifact import ArtifactStore
from llmagent.kernel.task import Executor, TaskKind, TaskRun, TaskSpec

# 写单章 TaskSpec 定义
WRITE_CHAPTER_SPEC = TaskSpec(
    name="write_chapter",
    kind=TaskKind.LLM,
    description="生成小说章节内容",
    input_schema={
        "type": "object",
        "required": ["chapter_title", "outline", "previous_chapter"],
        "properties": {
            "chapter_title": {"type": "string"},
            "outline": {"type": "string"},
            "previous_chapter": {"type": "string"},
            "word_target": {"type": "integer", "default": 3000},
        },
    },
    output_schema={
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "content": {"type": "string"},
            "word_count": {"type": "integer"},
        },
    },
    timeout_s=600.0,
)


class WriteChapterExecutor(Executor):
    """写单章执行器"""

    kind = TaskKind.LLM

    def __init__(self, gateway: Gateway, artifact_store: ArtifactStore) -> None:
        self._gateway = gateway
        self._artifact_store = artifact_store

    async def execute(self, run: TaskRun) -> TaskRun:
        input_data = run.output  # 假设输入已在 run.output 中
        chapter_title = input_data.get("chapter_title", "未命名章节")
        outline = input_data.get("outline", "")
        previous = input_data.get("previous_chapter", "")
        word_target = input_data.get("word_target", 3000)

        # 构建 Prompt
        system_prompt = (
            "你是一位专业的小说作家。请根据以下信息生成章节内容。\n\n"
            f"## 章节标题\n{chapter_title}\n\n"
            f"## 章节大纲\n{outline}\n\n"
            f"## 上一章内容（用于上下文衔接）\n{previous[:2000]}\n\n"
            "## 要求\n"
            f"- 字数：约 {word_target} 字\n"
            "- 保持与前文一致的风格和视角\n"
            "- 段落之间用空行分隔\n"
            "- 输出格式：先写章节标题，然后写正文"
        )

        # 经 Gateway 调用
        req = ChatRequest(
            messages=[
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": (
                        f"请写第 {chapter_title} 章，约 {word_target} 字。"
                        f"确保内容充实、有情节推进、人物刻画到位。"
                    ),
                },
            ],
            hint=TaskHint(complexity="complex", quality_critical=True, max_tokens=4096),
            run_id=run.run_id,
            budget_ref=run.budget_ref,
        )

        try:
            resp: ChatResponse = self._gateway.chat(req)
        except Exception as e:
            run.status = TaskStatus.FAILED  # type: ignore[name-defined]
            run.error = str(e)
            return run

        # 结果入 ArtifactStore
        self._artifact_store.put(resp.text, content_type="text/plain")

        # 解析输出
        lines = resp.text.strip().split("\n", 1)
        title = lines[0].strip() if len(lines) > 0 else chapter_title
        content = lines[1].strip() if len(lines) > 1 else resp.text.strip()
        word_count = len(content)

        run.output = {
            "title": title,
            "content": content,
            "word_count": word_count,
        }
        run.status = TaskStatus.SUCCEEDED  # type: ignore[name-defined]
        return run


from llmagent.kernel.task import TaskStatus  # noqa: E402, F811