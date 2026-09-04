"""大纲 Task：生成小说章节大纲"""

from __future__ import annotations

from typing import Any

from llmagent.gateway.chat import Gateway
from llmagent.gateway.models import ChatRequest, ChatResponse, TaskHint
from llmagent.kernel.artifact import ArtifactStore
from llmagent.kernel.task import Executor, TaskKind, TaskRun, TaskSpec, TaskStatus

# 大纲 TaskSpec
OUTLINE_SPEC = TaskSpec(
    name="generate_outline",
    kind=TaskKind.LLM,
    description="生成小说章节大纲",
    input_schema={
        "type": "object",
        "required": ["story_summary", "chapter_count"],
        "properties": {
            "story_summary": {"type": "string"},
            "chapter_count": {"type": "integer"},
            "style": {"type": "string", "default": "章回体"},
        },
    },
    output_schema={
        "type": "object",
        "properties": {
            "chapters": {"type": "array"},
            "summary": {"type": "string"},
        },
    },
    timeout_s=600.0,
)


class OutlineExecutor(Executor):
    """大纲执行器"""

    kind = TaskKind.LLM

    def __init__(self, gateway: Gateway, artifact_store: ArtifactStore) -> None:
        self._gateway = gateway
        self._artifact_store = artifact_store

    async def execute(self, run: TaskRun) -> TaskRun:
        input_data = run.output
        story_summary = input_data.get("story_summary", "")
        chapter_count = input_data.get("chapter_count", 10)
        style = input_data.get("style", "章回体")

        system_prompt = (
            "你是一位专业的小说大纲设计师。请根据以下信息生成章节大纲。\n\n"
            f"## 故事简介\n{story_summary}\n\n"
            f"## 风格\n{style}\n\n"
            f"## 要求\n"
            f"- 生成 {chapter_count} 章的大纲\n"
            "- 每章包含标题和内容概要\n"
            "- 确保情节有起承转合"
        )

        req = ChatRequest(
            messages=[
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": f"请为这个故事生成 {chapter_count} 章的大纲（{style}风格）。",
                },
            ],
            hint=TaskHint(complexity="complex", quality_critical=True, max_tokens=4096),
            run_id=run.run_id,
            budget_ref=run.budget_ref,
            extra={"format": "json"},
        )

        try:
            resp: ChatResponse = self._gateway.chat(req)
        except Exception as e:
            run.status = TaskStatus.FAILED
            run.error = str(e)
            return run

        self._artifact_store.put(resp.text, content_type="text/plain")

        run.output = {
            "chapters": [],
            "summary": resp.text[:500],
        }
        run.status = TaskStatus.SUCCEEDED
        return run