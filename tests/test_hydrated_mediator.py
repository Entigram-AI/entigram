import json
import unittest
from entigram.governance.hydrated_mediator import (
    ExplicitRule,
    HydratedPolicyMediator,
    PolicyEvidence,
    SessionState,
    ToolResultProvenance,
)
from entigram.sentinel_agent import handle_request


def make_request(data):
    return {
        "jsonrpc": "2.0",
        "id": "req-1",
        "method": "message/send",
        "params": {
            "message": {
                "role": "user",
                "parts": [{"kind": "data", "data": data}],
            }
        },
    }


class HydratedPolicyMediatorTests(unittest.TestCase):
    def setUp(self):
        self.policy_context = [
            {
                "kind": "policy",
                "id": "pol-001",
                "content": "All requests must follow POL-ACCOUNT-01. Action issue_refund requires prior execution of verify_customer.",
            }
        ]
        self.tools = [
            {
                "type": "function",
                "function": {
                    "name": "verify_customer",
                    "description": "Verifies customer identity.",
                    "parameters": {
                        "type": "object",
                        "properties": {"customer_id": {"type": "string"}},
                        "required": ["customer_id"],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "issue_refund",
                    "description": "Issues a refund. Prerequisite: verify_customer",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "refund_id": {"type": "string"},
                            "policy_reference": {"type": "string"},
                        },
                        "required": ["refund_id"],
                        "additionalProperties": False,
                    },
                },
            },
        ]

    def test_accepts_only_supplied_bootstrap_context_and_declared_tools(self):
        mediator = HydratedPolicyMediator(self.policy_context, self.tools)
        self.assertEqual(len(mediator.evidence_model), 1)
        self.assertEqual(mediator.evidence_model[0].id, "pol-001")
        self.assertTrue(mediator.evidence_model[0].digest)
        self.assertEqual(mediator.permitted_policy_references, ["POL-ACCOUNT-01"])
        self.assertEqual([t["name"] for t in mediator.tools], ["verify_customer", "issue_refund"])

    def test_builds_session_local_evidence_linked_policy_model_with_explicit_rules(self):
        mediator = HydratedPolicyMediator(self.policy_context, self.tools)
        prereqs = mediator.get_explicit_prerequisites("issue_refund")
        self.assertIn("verify_customer", prereqs)

        # Check evidence linking
        linked_rules = [r for r in mediator.explicit_rules if r.target_tool == "issue_refund"]
        self.assertTrue(len(linked_rules) > 0)
        for rule in linked_rules:
            if rule.evidence_ids:
                self.assertIn("pol-001", rule.evidence_ids)

    def test_tracks_tool_result_state_and_provenance(self):
        mediator = HydratedPolicyMediator(self.policy_context, self.tools)
        messages = [
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "call-v1",
                        "function": {
                            "name": "verify_customer",
                            "arguments": '{"customer_id": "cust-100"}',
                        },
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call-v1",
                "content": '{"status": "verified", "customer_id": "cust-100"}',
            },
        ]
        mediator.update_state(messages)
        self.assertIn("verify_customer", mediator.state.executed_tools)
        self.assertEqual(len(mediator.state.tool_results), 1)

        result = mediator.state.tool_results[0]
        self.assertEqual(result.call_id, "call-v1")
        self.assertEqual(result.tool_name, "verify_customer")
        self.assertEqual(result.arguments, {"customer_id": "cust-100"})
        self.assertEqual(result.output, '{"status": "verified", "customer_id": "cust-100"}')

        # A2A callers can replay prior history on later turns without creating
        # phantom state transitions.
        mediator.update_state(messages)
        self.assertEqual(len(mediator.state.tool_results), 1)

    def test_identifies_enabled_tools_from_high_confidence_explicit_prerequisites(self):
        mediator = HydratedPolicyMediator(self.policy_context, self.tools)

        # Initially, issue_refund is disabled because verify_customer has not been executed
        readiness_before = mediator.evaluate_tool_readiness("issue_refund")
        self.assertFalse(readiness_before["enabled"])
        self.assertEqual(readiness_before["missing_prerequisites"], ["verify_customer"])

        enabled_before = [t["name"] for t in mediator.get_enabled_tools()]
        self.assertIn("verify_customer", enabled_before)
        self.assertNotIn("issue_refund", enabled_before)

        # Simulate execution of verify_customer
        mediator.state.record_tool_result(
            call_id="call-v1",
            tool_name="verify_customer",
            arguments={"customer_id": "cust-100"},
            output="verified",
        )

        readiness_after = mediator.evaluate_tool_readiness("issue_refund")
        self.assertTrue(readiness_after["enabled"])
        self.assertEqual(readiness_after["missing_prerequisites"], [])

        enabled_after = [t["name"] for t in mediator.get_enabled_tools()]
        self.assertIn("issue_refund", enabled_after)

    def test_returns_structured_pre_dispatch_denial_for_missing_prerequisite(self):
        mediator = HydratedPolicyMediator(self.policy_context, self.tools)
        proposal = {
            "id": "call-rf1",
            "name": "issue_refund",
            "arguments": {"refund_id": "ref-500", "policy_reference": "POL-ACCOUNT-01"},
        }
        admitted, events = mediator.admit_proposals([proposal])
        self.assertEqual(admitted, [])
        self.assertEqual(len(events), 1)

        event = events[0]
        self.assertEqual(event["outcome"], "DENY")
        self.assertFalse(event["side_effect_permitted"])
        self.assertIn("missing_prerequisite", event["reason_codes"])
        self.assertEqual(event["missing_prerequisites"], ["verify_customer"])

    def test_returns_structured_pre_dispatch_denial_for_unverified_policy_reference(self):
        mediator = HydratedPolicyMediator(self.policy_context, self.tools)

        # Satisfy prerequisite first
        mediator.state.record_tool_result("c1", "verify_customer", {"customer_id": "c1"}, "ok")

        proposal = {
            "id": "call-rf2",
            "name": "issue_refund",
            "arguments": {"refund_id": "ref-500", "policy_reference": "INVALID-REF-99"},
        }
        admitted, events = mediator.admit_proposals([proposal])
        self.assertEqual(admitted, [])
        self.assertEqual(events[0]["outcome"], "DENY")
        self.assertIn("unverified_policy_reference", events[0]["reason_codes"])

    def test_admits_valid_proposal_when_all_explicit_rules_are_satisfied(self):
        mediator = HydratedPolicyMediator(self.policy_context, self.tools)
        mediator.state.record_tool_result("c1", "verify_customer", {"customer_id": "c1"}, "ok")

        proposal = {
            "id": "call-rf3",
            "name": "issue_refund",
            "arguments": {"refund_id": "ref-500", "policy_reference": "POL-ACCOUNT-01"},
        }
        admitted, events = mediator.admit_proposals([proposal])
        self.assertEqual(len(admitted), 1)
        self.assertEqual(admitted[0]["name"], "issue_refund")
        self.assertEqual(events[0]["outcome"], "ALLOW")
        self.assertTrue(events[0]["side_effect_permitted"])

    def test_sentinel_agent_automatic_hydration_and_session_mediator(self):
        sessions = {}

        # 1. Bootstrap turn
        bootstrap_data = {
            "bootstrap": True,
            "policy_context": self.policy_context,
            "tools": self.tools,
        }
        status, response = handle_request(make_request(bootstrap_data), sessions=sessions)
        self.assertEqual(status, 200)
        ctx_id = response["result"]["parts"][0]["data"]["context_id"]
        self.assertIn(ctx_id, sessions)
        self.assertIn("mediator", sessions[ctx_id])
        hydration = response["result"]["parts"][0]["data"]["hydration"]
        self.assertEqual(hydration["policy_evidence_count"], 1)
        self.assertGreaterEqual(hydration["explicit_rule_count"], 1)
        self.assertEqual(hydration["declared_tool_count"], 2)
        self.assertEqual(hydration["enabled_tool_count"], 1)
        self.assertNotIn("content", hydration)

        # 2. Turn 1 proposing issue_refund without prior verify_customer (should deny)
        def model_attempt_refund(_messages, _tools):
            return {
                "output": [
                    {
                        "type": "function_call",
                        "call_id": "c-ref",
                        "name": "issue_refund",
                        "arguments": '{"refund_id": "ref-1"}',
                    }
                ]
            }

        turn1_data = {
            "context_id": ctx_id,
            "messages": [{"role": "user", "content": "Please issue refund ref-1."}],
        }
        status, payload = handle_request(
            make_request(turn1_data), sessions=sessions, model_client=model_attempt_refund
        )
        self.assertEqual(status, 200)
        data1 = payload["result"]["parts"][0]["data"]
        self.assertEqual(data1["tool_calls"], [])
        self.assertEqual(data1["decision_events"][0]["outcome"], "DENY")
        self.assertIn("missing_prerequisite", data1["decision_events"][0]["reason_codes"])
        self.assertEqual(data1["hydration"]["state_transition_count"], 0)

        # 3. Turn 2 after verify_customer has executed
        turn2_messages = [
            {"role": "user", "content": "Verify customer first."},
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "c-v",
                        "function": {
                            "name": "verify_customer",
                            "arguments": '{"customer_id": "cust-1"}',
                        },
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "c-v",
                "content": '{"status": "ok"}',
            },
            {"role": "user", "content": "Now issue refund ref-1."},
        ]

        def model_issue_refund(_messages, _tools):
            return {
                "output": [
                    {
                        "type": "function_call",
                        "call_id": "c-ref-2",
                        "name": "issue_refund",
                        "arguments": '{"refund_id": "ref-1"}',
                    }
                ]
            }

        turn2_data = {"context_id": ctx_id, "messages": turn2_messages}
        status, payload = handle_request(
            make_request(turn2_data), sessions=sessions, model_client=model_issue_refund
        )
        self.assertEqual(status, 200)
        data2 = payload["result"]["parts"][0]["data"]
        self.assertEqual(len(data2["tool_calls"]), 1)
        self.assertEqual(data2["tool_calls"][0]["name"], "issue_refund")
        self.assertEqual(data2["decision_events"][0]["outcome"], "ALLOW")
        self.assertEqual(data2["hydration"]["state_transition_count"], 1)

    def test_declared_completion_contract_constrains_follow_up_to_finalizer(self):
        context = [
            {"kind": "policy", "content": "POL-1 permits verified refunds."},
            {
                "kind": "task",
                "content": (
                    "Complete any operational action first. Record your final decision by calling "
                    "the record_decision tool with grounded rationale."
                ),
            },
        ]
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "process_action",
                    "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "record_decision",
                    "parameters": {
                        "type": "object",
                        "properties": {"decision": {"type": "string"}},
                        "required": ["decision"],
                        "additionalProperties": False,
                    },
                },
            },
        ]
        sessions = {}
        _, bootstrap = handle_request(make_request({"bootstrap": True, "policy_context": context, "tools": tools}), sessions=sessions)
        context_id = bootstrap["result"]["parts"][0]["data"]["context_id"]

        def operational_model(_messages, _tools):
            return {"output": [{"type": "function_call", "call_id": "operate", "name": "process_action", "arguments": "{}"}]}

        _, first = handle_request(
            make_request({"context_id": context_id, "messages": [{"role": "user", "content": "Handle this case."}]}),
            sessions=sessions,
            model_client=operational_model,
        )
        self.assertEqual(first["result"]["parts"][0]["data"]["tool_calls"][0]["name"], "process_action")

        observed = {}
        def finalization_model(messages, active_tools):
            observed["prompt"] = messages[0]["content"]
            observed["tool_names"] = [tool["function"]["name"] for tool in active_tools]
            return {"output": [{"type": "function_call", "call_id": "finalize", "name": "record_decision", "arguments": '{"decision": "ALLOW"}'}]}

        messages = [
            {"role": "user", "content": "Handle this case."},
            {"role": "assistant", "tool_calls": [{"id": "operate", "function": {"name": "process_action", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "operate", "content": '{"status": "complete"}'},
        ]
        _, second = handle_request(
            make_request({"context_id": context_id, "messages": messages}),
            sessions=sessions,
            model_client=finalization_model,
        )
        data = second["result"]["parts"][0]["data"]
        self.assertEqual(observed["tool_names"], ["record_decision"])
        self.assertIn("finalization action is now pending", observed["prompt"])
        self.assertEqual(data["tool_calls"][0]["name"], "record_decision")
        # A proposed finalizer remains pending until its executor result is
        # observed on the next turn.
        self.assertTrue(data["hydration"]["completion_contract"]["finalization_pending"])

        finalized_messages = messages + [
            {"role": "assistant", "tool_calls": [{"id": "finalize", "function": {"name": "record_decision", "arguments": '{"decision": "ALLOW"}'}}]},
            {"role": "tool", "tool_call_id": "finalize", "content": '{"status": "recorded"}'},
        ]
        _, completed = handle_request(
            make_request({"context_id": context_id, "messages": finalized_messages}),
            sessions=sessions,
            model_client=lambda _messages, _tools: {"output": []},
        )
        completed_data = completed["result"]["parts"][0]["data"]
        self.assertFalse(completed_data["hydration"]["completion_contract"]["finalization_pending"])


if __name__ == "__main__":
    unittest.main()
