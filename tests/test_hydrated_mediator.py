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
    def test_completion_lifecycle_tracks_receipt_order_not_tool_name_presence(self):
        mediator = HydratedPolicyMediator(
            [{"kind": "task", "content": "Record your final decision by calling the close_workflow tool."}],
            [{"name": name, "parameters": {"type": "object"}}
             for name in ("publish_artifact", "close_workflow")],
        )
        self.assertEqual(mediator.finalization_tool, "close_workflow")
        self.assertFalse(mediator.finalization_pending())
        mediator.state.record_tool_result("work-1", "publish_artifact", {}, {"ok": True})
        self.assertTrue(mediator.finalization_pending())
        mediator.state.record_tool_result("close-1", "close_workflow", {}, {"ok": True})
        self.assertFalse(mediator.finalization_pending())
        self.assertIn("Finalization has already succeeded", mediator.completion_guidance())
        self.assertIn("conversational follow-up", mediator.completion_guidance())
        self.assertEqual(len(mediator.tools_for_planning()), 2)
        mediator.state.record_tool_result("work-2", "publish_artifact", {"revision": 2}, {"ok": True})
        self.assertTrue(mediator.finalization_pending())
        self.assertIn("remains outstanding", mediator.completion_guidance())
        mediator.state.record_tool_result("close-2", "close_workflow", {}, {"ok": False}, status="failed")
        self.assertTrue(mediator.finalization_pending())
        mediator.state.record_tool_result("close-3", "close_workflow", {}, {"ok": True})
        self.assertFalse(mediator.finalization_pending())

    def test_failed_work_can_be_finalized_but_failed_finalizer_cannot_complete_it(self):
        mediator = HydratedPolicyMediator(
            [{"kind": "task", "content": "Record your final decision by calling the close_workflow tool."}],
            [{"name": name, "parameters": {"type": "object"}}
             for name in ("publish_artifact", "close_workflow")],
        )
        mediator.state.record_tool_result("work", "publish_artifact", {}, {"ok": False}, status="failed")
        self.assertTrue(mediator.finalization_pending())
        mediator.state.record_tool_result("failed-close", "close_workflow", {}, {"ok": False}, status="failed")
        self.assertTrue(mediator.finalization_pending())
        mediator.state.record_tool_result("close", "close_workflow", {"outcome": "failed"}, {"ok": True})
        self.assertFalse(mediator.finalization_pending())
        self.assertIn("observed results", mediator.completion_guidance())

    def test_business_disposition_is_not_execution_failure(self):
        for status in ("denied", "cancelled", "canceled"):
            with self.subTest(status=status):
                mediator = HydratedPolicyMediator([], [{"name": "close_case", "parameters": {"type": "object"}}])
                mediator.update_state([
                    {"role": "assistant", "tool_calls": [{"id": "a1", "function": {"name": "close_case", "arguments": "{}"}}]},
                    {"role": "tool", "tool_call_id": "a1", "content": json.dumps({"status": status})},
                ])
                self.assertEqual(mediator.state.tool_results[0].status, "success")
                self.assertIn("close_case", mediator.state.executed_tools)
        for error_key in ("isError", "error"):
            mediator = HydratedPolicyMediator([], [{"name": "close_case", "parameters": {"type": "object"}}])
            mediator.update_state([
                {"role": "assistant", "tool_calls": [{"id": "a1", "function": {"name": "close_case", "arguments": "{}"}}]},
                {"role": "tool", "tool_call_id": "a1", error_key: True, "content": '{"status":"denied"}'},
            ])
            self.assertEqual(mediator.state.tool_results[0].status, "failed")

    def test_flattened_contract_is_enforced_not_only_shown_to_model(self):
        from entigram.governance.action_admission import admit_tool_proposals
        from entigram.sentinel_agent import _responses_parameters
        parameters = {"quantity": {"type": "integer", "required": True, "minimum": 1},
                      "note": {"type": "string", "required": False}}
        tools = [{"name": "submit", "parameters": parameters}]
        mediator = HydratedPolicyMediator([], tools)
        self.assertEqual(mediator.tools[0]["parameters"], _responses_parameters(parameters))
        for args, valid in [({}, False), ({"quantity": 0}, False), ({"quantity": 1}, True)]:
            proposal = {"name": "submit", "arguments": args}
            self.assertEqual(bool(mediator.admit_proposals([proposal])[0]), valid)
            self.assertEqual(bool(admit_tool_proposals([proposal], tools)[0]), valid)
        self.assertTrue(parameters["quantity"]["required"])

    def test_nested_schema_constraints_match_across_admission_paths(self):
        from entigram.governance.action_admission import admit_tool_proposals
        schema = {"type": "object", "properties": {
            "rows": {"type": "array", "minItems": 1, "items": {
                "type": "object", "properties": {
                    "quantity": {"type": "integer", "minimum": 1},
                    "mode": {"enum": ["approved"]},
                }, "required": ["quantity", "mode"], "additionalProperties": False}},
            "note": {"type": ["string", "null"]},
        }, "required": ["rows"], "additionalProperties": False}
        tools = [{"name": "submit", "parameters": schema}]
        mediator = HydratedPolicyMediator([], tools)
        for arguments, valid in [
            ({"rows": [{"quantity": 1, "mode": "approved"}], "note": None}, True),
            ({"rows": []}, False),
            ({"rows": ["wrong"]}, False),
            ({"rows": [{"quantity": 0, "mode": "approved"}]}, False),
            ({"rows": [{"quantity": 1, "mode": "unapproved"}]}, False),
            ({"rows": [{"quantity": 1}]}, False),
            ({"rows": [{"quantity": True, "mode": "approved"}]}, False),
            ({"rows": [{"quantity": 1, "mode": "approved", "invented": 2}]}, False),
        ]:
            with self.subTest(arguments=arguments):
                proposal = {"name": "submit", "arguments": arguments}
                self.assertEqual(bool(mediator.admit_proposals([proposal])[0]), valid)
                self.assertEqual(bool(admit_tool_proposals([proposal], tools)[0]), valid)

    def test_schema_validation_local_refs_and_fail_closed_contracts(self):
        from entigram.governance.action_admission import tool_argument_errors
        from unittest.mock import patch
        schema = {"$defs": {"positive": {"type": "integer", "minimum": 1}},
                  "properties": {"count": {"$ref": "#/$defs/positive"}}}
        self.assertEqual(tool_argument_errors({"count": 2}, schema), [])
        self.assertTrue(tool_argument_errors({"count": 0}, schema))
        self.assertEqual(tool_argument_errors({}, {"type": "nonsense"})[0]["code"], "invalid_tool_schema")
        self.assertEqual(tool_argument_errors({}, {"$schema": "https://invalid.example/schema"})[0]["code"], "unsupported_schema_dialect")
        with patch("urllib.request.urlopen", side_effect=AssertionError("Network must not be used")):
            for ref in ("https://invalid.example/schema", "file:///private/tmp/missing-schema"):
                self.assertEqual(tool_argument_errors({}, {"$ref": ref})[0]["code"], "unresolved_schema_reference")

    def test_business_references_are_not_policy_citations(self):
        from entigram.governance.action_admission import admit_tool_proposals
        tools = [{"name": "update_case", "parameters": {"type": "object", "properties": {
            "customer_reference": {"type": "string"},
            "document_section": {"type": "string"},
            "policy_reference": {"type": "string"},
        }}}]
        policy = [{"kind": "policy", "content": "Follow ORG-GOV-01."}]
        proposal = {"name": "update_case", "arguments": {
            "customer_reference": "customer-7", "document_section": "appendix", "policy_reference": "ORG-GOV-01"}}
        mediator = HydratedPolicyMediator(policy, tools)
        self.assertEqual(mediator.admit_proposals([proposal])[0], [proposal])
        self.assertEqual(admit_tool_proposals([proposal], tools, permitted_policy_references=["ORG-GOV-01"])[0], [proposal])
        proposal["arguments"]["policy_reference"] = "invented"
        self.assertEqual(mediator.admit_proposals([proposal])[0], [])
        self.assertEqual(admit_tool_proposals([proposal], tools, permitted_policy_references=["ORG-GOV-01"])[0], [])

    def test_citation_semantics_can_be_explicitly_declared(self):
        from entigram.governance.action_admission import is_policy_reference_field
        self.assertTrue(is_policy_reference_field("source", {"x-entigram-policy-reference": True}))
        self.assertFalse(is_policy_reference_field("policy_id", {"x-entigram-policy-reference": False}))
        self.assertTrue(is_policy_reference_field("policyReference"))
        self.assertFalse(is_policy_reference_field("cross_section"))

    def test_planning_citation_constraint_preserves_business_fields_and_enum(self):
        from entigram.sentinel_agent import _constrain_planning_citations
        tools = [{"name": "update_case", "parameters": {"type": "object", "properties": {
            "customer_reference": {"type": "string"},
            "policy_reference": {"type": "string", "enum": ["ORG-GOV-01"]},
        }}}]
        constrained = _constrain_planning_citations(tools, ["ORG-GOV-01", "ORG-GOV-02"])
        props = constrained[0]["parameters"]["properties"]
        self.assertNotIn("enum", props["customer_reference"])
        self.assertEqual(props["policy_reference"]["enum"], ["ORG-GOV-01"])
        self.assertEqual(tools[0]["parameters"]["properties"]["policy_reference"]["enum"], ["ORG-GOV-01"])

    def test_failed_verification_does_not_satisfy_prerequisite(self):
        for output in ({"success": False}, {"ok": False}, {"error": "failed"}, {"status": "failed"}, {"isError": True}):
            with self.subTest(output=output):
                mediator = HydratedPolicyMediator(self.policy_context, self.tools)
                mediator.update_state([
                    {"role": "assistant", "tool_calls": [{"id": "v1", "function": {"name": "verify_customer", "arguments": '{"customer_id":"c1"}'}}]},
                    {"role": "tool", "tool_call_id": "v1", "content": json.dumps(output)},
                ])
                self.assertFalse(mediator.evaluate_tool_readiness("issue_refund")["enabled"])
                self.assertEqual(mediator.state.tool_results[0].status, "failed")

    def test_admission_rejects_value_outside_declared_enum(self):
        mediator = HydratedPolicyMediator([], [{"name": "release_asset", "parameters": {
            "type": "object", "properties": {"mode": {"type": "string", "enum": ["approved"]}}, "required": ["mode"]}}])
        admitted, events = mediator.admit_proposals([{"id": "a1", "name": "release_asset", "arguments": {"mode": "forbidden"}}])
        self.assertEqual(admitted, [])
        self.assertIn("argument_enum_mismatch", events[0]["reason_codes"])

    def test_admission_prevents_replaying_a_completed_effect_by_business_identity(self):
        mediator = HydratedPolicyMediator([], [{"name": "deny_case", "parameters": {
            "type": "object",
            "properties": {"case_id": {"type": "string"}, "customer_message": {"type": "string"}},
            "required": ["case_id"],
        }}])
        mediator.state.record_tool_result("deny-1", "deny_case", {
            "case_id": "case-17", "customer_message": "First response",
        }, {"ok": True})

        admitted, events = mediator.admit_proposals([{
            "id": "deny-2", "name": "deny_case", "arguments": {
                "case_id": "case-17", "customer_message": "Different wording does not create a new effect",
            },
        }])
        self.assertEqual(admitted, [])
        self.assertIn("duplicate_completed_action", events[0]["reason_codes"])

        admitted, _ = mediator.admit_proposals([{
            "id": "deny-3", "name": "deny_case", "arguments": {"case_id": "case-18"},
        }])
        self.assertEqual(len(admitted), 1)

    def test_finalization_identity_ignores_incidental_citation_changes(self):
        mediator = HydratedPolicyMediator(
            [{"kind": "task", "content": "Record your final decision by calling the record_decision tool."}],
            [{"name": "record_decision", "description": "Record final decision.", "parameters": {
                "type": "object",
                "properties": {
                    "order_id": {"type": "string"},
                    "decision": {"type": "string", "enum": ["ALLOW", "DENY"]},
                    "policy_sections_cited": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["order_id", "decision"],
            }}],
        )
        mediator.state.record_tool_result("final-1", "record_decision", {
            "order_id": "order-9", "decision": "DENY", "policy_sections_cited": ["POL-1"],
        }, {"ok": True})
        admitted, events = mediator.admit_proposals([{
            "id": "final-2", "name": "record_decision", "arguments": {
                "order_id": "order-9", "decision": "DENY", "policy_sections_cited": ["POL-2"],
            },
        }])
        self.assertEqual(admitted, [])
        self.assertIn("duplicate_completed_action", events[0]["reason_codes"])

        corrected, correction_events = mediator.admit_proposals([{
            "id": "final-3", "name": "record_decision", "arguments": {
                "order_id": "order-9", "decision": "ALLOW", "policy_sections_cited": ["POL-3"],
            },
        }])
        self.assertEqual(corrected, [])
        self.assertIn("duplicate_completed_action", correction_events[0]["reason_codes"])

        mediator.tools[0]["parameters"]["x-entigram-allow-correction"] = True
        corrected, _ = mediator.admit_proposals([{
            "id": "final-4", "name": "record_decision", "arguments": {
                "order_id": "order-9", "decision": "ALLOW", "policy_sections_cited": ["POL-3"],
            },
        }])
        self.assertEqual(len(corrected), 1)

    def test_repeatable_contract_retains_authority_for_repeated_effects(self):
        mediator = HydratedPolicyMediator([], [{"name": "append_audit_note", "parameters": {
            "type": "object",
            "x-entigram-repeatable": True,
            "properties": {"case_id": {"type": "string"}},
            "required": ["case_id"],
        }}])
        mediator.state.record_tool_result("note-1", "append_audit_note", {"case_id": "case-17"}, {"ok": True})
        admitted, _ = mediator.admit_proposals([{
            "id": "note-2", "name": "append_audit_note", "arguments": {"case_id": "case-17"},
        }])
        self.assertEqual(len(admitted), 1)

    def test_failed_receipt_does_not_settle_an_effect(self):
        mediator = HydratedPolicyMediator([], [{"name": "deny_case", "parameters": {
            "type": "object",
            "properties": {"case_id": {"type": "string"}},
            "required": ["case_id"],
        }}])
        mediator.state.record_tool_result("deny-failed", "deny_case", {"case_id": "case-17"}, {"ok": False}, status="failed")
        admitted, _ = mediator.admit_proposals([{
            "id": "deny-retry", "name": "deny_case", "arguments": {"case_id": "case-17"},
        }])
        self.assertEqual(len(admitted), 1)

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

    def test_accepts_explicit_section_citations_from_policy_context(self):
        mediator = HydratedPolicyMediator(
            [
                {
                    "kind": "policy",
                    "content": "Document ID: BM-SOP-RET-2025-04\n\n## 7. Account review\n\nSee Section 7 for account review.",
                }
            ],
            self.tools,
        )
        self.assertEqual(
            mediator.permitted_policy_references,
            ["BM-SOP-RET-2025-04", "Section 7"],
        )

        errors, _, _ = mediator._proposal_denial_errors(
            {
                "id": "section-citation",
                "name": "issue_refund",
                "arguments": {
                    "refund_id": "ref-500",
                    "policy_reference": "BM-SOP-RET-2025-04 §7: Account review",
                },
            }
        )
        self.assertNotIn("unverified_policy_reference", [error["code"] for error in errors])

        invalid_errors, _, _ = mediator._proposal_denial_errors(
            {
                "id": "unknown-section",
                "name": "issue_refund",
                "arguments": {
                    "refund_id": "ref-500",
                    "policy_reference": "Sections 7 and 99",
                },
            }
        )
        self.assertIn("unverified_policy_reference", [error["code"] for error in invalid_errors])

        heading_errors, _, _ = mediator._proposal_denial_errors(
            {
                "id": "heading-citation",
                "name": "issue_refund",
                "arguments": {
                    "refund_id": "ref-500",
                    "policy_reference": "7. Account review",
                },
            }
        )
        self.assertNotIn("unverified_policy_reference", [error["code"] for error in heading_errors])

        compact_errors, _, _ = mediator._proposal_denial_errors(
            {
                "id": "compact-citation",
                "name": "issue_refund",
                "arguments": {
                    "refund_id": "ref-500",
                    "policy_reference": "7",
                },
            }
        )
        self.assertNotIn("unverified_policy_reference", [error["code"] for error in compact_errors])

    def test_extracts_sections_from_policy_context_beyond_citation_argument_limit(self):
        mediator = HydratedPolicyMediator(
            [
                {
                    "kind": "policy",
                    "content": ("# Policy\n" + ("x" * 5000) + "\n## 12. Retention\n"),
                }
            ],
            self.tools,
        )
        self.assertIn("Section 12", mediator.permitted_policy_references)

    def test_hydrates_explicit_structured_policy_reference_values(self):
        mediator = HydratedPolicyMediator(
            [
                {
                    "kind": "policy",
                    "content": "## 1. General Returns Policy\nReturns are allowed within 30 days.",
                    "metadata": {
                        "policy_clauses": [
                            {
                                "clause_id": "RET-GEN-01",
                                "label": "Section 1: General Returns Policy",
                                "template": "RET-{area}-{number}",
                            }
                        ]
                    },
                }
            ],
            [],
        )

        self.assertIn("RET-GEN-01", mediator.permitted_policy_references)
        self.assertNotIn("RET-{area}-{number}", mediator.permitted_policy_references)

    def test_structured_policy_references_take_precedence_over_inferred_headings(self):
        mediator = HydratedPolicyMediator(
            [{
                "kind": "policy",
                "content": "## 1. Standard Returns\nA receipt is required.",
                "metadata": {"policy_references": [{"id": "RET-GEN-01"}]},
            }],
            [{"name": "record", "parameters": {"type": "object", "properties": {
                "policy_sections_cited": {"type": "array", "items": {"type": "string"}},
            }}}],
        )
        self.assertIn('"RET-GEN-01"', mediator.citation_guidance())
        self.assertNotIn('"Section 1"', mediator.citation_guidance())
        rejected, events = mediator.admit_proposals([{
            "name": "record", "arguments": {"policy_sections_cited": ["Section 1"]},
        }])
        self.assertEqual(rejected, [])
        self.assertIn("unverified_policy_reference", events[0]["reason_codes"])
        admitted, _ = mediator.admit_proposals([{
            "name": "record", "arguments": {"policy_sections_cited": ["RET-GEN-01"]},
        }])
        self.assertEqual(len(admitted), 1)

    def test_extracts_only_explicit_policy_hierarchy_statements(self):
        mediator = HydratedPolicyMediator(
            [{"kind": "policy", "content": "## 1. General\nSpecific category terms take precedence over general rules.\n## 2. Loyalty\nLoyalty benefits do not override product restrictions."}],
            [],
        )
        guidance = mediator.hierarchy_guidance()
        self.assertIn("Section 1: General", guidance)
        self.assertIn("take precedence", guidance)
        self.assertIn("do not override", guidance)

    def test_retrieves_relevant_supplied_policy_sections_without_creating_rules(self):
        mediator = HydratedPolicyMediator(
            [{
                "kind": "policy",
                "content": (
                    "## 1. General Returns\nA receipt is required for ordinary returns.\n\n"
                    "## 2. Activated Devices\nActivated tablets are non-returnable."
                ),
            }],
            [],
        )

        guidance = mediator.relevant_policy_guidance([
            {"role": "user", "content": "I need to return my activated tablet."},
        ])

        self.assertIn("Section 2: Activated Devices", guidance)
        self.assertIn("Activated tablets are non-returnable.", guidance)
        self.assertNotIn("Section 1: General Returns", guidance)
        self.assertNotIn("DENY", guidance)

    def test_retrieval_does_not_treat_prior_assistant_prose_as_policy_evidence_query(self):
        mediator = HydratedPolicyMediator(
            [{
                "kind": "policy",
                "content": (
                    "## 1. Standard Requests\nA receipt is required.\n\n"
                    "## 2. Activated Devices\nActivated tablets are non-returnable."
                ),
            }],
            [],
        )

        guidance = mediator.relevant_policy_guidance([
            {"role": "user", "content": "I have my receipt."},
            {"role": "assistant", "content": "This is definitely an activated tablet case."},
        ])

        self.assertIn("Section 1: Standard Requests", guidance)
        self.assertNotIn("Section 2: Activated Devices", guidance)

    def test_retrieval_follows_a_bounded_source_authored_section_reference(self):
        mediator = HydratedPolicyMediator(
            [{
                "kind": "policy",
                "content": (
                    "## 1. General Returns\nReturns are allowed within 30 days. "
                    "See Section 5 for loyalty accommodations.\n\n"
                    "## 5. Loyalty Accommodations\nSilver tier does not extend the return window.\n\n"
                    "## 9. Unrelated Topic\nA different operational procedure."
                ),
            }],
            [],
        )

        guidance = mediator.relevant_policy_guidance([
            {"role": "user", "content": "Can I return this ordinary item after 45 days?"},
        ])

        self.assertIn("Section 1: General Returns", guidance)
        self.assertIn("Section 5: Loyalty Accommodations", guidance)
        self.assertIn("Silver tier does not extend the return window.", guidance)
        self.assertNotIn("Section 9: Unrelated Topic", guidance)

    def test_highlights_relevant_binding_policy_sentences_without_inference(self):
        mediator = HydratedPolicyMediator(
            [{
                "kind": "policy",
                "content": (
                    "## 1. Standard Requests\nA receipt is required.\n\n"
                    "## 2. Account Holds\nWhen an account is on hold, representatives must not process refunds."
                ),
            }],
            [],
        )

        guidance = mediator.directive_guidance([
            {"role": "user", "content": "My account has a hold; can you issue a refund?"},
        ])

        self.assertIn("Section 2: Account Holds", guidance)
        self.assertIn("must not process refunds", guidance)
        self.assertNotIn("A receipt is required", guidance)

    def test_surfaces_only_explicit_ambiguity_escalation_clauses(self):
        mediator = HydratedPolicyMediator(
            [{"kind": "policy", "content": (
                "## 1. Returns\nRefunds are normally permitted.\n\n"
                "## 2. Defective Electronics\nWhen policy provisions conflict and the damage claim is ambiguous, escalate to a manager."
            )}],
            [],
        )
        guidance = mediator.ambiguity_guidance([{"role": "user", "content": "My defective electronics return has conflicting terms."}])
        self.assertIn("Section 2: Defective Electronics", guidance)
        self.assertIn("escalate to a manager", guidance)
        self.assertNotIn("Refunds are normally permitted", guidance)

    def test_matches_provider_clause_ids_to_retrieved_section_labels(self):
        mediator = HydratedPolicyMediator(
            [{"kind": "policy", "content": "## 2. Account Holds\nA hold blocks refunds.", "metadata": {"policy_clauses": [{"clause_id": "HOLD-02", "section": "Section 2: Account Holds"}]}}],
            [],
        )
        self.assertEqual(mediator.relevant_structured_references([{"role": "user", "content": "My account has a hold."}]), ["HOLD-02"])

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
        self.assertNotIn("tool_calls", data1)
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
        self.assertEqual(observed["tool_names"], ["process_action", "record_decision"])
        self.assertIn("Continue any remaining declared inspection or operational actions", observed["prompt"])
        self.assertIn("must agree with all observed operations", observed["prompt"])
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

    def test_declared_tool_contract_fallback_completes_pibench_style_turn(self):
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "process_refund",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "record_decision",
                    "description": "Record the final decision. This is the canonical decision signal for the scenario.",
                    "parameters": {
                        "type": "object",
                        "properties": {"decision": {"type": "string", "enum": ["ALLOW", "DENY", "ESCALATE"]}},
                        "required": ["decision"],
                    },
                },
            },
        ]
        sessions = {}
        _, bootstrap = handle_request(
            make_request({"bootstrap": True, "benchmark_context": [{"kind": "policy", "content": "POL-1"}], "tools": tools}),
            sessions=sessions,
        )
        context_id = bootstrap["result"]["parts"][0]["data"]["context_id"]
        contract = bootstrap["result"]["parts"][0]["data"]["hydration"]["completion_contract"]
        self.assertEqual(contract["finalization_tool"], "record_decision")
        self.assertEqual(contract["source"], "tool_contract")

        calls_seen = {}
        def record_model(_messages, active_tools):
            calls_seen["tools"] = [tool["function"]["name"] for tool in active_tools]
            return {"output": [{"type": "function_call", "call_id": "record-1", "name": "record_decision", "arguments": '{"decision":"DENY"}'}]}

        # This is the A2A/OpenAI form PiBench sends after an executor result.
        messages = [
            {"role": "user", "content": "Handle the return."},
            {"role": "assistant", "tool_calls": [{"id": "refund-1", "function": {"name": "process_refund", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "refund-1", "content": '{"status":"processed"}'},
        ]
        _, response = handle_request(
            make_request({"context_id": context_id, "messages": messages}), sessions=sessions, model_client=record_model
        )
        data = response["result"]["parts"][0]["data"]
        self.assertEqual(calls_seen["tools"], ["process_refund", "record_decision"])
        self.assertEqual(data["tool_calls"][0]["name"], "record_decision")
        self.assertTrue(data["hydration"]["completion_contract"]["finalization_pending"])

    def test_initial_inspection_is_required_when_contract_declares_read_only_evidence_tool(self):
        mediator = HydratedPolicyMediator(
            [{"kind": "policy", "content": "POL-1"}],
            [
                {"name": "lookup_case", "description": "Look up current case state.", "parameters": {}},
                {"name": "issue_refund", "description": "Issue a refund.", "parameters": {}},
            ],
        )
        self.assertEqual(mediator.inspection_tool_names(), ["lookup_case"])
        self.assertTrue(mediator.initial_inspection_pending())
        self.assertEqual(mediator.required_tool_choice(), "required")
        mediator.state.record_tool_result("write-1", "issue_refund", {}, {"status": "complete"})
        self.assertTrue(mediator.initial_inspection_pending())
        mediator.state.record_tool_result("lookup-1", "lookup_case", {}, {"status": "found"})
        self.assertFalse(mediator.initial_inspection_pending())


if __name__ == "__main__":
    unittest.main()
