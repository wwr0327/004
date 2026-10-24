import json
from code_review_agent.llm import ModelError
from .base_agent import BaseAgent
from .prompts import CODE_PROMPT
from .parsing import unfence

class CodingAgent(BaseAgent):
    prompt = CODE_PROMPT

    def __init__(self, client):
        self.client = client

    def run(self, state):
        state.code = self._generate({"requirement": state.requirement})
        return state

    def _generate(self, context):
        message = self.client.complete([
            {"role": "system", "content": self.prompt},
            {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
        ], [])
        if not isinstance(message, dict):
            raise ModelError("代码 Agent 返回格式无效")
        content = message.get("content")
        if message.get("tool_calls") or not isinstance(content, str):
            raise ModelError("代码 Agent 必须返回完整源码")
        code = unfence(content)
        if not code or len(code) > 32000:
            raise ModelError("代码为空或超过 32000 字符")
        # Syntax defects are deliberately left for the review/fix stages.
        return code + "\n"

