"""评审 Task：对章节内容进行质量评审"""

from __future__ import annotations

from typing import Any

from llmagent.gateway.chat import Gateway
from llmagent.gateway.models import ChatRequest, ChatResponse, TaskHint
from llmagent.kernel.artifact import ArtifactStore
from llmagent.kernel.task import Executor, TaskKind, TaskRun, TaskSpec, TaskStatus

# 评审 TaskSpec
REVIEW_SPEC = TaskSpec(
    name="review_chapter",
    kind=TaskKind.LLM,
    description="评审小说章节质量",
    input_schema={
        "type": "object",
        "required": ["chapter_title", "chapter_content"],
        "properties": {
            "chapter_title": {"type": "string"},
            "chapter_content": {"type": "string"},
            "criteria": {"type": "string", "default": "情节连贯性、人物刻画、文字质量"},
        },
    },
    output_schema={
        "type": "object",
        "properties": {
            "score": {"type": "integer"},
            "issues": {"type": "array"},
            "suggestions": {"type": "string"},
        },
    },
    timeout_s=300.0,
)


class ReviewExecutor(Executor):
    """评审执行器"""

    kind = TaskKind.LLM

    def __init__(self, gateway: Gateway, artifact_store: ArtifactStore) -> None:
        self._gateway = gateway
        self._artifact_store = artifact_store

    async def execute(self, run: TaskRun) -> TaskRun:
        input_data = run.output
        chapter_title = input_data.get("chapter_title", "")
        chapter_content = input_data.get("chapter_content", "")
        criteria = input_data.get("criteria", "情节连贯性、人物刻画、文字质量")

        system_prompt = (
            "你是一位专业的小说评审编辑。请对以下章节进行评审。\n\n"
            f"## 评审标准\n{criteria}\n\n"
            "## 输出要求\n"
            "- 给出 1-10 的总体评分\n"
            "- 列出主要问题（如有）\n"
            "- 给出改进建议"
        )

        req = ChatRequest(
            messages=[
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": f"请评审章节《{chapter_title}》：\n\n{chapter_content[:4000]}",
                },
            ],
            hint=TaskHint(complexity="complex", quality_critical=True, max_tokens=2048),
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

        # 简化解析
        import json
        import re

        score = 7
        issues: list[str] = []
        suggestions = ""

        json_match = re.search(r"\{.*\}", resp.text, re.DOTALL)
        if json_match:
            try:
                data = json.loads(json_match.group(0))
                score = data.get("score", score)
                issues = data.get("issues", issues)
                suggestions = data.get("suggestions", suggestions)
            except (json.JSONDecodeError, TypeError):
                pass

        run.output = {
            "score": score,
            "issues": issues,
            "suggestions": suggestions,
        }
        run.status = TaskStatus.SUCCEEDED
        return run