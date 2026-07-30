import os
import unittest
from unittest import mock

from scripts import local_llm


class LocalLLMRoutingTests(unittest.TestCase):
    def test_screening_route_uses_small_model_by_default(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            route = local_llm.resolve_route("screening")

        self.assertEqual(route["task"], "batch_screening")
        self.assertEqual(route["model"], "qwen3:8b")
        self.assertEqual(route["mode_prefix"], "/no_think")

    def test_resume_route_uses_environment_override(self):
        with mock.patch.dict(os.environ, {"LOCAL_LLM_RESUME_MODEL": "custom-resume"}, clear=True):
            route = local_llm.resolve_route("resume_tailoring")

        self.assertEqual(route["model"], "custom-resume")
        self.assertEqual(route["mode_prefix"], "/think")

    def test_chat_completion_prefixes_task_mode(self):
        captured_payloads = []

        def fake_post(payload, timeout):
            captured_payloads.append(payload)
            return {"choices": [{"message": {"content": "{}"}}]}

        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch.object(local_llm, "_post_chat", side_effect=fake_post):
                local_llm.chat_completion(
                    "application_answer",
                    [{"role": "user", "content": "Draft this answer."}],
                    json_mode=True,
                )

        self.assertTrue(captured_payloads)
        content = captured_payloads[0]["messages"][0]["content"]
        self.assertTrue(content.startswith("/no_think\n"))
        self.assertEqual(captured_payloads[0]["model"], "qwen3:14b")

    def test_chat_json_parses_markdown_json(self):
        def fake_completion(*args, **kwargs):
            return {"choices": [{"message": {"content": "```json\n{\"ok\": true}\n```"}}]}

        with mock.patch.object(local_llm, "chat_completion", side_effect=fake_completion):
            payload = local_llm.chat_json("resume_tailoring", [{"role": "user", "content": "x"}])

        self.assertEqual(payload, {"ok": True})


if __name__ == "__main__":
    unittest.main()
