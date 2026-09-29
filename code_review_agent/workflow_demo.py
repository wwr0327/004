"""Fixed fixtures for the Calculator workflow, not a general-purpose model."""
import json

DRAFT = '''class Calculator:
    def add(self, a, b):
        return a + b

    def subtract(self, a, b):
        return a - b

    def multiply(self, a, b):
        return a * b

    def divide(self, a, b):
        return a / b
'''
FIXED = DRAFT.replace("        return a / b", '        if b == 0:\n            raise ValueError("除数不能为 0")\n        return a / b')


class WorkflowDemoClient:
    def complete(self, messages, tools):
        prompt = messages[0]["content"]
        if "你是 CodingAgent" in prompt:
            return {"role": "assistant", "content": DRAFT}
        if "你是 FixAgent" in prompt:
            return {"role": "assistant", "content": FIXED}
        results = [json.loads(m["content"]) for m in messages if m["role"] == "tool"]
        if not results:
            context = json.loads(messages[-1]["content"].split("\n", 1)[1])
            return {"role": "assistant", "content": None, "tool_calls": [
                {"id": f"demo_{name}", "type": "function", "function": {
                    "name": name, "arguments": json.dumps({"path": context["path"]})}}
                for name in ("read_file", "analyze_python")]}
        if any(not r["ok"] for r in results):
            raise ValueError("离线演示读取工具失败")
        source = results[0]["result"]["content"]
        fixed = 'raise ValueError("除数不能为 0")' in source
        review = {"approved": fixed,
                  "summary": "固定演示：静态审查通过，未运行代码。" if fixed else "固定演示：除零异常不符合需求。",
                  "issues": [] if fixed else [{"severity": "high", "line": 12,
                      "message": "b=0 时抛出 ZeroDivisionError，需求要求 ValueError",
                      "suggestion": "除法前判断 b == 0 并抛出 ValueError"}]}
        return {"role": "assistant", "content": json.dumps(review, ensure_ascii=False)}
