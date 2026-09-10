import unittest

from entigram.sentinel_agent import AGENT_NAME, POLICY_BOOTSTRAP_EXTENSION, agent_card, handle_request


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
