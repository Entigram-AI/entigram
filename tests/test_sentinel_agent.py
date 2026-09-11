import json
import unittest
from jsonschema import Draft202012Validator
from unittest.mock import patch

from entigram.sentinel_agent import (
    AGENT_NAME,
    POLICY_BOOTSTRAP_EXTENSION,
    STRUCTURED_PLAN_TOOL,
    PLAN_REVIEW_TOOL,
    _review_plan,
    _structured_plan_response,
    agent_card,
    handle_request,
    model_responses,
    openai_responses,
    _responses_tools,
)


def request(data):
    return {"jsonrpc": "2.0", "id": "request-1", "method": "message/send", "params": {"message": {"role": "user", "parts": [{"kind": "data", "data": data}]}}}


class SentinelAgentTests(unittest.TestCase):
    def test_structured_planner_preserves_per_tool_argument_contracts(self):
        from entigram.sentinel_agent import _structured_plan_tool
        tools = [{"name": "allocate_units", "parameters": {
            "type": "object", "properties": {
                "units": {"type": "integer", "minimum": 1},
                "source": {"type": "string", "enum": ["APPROVED"]},
                "note": {"type": "string"},
            }, "required": ["units", "source"], "additionalProperties": False,
        }}, {"name": "notify", "parameters": {
            "recipient": {"type": "string", "required": True}}}]
        original = json.dumps(tools, sort_keys=True)
        schema = _structured_plan_tool(tools)[0]["function"]["parameters"]
        validator = Draft202012Validator(schema)
        for arguments, valid in [
            ({"units": 2, "source": "APPROVED"}, True),
            ({"units": 0, "source": "APPROVED"}, False),
            ({"units": 2, "source": "UNKNOWN"}, False),
            ({"units": 2}, False),
            ({"units": 2, "source": "APPROVED", "extra": True}, False),
        ]:
            with self.subTest(arguments=arguments):
                self.assertEqual(validator.is_valid({"actions": [{"name": "allocate_units", "arguments": arguments}]}), valid)
        self.assertTrue(validator.is_valid({"actions": [{"name": "notify", "arguments": {"recipient": "ops"}}]}))
        self.assertFalse(validator.is_valid({"actions": [{"name": "notify", "arguments": {"units": 1}}]}))
        self.assertEqual(json.dumps(tools, sort_keys=True), original)

    def test_adjudication_requires_grounded_operational_conditions_not_executor_defaults(self):
        from entigram.sentinel_agent import _adjudication_prompt
        prompt = _adjudication_prompt("Policy evidence")
        self.assertIn("Do not rely on an executor default", prompt)
        self.assertIn("do not invent values for genuinely unknown fields", prompt)
        self.assertIn("state the grounded policy result directly and concisely", prompt)

    def test_admission_prompt_includes_source_authorized_ambiguity_guidance(self):
        from entigram.sentinel_agent import _admission_prompt
        prompt = _admission_prompt([], [], ambiguity_guidance="Explicit ambiguity clause")
        self.assertIn("actual policy conflict", prompt)
        self.assertIn("Explicit ambiguity clause", prompt)

    def test_structured_planner_keeps_local_references_in_each_tool_scope(self):
        from entigram.sentinel_agent import _structured_plan_tool
        tools = [{"name": name, "parameters": {
            "type": "object", "$defs": {"value": {"type": "integer", "minimum": minimum}},
            "properties": {"value": {"$ref": "#/$defs/value"}}, "required": ["value"],
            "additionalProperties": False,
        }} for name, minimum in (("small", 1), ("large", 10))]
        validator = Draft202012Validator(_structured_plan_tool(tools)[0]["function"]["parameters"])
        for name, value, valid in (("small", 1, True), ("small", 0, False), ("large", 1, False), ("large", 10, True)):
            self.assertEqual(validator.is_valid({"actions": [{"name": name, "arguments": {"value": value}}]}), valid)

    def test_structured_planner_without_tools_allows_only_empty_actions(self):
        from entigram.sentinel_agent import _structured_plan_tool
        validator = Draft202012Validator(_structured_plan_tool([])[0]["function"]["parameters"])
        self.assertTrue(validator.is_valid({"actions": [], "customer_message": "Please clarify."}))
        self.assertFalse(validator.is_valid({"actions": [{"name": "invented", "arguments": {}}]}))

    def test_plan_schema_relocates_recursion_and_preserves_annotation_data(self):
        from entigram.sentinel_agent import _structured_plan_tool
        parameters = {"type": "object", "properties": {
            "name": {"type": "string"},
            "child": {"$ref": "#"},
            "data": {"type": "object", "default": {"$ref": "#literal-data"}},
        }, "required": ["name"], "additionalProperties": False}
        schema = _structured_plan_tool([{"name": "store_tree", "parameters": parameters}])[0]["function"]["parameters"]
        validator = Draft202012Validator(schema)
        self.assertTrue(validator.is_valid({"actions": [{"name": "store_tree", "arguments": {"name": "root", "child": {"name": "leaf"}}}]}))
        self.assertFalse(validator.is_valid({"actions": [{"name": "store_tree", "arguments": {"name": "root", "child": {}}}]}))
        self.assertEqual(schema["$defs"]["tool_0"]["properties"]["data"]["default"], {"$ref": "#literal-data"})

    def test_plan_schema_namespaces_anchors_and_preserves_explicit_resource_scope(self):
        from entigram.sentinel_agent import _structured_plan_tool
        for resource_id in (None, "urn:example:tool"):
            parameters = {"type": "object", "$defs": {"value": {"$anchor": "value", "type": "integer", "minimum": 1}},
                          "properties": {"value": {"$ref": "#value"}}, "required": ["value"]}
            if resource_id:
                parameters["$id"] = resource_id
            validator = Draft202012Validator(_structured_plan_tool([{"name": "submit", "parameters": parameters}])[0]["function"]["parameters"])
            self.assertTrue(validator.is_valid({"actions": [{"name": "submit", "arguments": {"value": 1}}]}))
            self.assertFalse(validator.is_valid({"actions": [{"name": "submit", "arguments": {"value": 0}}]}))

    def test_review_logs_only_gate_metadata(self):
        model = unittest.mock.Mock(return_value={"output": [{"type": "function_call", "name": PLAN_REVIEW_TOOL,
            "arguments": json.dumps({"approved": False, "issues": ["Confidential label SECRET-ISSUE"]})}]})
        with self.assertLogs("entigram.sentinel_agent", level="INFO") as logs:
            self.assertFalse(_review_plan("SECRET-POLICY", [], "SECRET-MESSAGE", [], model)[0])
        rendered = "\n".join(logs.output)
        self.assertIn("reason=semantic_rejection", rendered)
        self.assertIn("issue_count=1", rendered)
        self.assertNotIn("SECRET", rendered)
        model.side_effect = RuntimeError("SECRET-PROVIDER-BODY")
        with self.assertLogs("entigram.sentinel_agent", level="INFO") as logs:
            self.assertFalse(_review_plan("", [], "", [], model)[0])
        self.assertNotIn("SECRET", "\n".join(logs.output))
        self.assertIn("reason=review_unavailable", "\n".join(logs.output))

    def test_review_fails_closed_for_malformed_or_conflicting_verdicts(self):
        for verdict in [{"approved": "true", "issues": []}, {"approved": True},
                        {"approved": True, "issues": ["Missing authorization"]},
                        {"approved": True, "issues": [], "actions": []}]:
            with self.subTest(verdict=verdict):
                model = unittest.mock.Mock(return_value={"output": [{"type": "function_call",
                    "name": PLAN_REVIEW_TOOL, "arguments": json.dumps(verdict)}]})
                approved, _ = _review_plan("Policy", [], "", [], model)
                self.assertFalse(approved)
        model = unittest.mock.Mock(side_effect=RuntimeError("unavailable"))
        with self.assertLogs("entigram.sentinel_agent", level="ERROR"):
            self.assertFalse(_review_plan("Policy", [], "", [], model)[0])

    @patch.dict("os.environ", {"ENTIGRAM_SENTINEL_PLAN_EXECUTION": "sequential", "ENTIGRAM_SENTINEL_PLAN_REVIEW": "1"})
    def test_review_rejection_repairs_whole_plan_before_any_dispatch(self):
        def output(name, arguments):
            return {"output": [{"type": "function_call", "name": name, "call_id": "draft",
                                "arguments": json.dumps(arguments)}]}
        tool = {"name": "release_asset", "parameters": {"type": "object", "properties": {}}}
        model = unittest.mock.Mock(side_effect=[
            output(STRUCTURED_PLAN_TOOL, {"actions": [{"name": "release_asset", "arguments": {}}]}),
            output(PLAN_REVIEW_TOOL, {"approved": False, "issues": ["Authorization is not established."]}),
            output(STRUCTURED_PLAN_TOOL, {"actions": [], "customer_message": "Who authorized this release?"}),
            output(PLAN_REVIEW_TOOL, {"approved": True, "issues": []}),
        ])
        _, response = handle_request(request({"tools": [tool], "messages": []}), model_client=model)
        data = response["result"]["parts"][0]["data"]
        self.assertNotIn("tool_calls", data)
        self.assertEqual(data["content"], "Who authorized this release?")
        self.assertEqual([e["approved"] for e in data["decision_events"]], [False, True])
        self.assertIn("Authorization is not established", model.call_args_list[2].args[0][0]["content"])
        self.assertEqual([t["function"]["name"] for t in model.call_args_list[1].args[1]], [PLAN_REVIEW_TOOL])

    @patch.dict("os.environ", {"ENTIGRAM_SENTINEL_PLAN_EXECUTION": "sequential", "ENTIGRAM_SENTINEL_PLAN_REVIEW": "1"})
    def test_semantic_approval_cannot_bypass_native_admission(self):
        def model(messages, tools):
            if tools[0]["function"]["name"] == PLAN_REVIEW_TOOL:
                arguments = {"approved": True, "issues": []}
                name = PLAN_REVIEW_TOOL
            else:
                arguments = {"actions": [{"name": "undeclared", "arguments": {}}]}
                name = STRUCTURED_PLAN_TOOL
            return {"output": [{"type": "function_call", "name": name, "arguments": json.dumps(arguments)}]}
        _, response = handle_request(request({"tools": [], "messages": []}), model_client=model)
        data = response["result"]["parts"][0]["data"]
        self.assertNotIn("tool_calls", data)
        self.assertTrue(any("undeclared_tool" in e.get("reason_codes", []) for e in data["decision_events"]))

    @patch.dict("os.environ", {"ENTIGRAM_SENTINEL_PLAN_EXECUTION": "sequential"})
    def test_sequential_replay_requires_producer_repeatable_contract(self):
        for repeatable in (False, True):
            with self.subTest(repeatable=repeatable):
                tools = [{"name": "update_asset", "parameters": {"type": "object", "properties": {},
                          "x-entigram-repeatable": repeatable}}]
                sessions = {}
                _, boot = handle_request(request({"bootstrap": True, "tools": tools}), sessions)
                context_id = boot["result"]["parts"][0]["data"]["context_id"]
                sessions[context_id]["mediator"].state.record_tool_result("old", "update_asset", {}, {"ok": True})
                model = unittest.mock.Mock(return_value={"output": [{"type": "function_call", "name": STRUCTURED_PLAN_TOOL,
                    "call_id": "new-plan", "arguments": json.dumps({"actions": [{"name": "update_asset", "arguments": {}}]})}]})
                _, response = handle_request(request({"context_id": context_id,
                    "messages": [{"role": "user", "content": "What happened?"}]}), sessions, model)
                data = response["result"]["parts"][0]["data"]
                self.assertEqual("tool_calls" in data, repeatable)
                if not repeatable:
                    self.assertIn("duplicate_completed_action", data["decision_events"][0]["reason_codes"])
                    self.assertEqual(model.call_count, 2)

    @patch.dict("os.environ", {"ENTIGRAM_SENTINEL_PLAN_EXECUTION": "sequential"})
    def test_sequential_plan_waits_for_receipt_and_keeps_order(self):
        tools = [{"name": name, "parameters": {"type": "object", "properties": {}}}
                 for name in ["inspect_asset", "release_asset"]]
        sessions = {}
        _, boot = handle_request(request({"bootstrap": True, "policy_context": [], "tools": tools}), sessions)
        context_id = boot["result"]["parts"][0]["data"]["context_id"]
        model = unittest.mock.Mock(return_value={"output": [{"type": "function_call", "name": STRUCTURED_PLAN_TOOL,
            "call_id": "plan", "arguments": json.dumps({"actions": [
                {"name": "inspect_asset", "arguments": {}}, {"name": "release_asset", "arguments": {}}]})}]})
        messages = [{"role": "user", "content": "Inspect then release the asset."}]
        def send():
            return handle_request(request({"context_id": context_id, "messages": messages}), sessions, model)[1]["result"]["parts"][0]["data"]
        first = send()
        self.assertEqual([c["name"] for c in first["tool_calls"]], ["inspect_asset"])
        self.assertNotIn("tool_calls", send())
        self.assertEqual(model.call_count, 1)
        messages.extend([
            {"role": "assistant", "tool_calls": [{"id": "plan-1", "function": {"name": "inspect_asset", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "plan-1", "content": '{"ok":true}'},
        ])
        second = send()
        self.assertEqual([c["name"] for c in second["tool_calls"]], ["release_asset"])
        self.assertEqual(model.call_count, 1)
        messages.extend([
            {"role": "assistant", "tool_calls": [{"id": "plan-2", "function": {"name": "release_asset", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "plan-2", "content": '{"ok":true}'},
        ])
        model.return_value = {"output": [{"type": "message", "content": [{"type": "output_text", "text": "The asset was released."}]}]}
        completed = send()
        self.assertNotIn("tool_calls", completed)
        self.assertEqual(completed["workflow"]["status"], "completed")
        self.assertEqual(model.call_args.args[1], [])
        self.assertNotIn("tool_calls", send())
        self.assertEqual(model.call_args.args[1], [])

    @patch.dict("os.environ", {"ENTIGRAM_SENTINEL_PLAN_EXECUTION": "sequential"})
    def test_sequential_plan_cannot_bypass_admission(self):
        model = unittest.mock.Mock(return_value={"output": [{"type": "function_call", "name": STRUCTURED_PLAN_TOOL,
            "call_id": "plan", "arguments": json.dumps({"actions": [{"name": "undeclared", "arguments": {}}]})}]})
        _, response = handle_request(request({"tools": [], "messages": []}), model_client=model)
        data = response["result"]["parts"][0]["data"]
        self.assertNotIn("tool_calls", data)
        self.assertEqual(model.call_count, 2)
        self.assertTrue(all("undeclared_tool" in e["reason_codes"] for e in data["decision_events"]))

    def test_admission_feedback_repairs_before_dispatch(self):
        tools = [{"name": "release_asset", "parameters": {"type": "object", "properties": {
            "mode": {"type": "string", "enum": ["approved"]}}, "required": ["mode"]}}]
        def output(mode):
            return {"output": [{"type": "function_call", "name": "release_asset", "call_id": mode,
                                "arguments": json.dumps({"mode": mode})}]}
        model = unittest.mock.Mock(side_effect=[output("invalid"), output("approved")])
        _, payload = handle_request(request({"tools": tools, "messages": []}), model_client=model)
        data = payload["result"]["parts"][0]["data"]
        self.assertEqual(model.call_count, 2)
        self.assertIn("No action from that batch was executed", model.call_args_list[1].args[0][-1]["content"])
        self.assertIn("argument_enum_mismatch", model.call_args_list[1].args[0][-1]["content"])
        self.assertEqual(data["tool_calls"][0]["arguments"], {"mode": "approved"})
        self.assertFalse(data["decision_events"][0]["dispatch_released"])
        self.assertTrue(data["decision_events"][-1]["dispatch_released"])
        self.assertEqual(data["hydration"]["state_transition_count"], 0)

    def test_invalid_batch_never_releases_its_valid_finalizer(self):
        tools = [{"name": "record_outcome", "parameters": {"type": "object"}}]
        response = {"output": [
            {"type": "function_call", "name": "undeclared_operation", "call_id": "op", "arguments": "{}"},
            {"type": "function_call", "name": "record_outcome", "call_id": "audit", "arguments": "{}"},
        ]}
        model = unittest.mock.Mock(return_value=response)
        _, payload = handle_request(request({"tools": tools, "messages": []}), model_client=model)
        data = payload["result"]["parts"][0]["data"]
        self.assertEqual(model.call_count, 2)
        self.assertNotIn("tool_calls", data)
        self.assertTrue(all(not event["dispatch_released"] for event in data["decision_events"]))

    def test_repair_provider_failure_keeps_original_batch_withheld(self):
        response = {"output": [{"type": "function_call", "name": "unknown", "call_id": "op", "arguments": "{}"}]}
        model = unittest.mock.Mock(side_effect=[response, RuntimeError("provider unavailable")])
        with self.assertLogs("entigram.sentinel_agent", level="ERROR"):
            _, payload = handle_request(request({"tools": [], "messages": []}), model_client=model)
        self.assertNotIn("tool_calls", payload["result"]["parts"][0]["data"])
        self.assertEqual(model.call_count, 2)

    def test_responses_preserves_optional_fields_without_implicit_strictness(self):
        from entigram.sentinel_agent import _responses_tools
        schema = {"type": "object", "properties": {
            "record_id": {"type": "string"}, "optional_note": {"type": "string"}}, "required": ["record_id"]}
        tools = [{"name": "inspect_record", "parameters": schema}]
        converted = _responses_tools(tools)[0]
        self.assertIs(converted["strict"], False)
        self.assertEqual(converted["parameters"], schema)
        tools[0]["strict"] = True
        self.assertIs(_responses_tools(tools)[0]["strict"], True)

    def test_adjudication_can_request_missing_evidence_without_action(self):
        from entigram.sentinel_agent import _structured_plan_tool
        response = {"output": [{"type": "function_call", "name": STRUCTURED_PLAN_TOOL,
                                "arguments": json.dumps({"actions": [], "customer_message": "Which asset should be released?"})}]}
        self.assertEqual(_structured_plan_response(response), ("Which asset should be released?", []))
        self.assertNotIn("minItems", _structured_plan_tool([])[0]["function"]["parameters"]["properties"]["actions"])
        response["output"][0]["arguments"] = '{"actions":[]}'
        self.assertIsNone(_structured_plan_response(response))

    def test_text_reply_omits_empty_optional_tool_collection(self):
        response = {"output": [{"type": "message", "content": [{"type": "output_text", "text": "Please confirm the delivery date."}]}]}
        _, payload = handle_request(request({"messages": [], "tools": []}), model_client=lambda *_: response)
        data = payload["result"]["parts"][0]["data"]
        self.assertEqual(data["content"], "Please confirm the delivery date.")
        self.assertNotIn("tool_calls", data)

    def test_history_preserves_customer_message_with_tool_calls(self):
        from entigram.sentinel_agent import _responses_input
        history = [{"role": "assistant", "content": "I can disclose the public label, but not the private score.",
                    "tool_calls": [{"id": "a1", "function": {"name": "record_outcome", "arguments": "{}"}}]},
                   {"role": "tool", "tool_call_id": "a1", "content": '{"ok":true}'}]
        converted = _responses_input(history)
        self.assertEqual(converted[0], {"role": "assistant", "content": history[0]["content"]})
        self.assertEqual(converted[1]["call_id"], "a1")
        self.assertEqual(converted[2]["call_id"], "a1")
        self.assertEqual(converted[2]["type"], "function_call_output")

    def test_adjudicator_receives_complete_business_contract(self):
        from entigram.sentinel_agent import _admission_prompt, _adjudication_prompt
        tools = [{"name": "release_asset", "description": "Release an approved asset.",
                  "parameters": {"asset_id": {"type": "string", "required": True},
                                 "mode": {"type": "string", "enum": ["approved"]}}}]
        prompt = _adjudication_prompt(_admission_prompt([], tools))
        contract = json.loads(prompt.split("Declared tool contracts (names, descriptions, and parameter schemas):\n")[1].splitlines()[0])
        self.assertEqual(contract[0]["description"], "Release an approved asset.")
        self.assertEqual(contract[0]["parameters"]["required"], ["asset_id"])
        self.assertEqual(contract[0]["parameters"]["properties"]["mode"]["enum"], ["approved"])

    def test_retrieval_does_not_replace_citations_or_business_references(self):
        context = [{"kind": "policy", "content": "## 1. Asset Retention\nAsset retention requires review.",
                    "metadata": {"policy_clauses": [{"clause_id": "RET-01", "label": "Section 1: Asset Retention"}]}}]
        arguments = {"policy_sections_cited": ["RET-01"], "customer_reference": "customer-7"}
        response = {"output": [{"type": "function_call", "name": "review_asset", "call_id": "a1", "arguments": json.dumps(arguments)}]}
        tools = [{"name": "review_asset", "parameters": {"type": "object", "properties": {}}}]
        _, payload = handle_request(request({"policy_context": context, "tools": tools,
                                            "messages": [{"role": "user", "content": "Review asset retention."}]}),
                                    model_client=lambda *_: response)
        self.assertEqual(payload["result"]["parts"][0]["data"]["tool_calls"][0]["arguments"], arguments)

    def test_structured_plan_response_requires_typed_actions(self):
        response = {
            "output": [{
                "type": "function_call",
                "call_id": "plan-1",
                "name": STRUCTURED_PLAN_TOOL,
                "arguments": json.dumps({
                    "decision": "ALLOW",
                    "customer_message": "Your request is approved.",
                    "actions": [{"name": "perform_action", "arguments": {"request_id": "req-1"}}],
                }),
            }],
        }
        self.assertEqual(
            _structured_plan_response(response),
            ("Your request is approved.", [{"id": "plan-1-1", "name": "perform_action", "arguments": {"request_id": "req-1"}}]),
        )
        self.assertIsNone(_structured_plan_response({"output": []}))

    def test_provider_backed_turn_adjudicates_before_releasing_actions(self):
        tools = [{"type": "function", "function": {"name": "perform_action", "parameters": {"type": "object", "properties": {"request_id": {"type": "string"}}, "required": ["request_id"], "additionalProperties": False}}}]
        plan_response = {
            "output": [{
                "type": "function_call",
                "call_id": "plan-1",
                "name": STRUCTURED_PLAN_TOOL,
                "arguments": json.dumps({"actions": [{"name": "perform_action", "arguments": {"request_id": "req-1"}}]}),
            }],
        }
        executor_response = {
            "output": [{
                "type": "function_call",
                "call_id": "execute-1",
                "name": "perform_action",
                "arguments": '{"request_id":"req-1"}',
            }],
        }
        with patch.dict("os.environ", {"ENTIGRAM_SENTINEL_STRUCTURED_PLANNING": "1"}, clear=True), patch(
            "entigram.sentinel_agent.model_responses", side_effect=[plan_response, executor_response]
        ) as planned:
            _, payload = handle_request(
                request({"benchmark_context": [{"kind": "policy", "content": "POL-1 permits the action."}], "tools": tools, "messages": [{"role": "user", "content": "Please proceed."}]}),
            )
        data = payload["result"]["parts"][0]["data"]
        self.assertEqual(data["tool_calls"], [{"id": "execute-1", "name": "perform_action", "arguments": {"request_id": "req-1"}}])
        self.assertEqual(planned.call_args_list[0].args[1][0]["function"]["name"], STRUCTURED_PLAN_TOOL)
        self.assertEqual(planned.call_args_list[0].args[2], {"type": "function", "name": STRUCTURED_PLAN_TOOL})
        self.assertEqual(planned.call_count, 2)
        self.assertIn("Independent policy adjudication draft", planned.call_args_list[1].args[0][0]["content"])

    def test_agent_card_declares_pibench_bootstrap(self):
        card = agent_card("http://endpoint:9010/")
        self.assertEqual(card["name"], AGENT_NAME)
        self.assertEqual(card["url"], "http://endpoint:9010")
        self.assertIn(POLICY_BOOTSTRAP_EXTENSION, card["extensions"])
        self.assertIn(POLICY_BOOTSTRAP_EXTENSION, [e["uri"] for e in card["capabilities"]["extensions"]])

    def test_bootstrap_hydrates_context_without_model_call(self):
        sessions = {}
        status, response = handle_request(request({"bootstrap": True, "benchmark_context": [{"kind": "policy", "content": "Refunds require manager approval."}], "tools": [{"type": "function", "function": {"name": "record_decision", "parameters": {}}}]}), sessions=sessions, model_client=lambda *_: self.fail("bootstrap must not infer"))
        self.assertEqual(status, 200)
        bootstrap = response["result"]["parts"][0]["data"]
        context_id = bootstrap["context_id"]
        self.assertIn(context_id, sessions)
        self.assertEqual(bootstrap["hydration"]["policy_evidence_count"], 1)
        self.assertNotIn("content", bootstrap["hydration"])

    def test_turn_uses_cached_context_and_declared_tool_contract(self):
        sessions = {"ctx": {"benchmark_context": [{"kind": "policy", "content": "Policy ID: OPS-001. Escalate uncertain cases."}], "tools": [{"type": "function", "function": {"name": "record_decision", "parameters": {}}}]}}
        seen = {}
        def model(messages, tools):
            seen["prompt"], seen["tools"] = messages[0]["content"], tools
            return {"output": [{"type": "function_call", "call_id": "call-1", "name": "record_decision", "arguments": "{}"}]}
        status, response = handle_request(request({"context_id": "ctx", "messages": [{"role": "user", "content": "Please decide."}]}), sessions=sessions, model_client=model)
        self.assertEqual(status, 200)
        data = response["result"]["parts"][0]["data"]
        self.assertIn("Escalate uncertain cases", seen["prompt"])
        self.assertIn("native function calls", seen["prompt"])
        self.assertIn("Do not escalate merely because current state is unknown", seen["prompt"])
        self.assertIn("choose and implement that direct outcome rather than escalating", seen["prompt"])
        self.assertIn("never as a substitute for implementing the outcome", seen["prompt"])
        self.assertIn('"OPS-001"', seen["prompt"])
        self.assertEqual(seen["tools"][0]["function"]["name"], "record_decision")
        self.assertEqual(data["tool_calls"][0]["name"], "record_decision")
        self.assertEqual(data["decision_events"][0]["outcome"], "ALLOW")

    def test_turn_adds_extractively_retrieved_policy_evidence_to_planning_prompt(self):
        sessions = {
            "ctx": {
                "benchmark_context": [{
                    "kind": "policy",
                    "content": (
                        "## 1. Standard Requests\nA receipt is required.\n\n"
                        "## 2. Activated Devices\nActivated tablets are non-returnable."
                    ),
                }],
                "tools": [{"type": "function", "function": {"name": "record_decision", "parameters": {}}}],
            }
        }
        seen = {}

        def model(messages, _tools):
            seen["prompt"] = messages[0]["content"]
            return {"output": []}

        status, _ = handle_request(
            request({"context_id": "ctx", "messages": [{"role": "user", "content": "I need to return an activated tablet."}]}),
            sessions=sessions,
            model_client=model,
        )

        self.assertEqual(status, 200)
        self.assertIn("Relevant policy evidence for the current turn", seen["prompt"])
        self.assertIn("Section 2: Activated Devices", seen["prompt"])
        self.assertIn("Binding policy statements relevant to this turn", seen["prompt"])

    def test_admission_rejects_invalid_arguments_before_execution(self):
        tools = [{"type": "function", "function": {"name": "hold_refund", "parameters": {"type": "object", "properties": {"refund_id": {"type": "string"}}, "required": ["refund_id"], "additionalProperties": False}}}]
        response = {"output": [{"type": "function_call", "call_id": "call-1", "name": "hold_refund", "arguments": '{"refund_id":3}'}]}
        status, payload = handle_request(request({"benchmark_context": [], "tools": tools, "messages": []}), model_client=lambda *_: response)
        data = payload["result"]["parts"][0]["data"]
        self.assertEqual(status, 200)
        self.assertNotIn("tool_calls", data)
        self.assertEqual(data["decision_events"][0]["outcome"], "DENY")
        self.assertEqual(data["decision_events"][0]["reason_codes"], ["argument_type_mismatch"])

    def test_structured_policy_reference_is_exposed_and_admitted_exactly(self):
        tools = [{"type": "function", "function": {"name": "record_decision", "parameters": {"type": "object", "properties": {"policy_sections_cited": {"type": "array", "items": {"type": "string"}}}, "required": ["policy_sections_cited"]}}}]
        response = {"output": [{"type": "function_call", "call_id": "call-1", "name": "record_decision", "arguments": '{"policy_sections_cited":["RET-GEN-01"]}'}]}
        seen = {}

        def model(messages, seen_tools):
            seen["prompt"] = messages[0]["content"]
            seen["tools"] = seen_tools
            return response

        _, payload = handle_request(
            request({
                "benchmark_context": [{"kind": "policy", "content": "## 1. General Returns", "metadata": {"policy_clauses": [{"clause_id": "RET-GEN-01", "label": "Section 1"}]}}],
                "tools": tools,
                "messages": [],
            }),
            model_client=model,
        )
        data = payload["result"]["parts"][0]["data"]
        self.assertIn('"RET-GEN-01"', seen["prompt"])
        self.assertEqual(
            seen["tools"][0]["function"]["parameters"]["properties"]["policy_sections_cited"]["items"]["enum"],
            ["RET-GEN-01"],
        )
        self.assertEqual(data["tool_calls"][0]["arguments"]["policy_sections_cited"], ["RET-GEN-01"])
        self.assertEqual(data["decision_events"][0]["outcome"], "ALLOW")

    def test_admission_preserves_schema_valid_action_order(self):
        tools = [{"type": "function", "function": {"name": name, "parameters": {}}} for name in ("open_case", "escalate")]
        response = {"output": [{"type": "function_call", "call_id": "call-1", "name": "open_case", "arguments": "{}"}, {"type": "function_call", "call_id": "call-2", "name": "escalate", "arguments": "{}"}]}
        _, payload = handle_request(request({"benchmark_context": [], "tools": tools, "messages": []}), model_client=lambda *_: response)
        data = payload["result"]["parts"][0]["data"]
        self.assertEqual([call["name"] for call in data["tool_calls"]], ["open_case", "escalate"])
        self.assertEqual([event["outcome"] for event in data["decision_events"]], ["ALLOW", "ALLOW"])

    def test_undeclared_tool_is_not_returned(self):
        def model(_messages, _tools):
            return {"output": [{"type": "function_call", "call_id": "call-2", "name": "process_refund", "arguments": "{}"}]}
        status, response = handle_request(request({"benchmark_context": [], "tools": [], "messages": []}), model_client=model)
        self.assertEqual(status, 200)
        data = response["result"]["parts"][0]["data"]
        self.assertNotIn("tool_calls", data)
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

    def test_responses_tools_normalizes_flattened_parameter_contract(self):
        tools = _responses_tools([{"name": "record", "parameters": {"policy_sections_cited": {"type": "array", "items": {"type": "string", "enum": ["RET-01"]}, "required": True}}}])
        self.assertEqual(tools[0]["parameters"], {"type": "object", "properties": {"policy_sections_cited": {"type": "array", "items": {"type": "string", "enum": ["RET-01"]}}}, "required": ["policy_sections_cited"]})

    def test_openai_responses_passes_required_native_tool_choice(self):
        seen = {}

        class Response:
            def __enter__(self): return self
            def __exit__(self, *_args): return False
            def read(self): return b'{"output": []}'

        def urlopen(request, timeout):
            seen["payload"] = json.loads(request.data)
            return Response()

        with patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}, clear=True), patch("urllib.request.urlopen", urlopen):
            openai_responses([], [], {"type": "function", "name": "record_decision"})

        self.assertEqual(seen["payload"]["tool_choice"], {"type": "function", "name": "record_decision"})

    def test_openai_responses_uses_configurable_output_budget(self):
        seen = {}

        class Response:
            def __enter__(self): return self
            def __exit__(self, *_): return False
            def read(self): return b'{"output": []}'

        def urlopen(request, timeout):
            seen["payload"] = json.loads(request.data)
            return Response()

        with patch.dict("os.environ", {"OPENAI_API_KEY": "test-key", "ENTIGRAM_SENTINEL_MAX_OUTPUT_TOKENS": "2048"}, clear=True), patch("urllib.request.urlopen", urlopen):
            openai_responses([], [])

        self.assertEqual(seen["payload"]["max_output_tokens"], 2048)

    def test_router_prefers_openai_when_its_key_is_available(self):
        with patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}, clear=True), patch("entigram.sentinel_agent.openai_responses", return_value={"output": []}) as openai, patch("entigram.sentinel_agent.cloudflare_responses") as cloudflare:
            self.assertEqual(model_responses([], []), {"output": []})
        openai.assert_called_once_with([], [], None)
        cloudflare.assert_not_called()
