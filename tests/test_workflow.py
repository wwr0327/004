import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from code_review_agent.llm import ChatClient, ModelError
from code_review_agent.workflow import (DEMO_REQUIREMENT, CodingAgent, Workflow,
                                        WorkflowState, main, parse_review)
from code_review_agent.workflow_demo import WorkflowDemoClient, FIXED
from test_agent import FakeOpener, ScriptedClient


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_demo_end_to_end_and_saved_state(self):
        state = Workflow(WorkflowDemoClient(), self.root).run(DEMO_REQUIREMENT)
        self.assertEqual(state.status, "completed")
        self.assertEqual([e["stage"] for e in state.events], ["coding", "review", "fix", "review", "done"])
        self.assertEqual([r["approved"] for r in state.reviews], [False, True])
        folder = Path(state.run_dir)
        self.assertEqual((folder / "final.py").read_text(encoding="utf-8"), FIXED)
        self.assertTrue((folder / "revision_0.py").exists())
        saved = json.loads((folder / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["status"], "completed")
        self.assertEqual(len(saved["reviews"]), 2)
        self.assertIn("未执行", (folder / "report.md").read_text(encoding="utf-8"))

    def test_no_fixes_returns_needs_attention(self):
        state = Workflow(WorkflowDemoClient(), self.root, max_fixes=0).run(DEMO_REQUIREMENT)
        self.assertEqual(state.status, "needs_attention")
        self.assertEqual(state.revision, 0)

    def test_unresolved_fixes_stop_at_limit(self):
        from code_review_agent.workflow_demo import DRAFT

        class IneffectiveFixClient(WorkflowDemoClient):
            def complete(self, messages, tools):
                if "你是 FixAgent" in messages[0]["content"]:
                    return {"content": DRAFT}
                return super().complete(messages, tools)

        state = Workflow(IneffectiveFixClient(), self.root, max_fixes=2).run(DEMO_REQUIREMENT)
        self.assertEqual(state.status, "needs_attention")
        self.assertEqual(state.revision, 2)
        self.assertEqual(len(state.reviews), 3)
        self.assertFalse(any(r["approved"] for r in state.reviews))

    def test_first_review_pass_skips_fixer(self):
        class CleanClient(WorkflowDemoClient):
            def complete(self, messages, tools):
                if "你是 CodingAgent" in messages[0]["content"]:
                    return {"content": FIXED}
                if "你是 FixAgent" in messages[0]["content"]:
                    raise AssertionError("Fix must be skipped")
                return super().complete(messages, tools)
        state = Workflow(CleanClient(), self.root).run(DEMO_REQUIREMENT)
        self.assertEqual(state.status, "completed")
        self.assertEqual(state.revision, 0)

    def test_fix_receives_requirement_code_and_review(self):
        class CapturingClient(WorkflowDemoClient):
            def complete(client, messages, tools):
                if "你是 FixAgent" in messages[0]["content"]:
                    context = json.loads(messages[1]["content"])
                    self.assertEqual(context["requirement"], DEMO_REQUIREMENT)
                    self.assertIn("return a / b", context["code"])
                    self.assertIn("ZeroDivisionError", context["review"]["issues"][0]["message"])
                return super().complete(messages, tools)
        self.assertEqual(Workflow(CapturingClient(), self.root).run(DEMO_REQUIREMENT).status, "completed")

    def test_bad_review_fails_without_fixing(self):
        client = ScriptedClient([{"content": "x = 1"}, {"content": "not json"}])
        state = Workflow(client, self.root).run("生成模块")
        self.assertEqual(state.status, "failed")
        self.assertEqual(state.stage, "review")
        self.assertIn("JSON", state.error)
        self.assertTrue((Path(state.run_dir) / "state.json").exists())
        self.assertFalse((Path(state.run_dir) / "final.py").exists())

    def test_network_failure_preserves_draft(self):
        class Broken(WorkflowDemoClient):
            def complete(self, messages, tools):
                if "你是 FixAgent" in messages[0]["content"]:
                    raise ModelError("模型网络请求失败")
                return super().complete(messages, tools)
        state = Workflow(Broken(), self.root).run(DEMO_REQUIREMENT)
        self.assertEqual(state.status, "failed")
        self.assertEqual(state.stage, "fix")
        self.assertEqual(len(state.reviews), 1)
        self.assertTrue((Path(state.run_dir) / state.code_path).exists())

    def test_syntax_error_cannot_be_approved(self):
        response = json.dumps({"approved": True, "summary": "通过", "issues": []})
        client = ScriptedClient([{"content": "def f(:"}, {"content": response}])
        state = Workflow(client,
                         self.root, max_fixes=0).run("生成函数")
        self.assertEqual(state.status, "needs_attention")
        self.assertFalse(state.reviews[0]["static_analysis"]["syntax_ok"])
        context = json.loads(client.requests[1][-1]["content"].split("\n", 1)[1])
        self.assertEqual(context["code"], "def f(:\n")
        self.assertFalse(context["static_analysis"]["syntax_ok"])
        self.assertIn("最终只返回", client.requests[1][0]["content"])

    def test_repeated_runs_do_not_overwrite(self):
        workflow = Workflow(WorkflowDemoClient(), self.root)
        first = workflow.run(DEMO_REQUIREMENT)
        second = workflow.run(DEMO_REQUIREMENT)
        self.assertNotEqual(first.run_dir, second.run_dir)
        self.assertEqual(len(list(self.root.iterdir())), 2)

    def test_strict_review_contract(self):
        for review in ({"approved": "false", "summary": "x", "issues": []},
                       {"approved": True, "summary": "x", "issues": [{}]},
                       {"approved": False, "summary": "x", "issues": []}, []):
            with self.subTest(review=review), self.assertRaises(ModelError):
                parse_review(json.dumps(review))

    def test_code_fences_and_empty_reply(self):
        agent = CodingAgent(ScriptedClient([{"content": "```python\nx = 1\n```"}, {"content": ""}]))
        state = WorkflowState("生成模块", str(self.root))
        self.assertIs(agent.run(state), state)
        self.assertEqual(state.code, "x = 1\n")
        with self.assertRaises(ModelError):
            agent.run(state)

    def test_invalid_inputs_do_not_create_runs(self):
        for count in (-1, 6, True):
            with self.assertRaises(ValueError):
                Workflow(WorkflowDemoClient(), self.root, max_fixes=count)
        for requirement in ("", " " * 10, "x" * 8001):
            with self.assertRaises(ValueError):
                Workflow(WorkflowDemoClient(), self.root).run(requirement)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_cli_exit_codes_and_custom_demo_rejection(self):
        with patch("sys.stdout", new_callable=io.StringIO), patch("sys.stderr", new_callable=io.StringIO):
            self.assertEqual(main(["--demo", "--output-dir", str(self.root)]), 0)
            self.assertEqual(main(["--demo", "--max-fixes", "0", "--output-dir", str(self.root)]), 2)
            with self.assertRaises(SystemExit):
                main(["--demo", "任意需求"])

    def test_client_tools_are_only_sent_when_present(self):
        raw = b'{"choices":[{"message":{"role":"assistant","content":"OK"}}]}'
        opener = FakeOpener([raw, raw])
        client = ChatClient("test", "https://example.com", "model", opener=opener)
        client.complete([], [])
        schema = [{"type": "function", "function": {"name": "read_file"}}]
        client.complete([], schema)
        self.assertNotIn("tools", json.loads(opener.requests[0].data))
        self.assertEqual(json.loads(opener.requests[1].data)["tools"], schema)


if __name__ == "__main__":
    unittest.main()
