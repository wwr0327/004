import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

from code_review_agent.agent import ReviewAgent
from code_review_agent.demo import DemoClient
from code_review_agent.llm import ChatClient, ModelError, load_env_file
from code_review_agent.tools import WorkspaceTools
from code_review_agent.__main__ import main


def call(name="read_file", args='{"path":"sample.py"}'):
    return {"role": "assistant", "content": None, "tool_calls": [
        {"id": "call_1", "type": "function", "function": {"name": name, "arguments": args}}]}


class ScriptedClient:
    def __init__(self, replies):
        self.replies = iter(replies)
        self.requests = []

    def complete(self, messages, tools):
        self.requests.append(list(messages))
        return next(self.replies)


class ToolTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.tools = WorkspaceTools(self.root)
        (self.root / "sample.py").write_text("def f(x=[]):\n    return 1 / 0\n", encoding="utf-8")

    def test_read_and_analysis(self):
        self.assertIn("1: def f", self.tools.read_file("sample.py")["content"])
        findings = self.tools.analyze_python("sample.py")["findings"]
        self.assertEqual({f["rule"] for f in findings}, {"mutable-default", "zero-division"})
        self.assertEqual([f["line"] for f in findings], [1, 2])

    def test_syntax_error(self):
        (self.root / "bad.py").write_text("def f(:", encoding="utf-8")
        self.assertFalse(self.tools.analyze_python("bad.py")["syntax_ok"])

    def test_clean_file(self):
        (self.root / "clean.py").write_text("def f(x=None):\n    return x\n", encoding="utf-8")
        self.assertEqual(self.tools.analyze_python("clean.py")["findings"], [])

    def test_unsafe_and_missing_paths(self):
        (self.root / ".hidden.py").write_text("secret", encoding="utf-8")
        for name in ("../secret.py", str(self.root / "sample.py"), ".hidden.py", "missing.py"):
            with self.subTest(name=name):
                self.assertFalse(self.tools.execute("read_file", json.dumps({"path": name}))["ok"])

    def test_invalid_arguments(self):
        for name, args in [("unknown", "{}"), ("read_file", "not json"),
                           ("read_file", "[]"), ("read_file", '{"path":"sample.py","start_line":true}'),
                           ("read_file", '{"path":"sample.py","end_line":201}'),
                           ("read_file", '{"path":"sample.py","extra":1}')]:
            with self.subTest(args=args):
                self.assertFalse(self.tools.execute(name, args)["ok"])

    def test_file_size_and_encoding(self):
        (self.root / "large.py").write_bytes(b"a" * (128 * 1024 + 1))
        (self.root / "binary.py").write_bytes(b"\xff")
        for path in ("large.py", "binary.py"):
            self.assertFalse(self.tools.execute("read_file", json.dumps({"path": path}))["ok"])

    def test_list_filters(self):
        (self.root / ".env").write_text("KEY=secret")
        (self.root / "node_modules").mkdir()
        (self.root / "node_modules" / "dep.js").write_text("x")
        self.assertEqual(self.tools.list_files()["files"], ["sample.py"])

    def test_symlink_escape_when_supported(self):
        with tempfile.TemporaryDirectory() as outside:
            target = Path(outside) / "outside.py"
            target.write_text("secret")
            try:
                (self.root / "linked.py").symlink_to(target)
            except OSError:
                self.skipTest("系统未授予创建符号链接权限")
            self.assertFalse(self.tools.execute("read_file", '{"path":"linked.py"}')["ok"])

    def test_agent_loop_and_memory(self):
        client = ScriptedClient([call(), {"role": "assistant", "content": "发现问题"},
                                 {"role": "assistant", "content": "修复建议"}])
        agent = ReviewAgent(client, self.tools)
        self.assertEqual(agent.ask("审查"), "发现问题")
        result_message = client.requests[1][-1]
        self.assertEqual(result_message["tool_call_id"], "call_1")
        self.assertTrue(json.loads(result_message["content"])["ok"])
        agent.ask("如何修复")
        self.assertTrue(any(m.get("content") == "发现问题" for m in client.requests[2]))
        agent.reset()
        self.assertEqual(agent.turns, [])

    def test_agent_preserves_deepseek_reasoning_content(self):
        tool_reply = call()
        tool_reply["reasoning_content"] = "工具选择依据"
        client = ScriptedClient([
            tool_reply,
            {"role": "assistant", "content": "发现问题", "reasoning_content": "审查依据"},
            {"role": "assistant", "content": "后续回答", "reasoning_content": "追问依据"},
        ])
        agent = ReviewAgent(client, self.tools)
        agent.ask("审查")
        self.assertTrue(any(m.get("reasoning_content") == "工具选择依据"
                            for m in client.requests[1]))
        agent.ask("继续")
        self.assertTrue(any(m.get("reasoning_content") == "审查依据"
                            for m in client.requests[2]))

    def test_tool_error_is_returned_to_model(self):
        client = ScriptedClient([call(args="invalid"), {"role": "assistant", "content": "调整参数"}])
        self.assertEqual(ReviewAgent(client, self.tools).ask("审查"), "调整参数")
        self.assertFalse(json.loads(client.requests[1][-1]["content"])["ok"])

    def test_step_limit_rolls_back_turn(self):
        agent = ReviewAgent(ScriptedClient([call(), call()]), self.tools, max_steps=2)
        with self.assertRaises(ModelError):
            agent.ask("循环")
        self.assertEqual(agent.turns, [])

    def test_memory_preserves_whole_turns(self):
        client = ScriptedClient([{"role": "assistant", "content": "A"}] * 3)
        agent = ReviewAgent(client, self.tools, memory_turns=1)
        agent.ask("一")
        agent.ask("二")
        agent.ask("三")
        self.assertEqual([m["content"] for m in client.requests[-1][1:]], ["二", "A", "三"])

    def test_bad_call_fails_without_committing(self):
        malformed = call()
        malformed["tool_calls"][0].pop("id")
        agent = ReviewAgent(ScriptedClient([malformed]), self.tools)
        with self.assertRaises(ModelError):
            agent.ask("审查")
        self.assertEqual(agent.turns, [])


