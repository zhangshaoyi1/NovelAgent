"""分析 Task：对小说内容进行分析"""

from __future__ import annotations

from typing import Any

from llmagent.gateway.chat import Gateway
from llmagent.gateway.models import ChatRequest, ChatResponse, TaskHint
from llmagent.kernel.artifact import ArtifactStore
from llmagent.kernel.task import Executor, TaskKind, TaskRun, TaskSpec, TaskStatus

# 分析 TaskSpec
ANALYZE_SPEC = TaskSpec(
    name="analyze_content",
    kind=TaskKind.LLM,
    description="分析小说内容（角色/情节/主题）",
    input_schema={
        "type": "object",
        "required": ["content", "analysis_type"],
        "properties": {
            "content": {"type": "string"},
            "analysis_type": {"type": "string", "enum": ["character", "plot", "theme", "style"]},
            "context": {"type": "string", "default": ""},
        },
    },
    output_schema={
        "type": "object",
        "properties": {
            "analysis": {"type": "string"},
            "key_points": {"type": "array"},
        },
    },
    timeout_s=300.0,
)


class AnalyzeExecutor(Executor):
    """分析执行器"""

    kind = TaskKind.LLM

    def __init__(self, gateway: Gateway, artifact_store: ArtifactStore) -> None:
        self._gateway = gateway
        self._artifact_store = artifact_store

    async def execute(self, run: TaskRun) -> TaskRun:
        input_data = run.output
        content = input_data.get("content", "")
        analysis_type = input_data.get("analysis_type", "plot")
        context = input_data.get("context", "")

        analysis_prompts = {
            "character": "分析以下内容中的人物塑造、性格特点和角色发展",
            "plot": "分析以下内容的情节结构、冲突设置和悬念安排",
            "theme": "分析以下内容的主题思想、价值观和隐喻",
            "style": "分析以下内容的写作风格、语言特点和叙事技巧",
        }

        prompt = analysis_prompts.get(analysis_type, analysis_prompts["plot"])

        system_prompt = (
            "你是一位专业的小说分析专家。\n\n"
            f"## 分析任务\n{prompt}\n\n"
            "## 输出要求\n"
            "- 给出详细的分析报告\n"
            "- 列出关键发现\n"
            "- 给出改进建议"
        )

        user_content = f"请分析以下内容：\n\n{content[:4000]}"
        if context:
            user_content = f"背景信息：{context}\n\n{user_content}"

        req = ChatRequest(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
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

        run.output = {
            "analysis": resp.text[:2000],
            "key_points": [],
        }
        run.status = TaskStatus.SUCCEEDED
        return run