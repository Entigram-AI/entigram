"""Session-local hydrated policy mediator.

Provides product-general policy mediation for agent sessions by:
1. Accepting supplied bootstrap policy context and declared tool contracts.
2. Building an evidence-linked policy model containing explicit rules only.
3. Tracking tool execution state, output history, and provenance.
4. Identifying enabled tools based on satisfied explicit prerequisites.
5. Emitting structured pre-dispatch denials before side-effect execution.
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from .action_admission import (
    decision_event,
    normalize_tool_contract,
    policy_reference_ids,
)

POLICY_REFERENCE_FIELD = re.compile(r"(?:policy|citation|section|reference)", re.IGNORECASE)
PREREQUISITE_PATTERN = re.compile(
    r"(?:prerequisite|requires|must execute|after|following|depends on)\s*:?\s*([a-zA-Z0-9_.-]+)",
    re.IGNORECASE,
)
FINALIZATION_PATTERN = re.compile(
    r"\b(?:record|log|finali[sz]e)\s+(?:your\s+)?final\s+(?:decision|outcome)\b"
    r".*?\b(?:by\s+)?calling\s+(?:the\s+)?(?P<tool>[a-zA-Z0-9_.-]+)\s+tool\b",
    re.IGNORECASE | re.DOTALL,
)
TOOL_FINALIZATION_PATTERN = re.compile(
    r"\bcanonical\s+(?:final\s+)?(?:decision|outcome)\s+signal\b"
    r"|\b(?:record|log|finali[sz]e)\s+(?:the\s+)?final\s+(?:decision|outcome)\b",
    re.IGNORECASE,
)


class PolicyEvidence:
    """Represents a unit of evidence from bootstrap policy context."""

    def __init__(self, evidence_id: str, kind: str, content: str):
        self.id = evidence_id
        self.kind = kind
        self.content = content
        self.digest = hashlib.sha256(content.encode("utf-8")).hexdigest()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "content": self.content,
            "digest": self.digest,
        }


class ExplicitRule:
    """An explicit policy rule or tool prerequisite linked to policy evidence."""

    def __init__(
        self,
        rule_id: str,
        rule_type: str,  # "prerequisite", "prohibition", "citation_required", "schema"
        description: str,
        target_tool: Optional[str] = None,
        prerequisites: Optional[List[str]] = None,
        evidence_ids: Optional[List[str]] = None,
    ):
        self.rule_id = rule_id
        self.rule_type = rule_type
        self.description = description
        self.target_tool = target_tool
        self.prerequisites = list(prerequisites or [])
        self.evidence_ids = list(evidence_ids or [])

    def to_dict(self) -> Dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "rule_type": self.rule_type,
            "description": self.description,
            "target_tool": self.target_tool,
            "prerequisites": self.prerequisites,
            "evidence_ids": self.evidence_ids,
        }


class ToolResultProvenance:
    """Record of an executed tool call and its output state."""

    def __init__(
        self,
        call_id: str,
        tool_name: str,
        arguments: Dict[str, Any],
        output: Any,
        status: str = "success",
        step_index: int = 0,
    ):
        self.call_id = call_id
        self.tool_name = tool_name
        self.arguments = arguments
        self.output = output
        self.status = status
        self.step_index = step_index

    def to_dict(self) -> Dict[str, Any]:
        return {
            "call_id": self.call_id,
            "tool_name": self.tool_name,
            "arguments": self.arguments,
            "output": self.output,
            "status": self.status,
            "step_index": self.step_index,
        }


class SessionState:
    """Tracks session execution history, completed tools, and outputs."""

    def __init__(self):
        self.executed_tools: Set[str] = set()
        self.tool_results: List[ToolResultProvenance] = []
        self.recorded_call_ids: Set[str] = set()
        self.observed_facts: Dict[str, Any] = {}
        self.step_counter: int = 0

    def record_tool_result(
        self,
        call_id: str,
        tool_name: str,
        arguments: Dict[str, Any],
        output: Any,
        status: str = "success",
    ) -> ToolResultProvenance:
        # A2A callers normally resend the complete conversation on every turn.
        # Only count each tool result once so policy state is derived from the
        # conversation, rather than from how often the caller retransmits it.
        if call_id in self.recorded_call_ids:
            return next(record for record in self.tool_results if record.call_id == call_id)
        self.step_counter += 1
        record = ToolResultProvenance(
            call_id=call_id,
            tool_name=tool_name,
            arguments=arguments,
            output=output,
            status=status,
            step_index=self.step_counter,
        )
        self.tool_results.append(record)
        self.recorded_call_ids.add(call_id)
        if status == "success":
            self.executed_tools.add(tool_name)
            self.observed_facts[f"tool_result:{tool_name}"] = output
        return record


class HydratedPolicyMediator:
    """Session-local policy mediator for hydrated governance."""

    def __init__(
        self,
        policy_context: Iterable[Dict[str, Any]],
        tools: Iterable[Dict[str, Any]],
    ):
        self.policy_context = [item for item in policy_context if isinstance(item, dict)]
        self.tools = normalize_tool_contract(tools)
        self.evidence_model: List[PolicyEvidence] = []
        self.explicit_rules: List[ExplicitRule] = []
        self.permitted_policy_references: List[str] = []
        self.finalization_tool: Optional[str] = None
        self.finalization_evidence_ids: List[str] = []
        self.finalization_contract_source: Optional[str] = None
        self.state = SessionState()

        self._hydrate()

    def telemetry(self) -> Dict[str, Any]:
        """Return privacy-safe diagnostics for the hydrated session.

        This intentionally exposes neither supplied policy text nor tool output.
        The fingerprint lets an operator correlate equal bootstrap contexts in
        local logs without making policy material recoverable from the response.
        """
        fingerprint_input = [
            {"id": evidence.id, "kind": evidence.kind, "digest": evidence.digest}
            for evidence in self.evidence_model
        ]
        policy_fingerprint = hashlib.sha256(
            json.dumps(fingerprint_input, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        enabled_tools = self.get_enabled_tools()
        return {
            "policy_fingerprint": policy_fingerprint,
            "policy_evidence_count": len(self.evidence_model),
            "explicit_rule_count": len(self.explicit_rules),
            "permitted_policy_reference_count": len(self.permitted_policy_references),
            "declared_tool_count": len(self.tools),
            "enabled_tool_count": len(enabled_tools),
            "state_transition_count": len(self.state.tool_results),
            "completion_contract": {
                "finalization_required": self.finalization_tool is not None,
                "finalization_tool": self.finalization_tool,
                "source": self.finalization_contract_source,
                "finalization_pending": self.finalization_pending(),
            },
        }

    def _hydrate(self) -> None:
        """Build evidence-linked policy model and extract explicit rules."""
        # 1. Build evidence items
        for index, item in enumerate(self.policy_context):
            kind = str(item.get("kind", "policy"))
            content = str(item.get("content", ""))
            evidence_id = str(item.get("id") or f"evidence-{index + 1}")
            evidence = PolicyEvidence(evidence_id=evidence_id, kind=kind, content=content)
            self.evidence_model.append(evidence)

        # 2. Extract permitted policy references from policy context
        self.permitted_policy_references = policy_reference_ids(self.policy_context)

        # 3. Extract explicit rules from policy context and declared tools
        tool_names = {t["name"] for t in self.tools}

        # 3a. Extract an explicitly declared completion contract from task
        # context. This is intentionally evidence-linked and opt-in: a tool is
        # never treated as a mandatory finalizer merely because of its name.
        for evidence in self.evidence_model:
            if evidence.kind.lower() not in {"task", "instruction", "workflow"}:
                continue
            match = FINALIZATION_PATTERN.search(evidence.content)
            if match and match.group("tool") in tool_names:
                self.finalization_tool = match.group("tool")
                self.finalization_evidence_ids = [evidence.id]
                self.finalization_contract_source = "task_context"
                break

        # 3b. If task context does not state a finalizer, accept a terminal
        # contract stated by a declared tool itself. This requires two
        # independent, explicit signals: a terminal-decision description and
        # a required decision enum. It deliberately does not infer behavior
        # from a tool's name.
        if self.finalization_tool is None:
            for tool in self.tools:
                parameters = tool.get("parameters")
                if not isinstance(parameters, dict):
                    continue
                properties = parameters.get("properties", {})
                if not isinstance(properties, dict):
                    continue
                decision_schema = properties.get("decision", {})
                decision_values = decision_schema.get("enum", []) if isinstance(decision_schema, dict) else []
                required = parameters.get("required", [])
                has_required_decision = (
                    isinstance(required, list)
                    and "decision" in required
                    and isinstance(decision_values, list)
                    and len(decision_values) >= 2
                    and all(isinstance(value, str) for value in decision_values)
                )
                if has_required_decision and TOOL_FINALIZATION_PATTERN.search(str(tool.get("description", ""))):
                    self.finalization_tool = tool["name"]
                    self.finalization_contract_source = "tool_contract"
                    break

        # 3c. From policy evidence
        for evidence in self.evidence_model:
            if evidence.kind.lower() != "policy":
                continue
            lines = evidence.content.splitlines()
            for line in lines:
                line_str = line.strip()
                if not line_str:
                    continue
                line_lower = line_str.lower()

                # Check prohibitions
                for target_tool in tool_names:
                    if target_tool.lower() in line_lower:
                        if any(kw in line_lower for kw in ["prohibit", "forbidden", "shall not", "never", "do not"]):
                            rule_id = f"rule-{uuid.uuid4().hex[:8]}"
                            self.explicit_rules.append(
                                ExplicitRule(
                                    rule_id=rule_id,
                                    rule_type="prohibition",
                                    description=f"Explicit prohibition for tool {target_tool}",
                                    target_tool=target_tool,
                                    evidence_ids=[evidence.id],
                                )
                            )

                # Check explicit prerequisites with directional relation matching
                for target_tool in tool_names:
                    for prereq_candidate in tool_names:
                        if target_tool == prereq_candidate:
                            continue
                        t_idx = line_lower.find(target_tool.lower())
                        p_idx = line_lower.find(prereq_candidate.lower())
                        if t_idx == -1 or p_idx == -1:
                            continue

                        # Pattern A: target_tool ... (requires|after|depends on|must execute) ... prereq_candidate
                        req_idx = -1
                        for kw in ["requires", "after", "depends on", "following", "must execute"]:
                            k = line_lower.find(kw)
                            if k != -1:
                                req_idx = k
                                break

                        if req_idx != -1 and t_idx < req_idx < p_idx:
                            rule_id = f"rule-{uuid.uuid4().hex[:8]}"
                            self.explicit_rules.append(
                                ExplicitRule(
                                    rule_id=rule_id,
                                    rule_type="prerequisite",
                                    description=f"Explicit prerequisite: {target_tool} requires prior execution of {prereq_candidate}",
                                    target_tool=target_tool,
                                    prerequisites=[prereq_candidate],
                                    evidence_ids=[evidence.id],
                                )
                            )

                        # Pattern B: prereq_candidate ... before ... target_tool
                        bef_idx = line_lower.find("before")
                        if bef_idx != -1 and p_idx < bef_idx < t_idx:
                            rule_id = f"rule-{uuid.uuid4().hex[:8]}"
                            self.explicit_rules.append(
                                ExplicitRule(
                                    rule_id=rule_id,
                                    rule_type="prerequisite",
                                    description=f"Explicit prerequisite: {target_tool} requires prior execution of {prereq_candidate}",
                                    target_tool=target_tool,
                                    prerequisites=[prereq_candidate],
                                    evidence_ids=[evidence.id],
                                )
                            )

        # 3d. From tool schema specifications / descriptions
        for tool in self.tools:
            name = tool.get("name", "")
            desc = tool.get("description", "")
            matches = PREREQUISITE_PATTERN.findall(desc)
            prereqs = [m for m in matches if m in tool_names and m != name]
            if prereqs:
                rule_id = f"tool-rule-{name}"
                self.explicit_rules.append(
                    ExplicitRule(
                        rule_id=rule_id,
                        rule_type="prerequisite",
                        description=f"Tool contract prerequisite for {name}: {', '.join(prereqs)}",
                        target_tool=name,
                        prerequisites=prereqs,
                        evidence_ids=[],
                    )
                )

    def update_state(self, messages: List[Dict[str, Any]]) -> None:
        """Update tool-result state and provenance from conversation messages."""
        call_map: Dict[str, Dict[str, Any]] = {}
        for msg in messages:
            if not isinstance(msg, dict):
                continue
            role = msg.get("role")
            if role == "assistant" and msg.get("tool_calls"):
                for call in msg["tool_calls"]:
                    if isinstance(call, dict):
                        cid = call.get("id", "")
                        func = call.get("function", {})
                        name = func.get("name", call.get("name", ""))
                        raw_args = func.get("arguments", call.get("arguments", {}))
                        if isinstance(raw_args, str):
                            try:
                                args = json.loads(raw_args)
                            except json.JSONDecodeError:
                                args = {}
                        else:
                            args = raw_args or {}
                        if cid:
                            call_map[cid] = {"name": name, "arguments": args}
            elif role == "tool" or msg.get("type") == "function_call_output":
                cid = msg.get("tool_call_id") or msg.get("call_id") or msg.get("id", "")
                output = msg.get("content") or msg.get("output", "")
                if cid and cid in call_map:
                    info = call_map[cid]
                    self.state.record_tool_result(
                        call_id=cid,
                        tool_name=info["name"],
                        arguments=info["arguments"],
                        output=output,
                        status="success",
                    )

    def get_explicit_prerequisites(self, tool_name: str) -> List[str]:
        """Return high-confidence explicit prerequisites required for a tool."""
        prereqs: Set[str] = set()
        for rule in self.explicit_rules:
            if rule.target_tool == tool_name and rule.rule_type == "prerequisite":
                prereqs.update(rule.prerequisites)
        return sorted(prereqs)

    def evaluate_tool_readiness(self, tool_name: str) -> Dict[str, Any]:
        """Identify if a tool is enabled and check if explicit prerequisites are satisfied."""
        declared_names = {t["name"] for t in self.tools}
        if tool_name not in declared_names:
            return {
                "enabled": False,
                "missing_prerequisites": [],
                "reasons": [f"Tool '{tool_name}' is not in declared tool contract."],
            }

        for rule in self.explicit_rules:
            if rule.target_tool == tool_name and rule.rule_type == "prohibition":
                return {
                    "enabled": False,
                    "missing_prerequisites": [],
                    "reasons": [rule.description],
                }

        prereqs = self.get_explicit_prerequisites(tool_name)
        missing = [p for p in prereqs if p not in self.state.executed_tools]
        if missing:
            return {
                "enabled": False,
                "missing_prerequisites": missing,
                "reasons": [f"Tool '{tool_name}' missing explicit prerequisites: {', '.join(missing)}"],
            }

        return {"enabled": True, "missing_prerequisites": [], "reasons": []}

    def get_enabled_tools(self) -> List[Dict[str, Any]]:
        """Return declared tools that satisfy all high-confidence explicit prerequisites."""
        enabled: List[Dict[str, Any]] = []
        for tool in self.tools:
            name = tool["name"]
            readiness = self.evaluate_tool_readiness(name)
            if readiness["enabled"]:
                enabled.append(tool)
        return enabled

    def finalization_pending(self) -> bool:
        """Whether a declared finalizer must follow an operational result."""
        return bool(
            self.finalization_tool
            and self.finalization_tool not in self.state.executed_tools
            and any(record.tool_name != self.finalization_tool for record in self.state.tool_results)
        )

    def tools_for_planning(self) -> List[Dict[str, Any]]:
        """Constrain a completion phase to its explicit declared finalizer."""
        if not self.finalization_pending():
            return list(self.tools)
        return [tool for tool in self.tools if tool["name"] == self.finalization_tool]

    def required_tool_choice(self) -> Optional[Dict[str, str] | str]:
        """Return a provider-neutral native-tool requirement for this phase."""
        if self.finalization_pending() and self.finalization_tool:
            return {"type": "function", "name": self.finalization_tool}
        if self.finalization_tool and self.finalization_tool not in self.state.executed_tools:
            return "required"
        return None

    def completion_guidance(self) -> str:
        """Return model-facing lifecycle guidance without exposing policy text."""
        if not self.finalization_tool:
            return ""
        if self.finalization_pending():
            return (
                "A declared finalization action is now pending after an operational result. "
                "Call the remaining finalization tool with grounded arguments before returning user-facing text."
            )
        return (
            "The supplied task declares a finalization tool. Complete any necessary inspection or "
            "operational actions first, then record the final grounded outcome through that declared tool."
        )

    def _proposal_denial_errors(self, proposal: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], List[str], List[str]]:
        """Validate proposal and return (errors, missing_prereqs, evidence_links)."""
        declared = {t["name"]: t for t in self.tools}
        name = proposal.get("name")
        tool = declared.get(name)
        errors: List[Dict[str, Any]] = []
        missing_prereqs: List[str] = []
        evidence_links: List[str] = []

        if tool is None:
            return [{"code": "undeclared_tool", "message": f"Tool '{name}' is not declared."}], [], []

        # 1. Check readiness / explicit prerequisites
        readiness = self.evaluate_tool_readiness(name)
        if not readiness["enabled"]:
            if readiness["missing_prerequisites"]:
                missing_prereqs.extend(readiness["missing_prerequisites"])
                errors.append(
                    {
                        "code": "missing_prerequisite",
                        "message": f"Tool '{name}' cannot be executed without prior execution of {', '.join(readiness['missing_prerequisites'])}.",
                    }
                )
            else:
                errors.append({"code": "prohibited_action", "message": readiness["reasons"][0]})

        for rule in self.explicit_rules:
            if rule.target_tool == name:
                evidence_links.extend(rule.evidence_ids)

        # 2. Argument schema validation
        arguments = proposal.get("arguments")
        schema = tool.get("parameters")
        if not isinstance(arguments, dict):
            errors.append({"code": "invalid_arguments", "message": "Arguments must be an object."})
            return errors, missing_prereqs, evidence_links

        if isinstance(schema, dict):
            required = schema.get("required", [])
            properties = schema.get("properties", {})
            for req in required:
                if req not in arguments:
                    errors.append(
                        {"code": "required_argument_missing", "message": f"Missing required parameter: {req}"}
                    )
            if schema.get("additionalProperties") is False:
                for arg_key in arguments:
                    if arg_key not in properties:
                        errors.append(
                            {"code": "undeclared_argument", "message": f"Undeclared argument: {arg_key}"}
                        )

            for arg_key, val in arguments.items():
                expected_prop = properties.get(arg_key)
                if isinstance(expected_prop, dict):
                    expected = expected_prop.get("type")
                    valid = (
                        expected is None
                        or (expected == "string" and isinstance(val, str))
                        or (expected == "boolean" and isinstance(val, bool))
                        or (expected == "number" and isinstance(val, (int, float)) and not isinstance(val, bool))
                        or (expected == "integer" and isinstance(val, int) and not isinstance(val, bool))
                        or (expected == "object" and isinstance(val, dict))
                        or (expected == "array" and isinstance(val, list))
                    )
                    if not valid:
                        errors.append(
                            {
                                "code": "argument_type_mismatch",
                                "message": f"Type mismatch for parameter '{arg_key}': expected {expected}, got {type(val).__name__}.",
                            }
                        )

                if POLICY_REFERENCE_FIELD.search(arg_key) and self.permitted_policy_references:
                    cited = val if isinstance(val, list) else [val]
                    if not all(isinstance(ref, str) and ref in self.permitted_policy_references for ref in cited):
                        errors.append(
                            {
                                "code": "unverified_policy_reference",
                                "message": f"Parameter '{arg_key}' contains policy references not present in policy context.",
                            }
                        )

        return errors, missing_prereqs, sorted(set(evidence_links))

    def admit_proposals(
        self,
        proposals: Iterable[Dict[str, Any]],
        *,
        phase: str = "pre_dispatch",
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Evaluate proposed native tool calls and return admitted calls and structured decision events."""
        admitted: List[Dict[str, Any]] = []
        events: List[Dict[str, Any]] = []

        for proposal in proposals:
            if not isinstance(proposal, dict):
                continue
            errors, missing_prereqs, evidence_links = self._proposal_denial_errors(proposal)
            allowed = not errors

            event = decision_event(
                {
                    "ok": allowed,
                    "status": "admitted" if allowed else "preflight_denied",
                    "action_name": proposal.get("name"),
                    "request_id": proposal.get("id"),
                    "reasons": errors,
                },
                phase=phase,
            )

            if missing_prereqs:
                event["missing_prerequisites"] = missing_prereqs
            if evidence_links:
                event["evidence_links"] = evidence_links

            events.append(event)
            if allowed:
                admitted.append(proposal)

        return admitted, events