class FakeOpener:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.requests = []

    def open(self, request, timeout):
        self.requests.append(request)
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return io.BytesIO(response)


class ClientTests(unittest.TestCase):
    good = json.dumps({"choices": [{"message": {"role": "assistant", "content": "OK"}}]}).encode()

    def test_http_retry_and_payload(self):
        opener = FakeOpener([urllib.error.HTTPError("https://example.com", 429, "rate", {}, None), self.good])
        delays = []
        client = ChatClient("test", "https://example.com/v1", "model", opener=opener, sleep=delays.append)
        self.assertEqual(client.complete([], [])["content"], "OK")
        self.assertEqual(delays, [1])
        self.assertNotIn("tool_choice", json.loads(opener.requests[0].data))

    def test_deepseek_payload_and_reasoning_content(self):
        response = json.dumps({"choices": [{"message": {
            "role": "assistant", "content": None, "reasoning_content": "依据",
            "tool_calls": []}}]}).encode()
        opener = FakeOpener([response])
        client = ChatClient("test", "https://api.deepseek.com", "deepseek-flash",
                            thinking="enabled", opener=opener)
        message = client.complete([], [])
        payload = json.loads(opener.requests[0].data)
        self.assertEqual(payload["thinking"], {"type": "enabled"})
        self.assertEqual(message["reasoning_content"], "依据")

    def test_deepseek_environment_defaults(self):
        with patch.dict("os.environ", {"DEEPSEEK_API_KEY": "test-key"}, clear=True), \
                patch("code_review_agent.llm.urllib.request.build_opener",
                      return_value=FakeOpener([])), \
                patch("code_review_agent.llm.load_env_file"):
            client = ChatClient.from_env()
        self.assertEqual(client.url, "https://api.deepseek.com/chat/completions")
        self.assertEqual(client.model, "deepseek-flash")
        self.assertEqual(client.thinking, "disabled")

    def test_load_env_file_without_overriding_shell(self):
        with tempfile.TemporaryDirectory() as directory:
            env_file = Path(directory) / ".env"
            env_file.write_text(
                "DEEPSEEK_API_KEY=file-key\nLLM_MODEL=deepseek-flash\nIGNORED=value\n",
                encoding="utf-8")
            with patch.dict("os.environ", {"DEEPSEEK_API_KEY": "shell-key"}, clear=True):
                load_env_file(env_file)
                self.assertEqual(__import__("os").environ["DEEPSEEK_API_KEY"], "shell-key")
                self.assertEqual(__import__("os").environ["LLM_MODEL"], "deepseek-flash")
                self.assertNotIn("IGNORED", __import__("os").environ)

    def test_invalid_thinking_mode(self):
        with self.assertRaisesRegex(ValueError, "enabled 或 disabled"):
            ChatClient("test", "https://api.deepseek.com", "deepseek-flash", thinking="auto")

    def test_auth_error_no_retry(self):
        opener = FakeOpener([urllib.error.HTTPError("https://example.com", 401, "auth", {}, None)])
        client = ChatClient("test", "https://example.com/v1", "model", opener=opener)
        with self.assertRaisesRegex(ModelError, "401"):
            client.complete([], [])
        self.assertEqual(len(opener.requests), 1)

    def test_http_error_includes_provider_message(self):
        error = urllib.error.HTTPError(
            "https://api.deepseek.com", 400, "bad request", {},
            io.BytesIO(b'{"error":{"message":"reasoning_content is required"}}'))
        client = ChatClient("test", "https://api.deepseek.com", "deepseek-flash",
                            opener=FakeOpener([error]))
        with self.assertRaisesRegex(ModelError, "reasoning_content is required"):
            client.complete([], [])

    def test_network_retry_exhaustion(self):
        opener = FakeOpener([TimeoutError()] * 3)
        client = ChatClient("test", "https://example.com/v1", "model", opener=opener, sleep=lambda _: None)
        with self.assertRaises(ModelError):
            client.complete([], [])
        self.assertEqual(len(opener.requests), 3)

    def test_malformed_response(self):
        for raw in (b"not-json", b'{"choices":[]}', b'{"choices":[{"message":null}]}'):
            client = ChatClient("test", "https://example.com/v1", "model", opener=FakeOpener([raw]))
            with self.assertRaises(ModelError):
                client.complete([], [])

    def test_remote_http_rejected(self):
        with self.assertRaises(ValueError):
            ChatClient("test", "http://example.com/v1", "model")

    def test_demo_cli(self):
        with patch("sys.stdout", new_callable=io.StringIO) as out:
            self.assertEqual(main(["--demo", "--root", str(Path(__file__).resolve().parents[1])]), 0)
            self.assertIn("离线演示", out.getvalue())
            self.assertIn("共享状态", out.getvalue())


if __name__ == "__main__":
    unittest.main()
