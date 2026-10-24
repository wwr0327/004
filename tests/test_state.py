import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from workflow import WorkflowEngine, WorkflowState, ReviewResult
from workflow.base_agent import BaseAgent
from workflow.cli import main
from code_review_agent.workflow_demo import WorkflowDemoClient
from workflow.prompts import DEMO_REQUIREMENT


class StateTests(unittest.TestCase):
    def test_independent_defaults_and_json_round_trip(self):
        first, second = WorkflowState("a"), WorkflowState("b")
        first.logs.append({"message": "a"})
        self.assertEqual(second.logs, [])
        first.current_step = "review"
        self.assertEqual(first.stage, "review")
        first.review_result = ReviewResult.from_dict({"approved": True, "summary": "通过", "issues": []})
        saved = json.loads(json.dumps(first.to_dict()))
        self.assertTrue(saved["review_result"]["passed"])
        self.assertTrue(saved["review_passed"])

    def test_agent_interface_is_required(self):
        with self.assertRaises(TypeError):
            BaseAgent()

    def test_injected_review_and_skip_fix(self):
        class Reviewer(BaseAgent):
            def run(self, state):
                self.code = state.code
                state.review_result = ReviewResult(True, "测试节点通过")
                state.iteration += 1
                state.reviews.append(state.review_result.to_dict() | {"revision": state.revision})
                return state
        reviewer = Reviewer()
        with tempfile.TemporaryDirectory() as directory:
            state = WorkflowEngine(WorkflowDemoClient(), directory, review_agent=reviewer).run(DEMO_REQUIREMENT)
            self.assertEqual(state.status, "completed")
            self.assertEqual(state.iteration, 1)
            self.assertEqual(state.final_code, reviewer.code)
            self.assertEqual(state.revision, 0)

    def test_logs_include_tools_in_state_and_file(self):
        with tempfile.TemporaryDirectory() as directory:
            state = WorkflowEngine(WorkflowDemoClient(), directory).run(DEMO_REQUIREMENT)
            lines = (Path(state.run_dir) / "workflow.log").read_text(encoding="utf-8").splitlines()
            self.assertEqual([json.loads(line) for line in lines], state.logs)
            self.assertTrue(any(log["status"] == "tool" for log in state.logs))
            self.assertEqual(state.iteration, 2)
            self.assertEqual(state.final_code, state.code)

    def test_failed_node_cannot_corrupt_shared_state(self):
        class BadFixer(BaseAgent):
            def run(self, state):
                state.code = "corrupted"
                raise ValueError("repair failed")
        with tempfile.TemporaryDirectory() as directory:
            state = WorkflowEngine(WorkflowDemoClient(), directory, fix_agent=BadFixer()).run(DEMO_REQUIREMENT)
            self.assertEqual(state.status, "failed")
            self.assertNotEqual(state.code, "corrupted")
            self.assertEqual(state.revision, 0)
            self.assertEqual(state.logs[-1]["status"], "failed")

    def test_no_args_is_offline_demo(self):
        with patch("workflow.cli.connect_model") as connect, patch("workflow.cli.WorkflowEngine") as engine:
            engine.return_value.run.return_value = WorkflowState("demo", status="completed")
            self.assertEqual(main([]), 0)
            connect.assert_not_called()
            self.assertEqual(engine.call_args.args[2], 2)
