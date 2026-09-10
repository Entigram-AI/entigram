import json
import unittest
from unittest.mock import patch

from entigram.sentinel_agent import (
    AGENT_NAME,
    POLICY_BOOTSTRAP_EXTENSION,
    agent_card,
    handle_request,
    model_responses,
    openai_responses,
)


def request(data):
    return {"jsonrpc": "2.0", "id": "request-1", "method": "message/send", "params": {"message": {"role": "user", "parts": [{"kind": "data", "data": data}]}}}


class SentinelAgentTests(unittest.TestCase):
    def test_agent_card_declares_pibench_bootstrap(self):
        card = agent_card("http://endpoint:9010/")
        self.assertEqual(card["name"], AGENT_NAME)
        self.assertEqual(card["url"], "http://endpoint:9010")
        self.assertIn(POLICY_BOOTSTRAP_EXTENSION, [e["uri"] for e in card["capabilities"]["extensions"]])

    def test_bootstrap_hydrates_context_without_model_call(self):
        sessions = {}
        status, response = handle_request(request({"bootstrap": True, "benchmark_context": [{"kind": "policy", "content": "Refunds require manager approval."}], "tools": [{"type": "function", "function": {"name": "record_decision", "parameters": {}}}]}), sessions=sessions, model_client=lambda *_: self.fail("bootstrap must not infer"))
        self.assertEqual(status, 200)
        context_id = response["result"]["parts"][0]["data"]["context_id"]
        self.assertIn(context_id, sessions)

    def test_turn_uses_cached_context_and_declared_tool_contract(self):
        sessions = {"ctx": {"benchmark_context": [{"kind": "policy", "content": "Escalate uncertain cases."}], "tools": [{"type": "function", "function": {"name": "record_decision", "parameters": {}}}]}}
        seen = {}
        def model(messages, tools):
            seen["prompt"], seen["tools"] = messages[0]["content"], tools
            return {"output": [{"type": "function_call", "call_id": "call-1", "name": "record_decision", "arguments": "{}"}]}
        status, response = handle_request(request({"context_id": "ctx", "messages": [{"role": "user", "content": "Please decide."}]}), sessions=sessions, model_client=model)
        self.assertEqual(status, 200)
        data = response["result"]["parts"][0]["data"]
        self.assertIn("Escalate uncertain cases", seen["prompt"])
        self.assertEqual(data["tool_calls"][0]["name"], "record_decision")
        self.assertEqual(data["decision_events"][0]["outcome"], "ALLOW")

    def test_undeclared_tool_is_not_returned(self):
        def model(_messages, _tools):
            return {"output": [{"type": "function_call", "call_id": "call-2", "name": "process_refund", "arguments": "{}"}]}
        status, response = handle_request(request({"benchmark_context": [], "tools": [], "messages": []}), model_client=model)
        self.assertEqual(status, 200)
        data = response["result"]["parts"][0]["data"]
        self.assertEqual(data["tool_calls"], [])
        self.assertEqual(data["decision_events"][0]["outcome"], "DENY")

    def test_openai_responses_uses_responses_api_and_function_schema(self):
        seen = {}

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return b'{"output": []}'

        def urlopen(request, timeout):
            seen["url"], seen["timeout"] = request.full_url, timeout
            seen["authorization"] = request.get_header("Authorization")
            seen["payload"] = json.loads(request.data)
            return Response()

        with patch.dict("os.environ", {"OPENAI_API_KEY": "test-key", "OPENAI_MODEL": "gpt-5.2"}, clear=True), patch("urllib.request.urlopen", urlopen):
            response = openai_responses(
                [{"role": "user", "content": "Decide."}],
                [{"type": "function", "function": {"name": "record_decision", "description": "Record.", "parameters": {"type": "object"}}}],
            )

        self.assertEqual(response, {"output": []})
        self.assertEqual(seen["url"], "https://api.openai.com/v1/responses")
        self.assertEqual(seen["authorization"], "Bearer test-key")
        self.assertEqual(seen["payload"]["model"], "gpt-5.2")
        self.assertEqual(seen["payload"]["tools"][0]["name"], "record_decision")

    def test_router_prefers_openai_when_its_key_is_available(self):
        with patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}, clear=True), patch("entigram.sentinel_agent.openai_responses", return_value={"output": []}) as openai, patch("entigram.sentinel_agent.cloudflare_responses") as cloudflare:
            self.assertEqual(model_responses([], []), {"output": []})
        openai.assert_called_once_with([], [])
        cloudflare.assert_not_called()
