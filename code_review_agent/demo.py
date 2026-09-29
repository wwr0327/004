"""Deterministic offline fixture, explicitly not a language model."""
import json


class DemoClient:
    def complete(self, messages, tools):
        last_user = max(i for i, m in enumerate(messages) if m["role"] == "user")
        results = [json.loads(m["content"]) for m in messages[last_user + 1:] if m["role"] == "tool"]
        if not results:
            return {"role": "assistant", "content": None, "tool_calls": [
                {"id": "demo_read", "type": "function", "function": {
                    "name": "read_file", "arguments": '{"path":"examples/buggy.py"}'}},
                {"id": "demo_ast", "type": "function", "function": {
                    "name": "analyze_python", "arguments": '{"path":"examples/buggy.py"}'}}]}
        lines = ["# 离线演示审查结果", "", "固定演示：只分析 examples/buggy.py，不调用 LLM，也不理解任意问题。", ""]
        for result in results:
            if not result["ok"]:
                lines.append("工具错误：" + result["error"])
            else:
                for finding in result["result"].get("findings", []):
                    lines.append(f"- [{finding['severity']}] examples/buggy.py:{finding['line']} "
                                 f"{finding['message']}。建议：{finding['suggestion']}。")
        lines.extend(["", "分析方式：读取源码和 AST 静态检查，未运行被审查代码。真实语义审查请配置模型后运行。"])
        return {"role": "assistant", "content": "\n".join(lines)}
