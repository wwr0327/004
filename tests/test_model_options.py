import io
import json
import unittest
from unittest.mock import patch
import urllib.error

from code_review_agent.llm import ChatClient, ModelError
from code_review_agent.workflow import main, parse_review
from test_agent import FakeOpener


class ModelOptionsTests(unittest.TestCase):
    def client(self, env, **options):
        with patch.dict("os.environ", env, clear=True), patch("code_review_agent.llm.load_env_file"):
            return ChatClient.from_env(**options)

    def test_explicit_model_overrides_environment(self):
        client = self.client({"DEEPSEEK_API_KEY": "secret", "LLM_MODEL": "old"}, model="chosen")
        self.assertEqual(client.model, "chosen")

    def test_provider_switch_ignores_old_url_model_and_key(self):
        client = self.client({"DEEPSEEK_API_KEY": "private", "LLM_BASE_URL": "https://api.deepseek.com",
                              "LLM_MODEL": "deepseek-flash"}, provider="ollama", model="local-model")
        self.assertEqual(client.url, "http://localhost:11434/v1/chat/completions")
        self.assertEqual(client.model, "local-model")
        self.assertEqual(client.api_key, "ollama")

    def test_custom_does_not_reuse_deepseek_key(self):
        with self.assertRaisesRegex(ValueError, "LLM_API_KEY"):
            self.client({"DEEPSEEK_API_KEY": "private"}, provider="custom",
                        base_url="https://example.com/v1", model="model")

    def test_deepseek_profile_rejects_different_host(self):
        with self.assertRaisesRegex(ValueError, "custom"):
            self.client({"DEEPSEEK_API_KEY": "private"}, provider="deepseek",
                        base_url="https://example.com/v1")

    def test_legacy_custom_config_uses_generic_key(self):
        client = self.client({"DEEPSEEK_API_KEY": "wrong", "LLM_API_KEY": "correct",
                              "LLM_BASE_URL": "https://example.com/v1", "LLM_MODEL": "model"})
        self.assertEqual(client.provider, "custom")
        self.assertEqual(client.api_key, "correct")

    def test_remote_ollama_requires_own_key(self):
        with self.assertRaisesRegex(ValueError, "OLLAMA_API_KEY"):
            self.client({}, provider="ollama", base_url="https://example.com/v1", model="model")

    def test_discovery_does_not_require_model(self):
        client = self.client({}, provider="ollama", discovery=True)
        self.assertEqual(client.provider, "ollama")
        with self.assertRaisesRegex(ValueError, "select-model"):
            self.client({}, provider="ollama")

    def test_list_models_request_and_validation(self):
        opener = FakeOpener([b'{"data":[{"id":"b"},{"id":"a"},{"id":"b"}]}'])
        client = ChatClient("secret", "https://example.com/v1", "model", opener=opener)
        self.assertEqual(client.list_models(), ["a", "b"])
        self.assertEqual(opener.requests[0].full_url, "https://example.com/v1/models")
        self.assertEqual(opener.requests[0].get_method(), "GET")
        for payload in ([], {"data": [None]}, {"data": [{"id": 1}]}):
            client.opener = FakeOpener([json.dumps(payload).encode()])
            with self.assertRaises(ModelError):
                client.list_models()

    def test_malformed_chat_and_http_error_are_controlled(self):
        for payload in ([], {"choices": [None]}, {"choices": [1]}):
            client = ChatClient("secret", "https://example.com", "model",
                                opener=FakeOpener([json.dumps(payload).encode()]))
            with self.assertRaises(ModelError):
                client.complete([], [])
        for payload in ([], {"error": "bad"}, {"error": {"message": "bad secret"}}):
            error = urllib.error.HTTPError("https://example.com", 401, "bad", {},
                                           io.BytesIO(json.dumps(payload).encode()))
            client = ChatClient("secret", "https://example.com", "model", opener=FakeOpener([error]))
            with self.assertRaises(ModelError) as context:
                client.complete([], [])
            self.assertNotIn("secret", str(context.exception))

    def test_list_and_check_do_not_require_task_or_create_workflow(self):
        with patch("code_review_agent.model_options.ChatClient.from_env") as factory, \
                patch("workflow.cli.WorkflowEngine") as workflow, \
                patch("sys.stdout", new_callable=io.StringIO), patch("sys.stderr", new_callable=io.StringIO):
            factory.return_value.list_models.return_value = ["model"]
            factory.return_value.complete.return_value = {"content": "OK"}
            self.assertEqual(main(["--list-models"]), 0)
            self.assertEqual(main(["--check-connection"]), 0)
            workflow.assert_not_called()

    def test_interactive_selection_and_invalid_selection(self):
        with patch("code_review_agent.model_options.ChatClient.from_env") as factory, \
                patch("builtins.input", return_value="2"), \
                patch("sys.stdout", new_callable=io.StringIO), patch("sys.stderr", new_callable=io.StringIO):
            factory.return_value.list_models.return_value = ["first", "second"]
            factory.return_value.complete.return_value = {"content": "OK"}
            self.assertEqual(main(["--select-model", "--check-connection"]), 0)
            self.assertEqual(factory.return_value.model, "second")
            with patch("builtins.input", return_value="0"):
                self.assertEqual(main(["--select-model", "--check-connection"]), 1)

    def test_demo_rejects_connection_flags(self):
        with patch("sys.stderr", new_callable=io.StringIO):
            with self.assertRaises(SystemExit):
                main(["--demo", "--model", "model"])

    def test_invalid_issue_severity_does_not_crash(self):
        with self.assertRaises(ModelError):
            parse_review(json.dumps({"approved": False, "summary": "bad", "issues": [
                {"severity": [], "line": 1, "message": "m", "suggestion": "s"}]}))
