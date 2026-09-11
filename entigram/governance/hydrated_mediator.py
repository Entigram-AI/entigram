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
    is_permitted_policy_reference,
    is_policy_reference_field,
    normalize_tool_contract,
    policy_reference_ids,
    structured_policy_reference_ids,
    tool_argument_errors,
)

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
POLICY_HIERARCHY_PATTERN = re.compile(
    r"[^.\n]*(?:take precedence|takes precedence|override|overrides|does not override|exception|conflict(?:s|ing)?)[^.\n]*[.]?",
    re.IGNORECASE,
)
POLICY_HEADING_PATTERN = re.compile(r"^\s*#{1,6}\s*(?P<number>\d+(?:\.\d+)*)\.\s*(?P<title>.+?)\s*$")
POLICY_SECTION_REFERENCE_PATTERN = re.compile(r"\bsection\s+(\d+(?:\.\d+)*)\b", re.IGNORECASE)
POLICY_TOKEN_PATTERN = re.compile(r"[a-z0-9][a-z0-9_-]{2,}", re.IGNORECASE)
POLICY_DIRECTIVE_PATTERN = re.compile(
    r"\b(?:must|shall|should|do\s+not|don't|cannot|can't|not\s+process|not\s+disclose|"
    r"required|only\s+if|unless|escalat\w*|non-returnable|not\s+eligible|prohibit\w*)\b",
    re.IGNORECASE,
)
POLICY_AMBIGUITY_PATTERN = re.compile(
    r"\b(?:conflict(?:s|ing)?|contradict(?:s|ory)?|ambiguous|uncertain|when\s+in\s+doubt|not\s+clearly\s+covered)\b",
    re.IGNORECASE,
)
POLICY_ESCALATION_PATTERN = re.compile(r"\bescalat\w*\b", re.IGNORECASE)
INSPECTION_TOOL_PATTERN = re.compile(
    r"\b(?:look\s*up|lookup|check|verify|search|retrieve|read|get)\b",
    re.IGNORECASE,
)
CONSEQUENTIAL_TOOL_PATTERN = re.compile(
    r"\b(?:process|apply|issue|deny|approve|escalate|hold|flag|create|update|delete|record|finali[sz]e)\b",
    re.IGNORECASE,
)
EFFECT_IDENTITY_FIELD_PATTERN = re.compile(r"(?:^|[_-])id$", re.IGNORECASE)
EFFECT_OUTCOME_FIELD_PATTERN = re.compile(
    r"^(?:decision|outcome|resolution|disposition|status)$", re.IGNORECASE
)

# These terms describe the policy-writing frame rather than the decision at
# hand.  They would otherwise make every section appear equally relevant.
POLICY_RETRIEVAL_STOP_WORDS = frozenset({
    "about", "also", "and", "are", "been", "but", "can", "customer", "for",
    "from", "has", "have", "into", "item", "items", "may", "not", "only",
    "or", "our", "please", "return", "returns", "should", "that", "the", "their",
    "this", "was", "when", "with", "would", "your",
})


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
        self.explicit_policy_references: List[str] = []
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
            "structured_policy_reference_count": len(self.explicit_policy_references),
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

    def citation_guidance(self) -> str:
        """Return a bounded, policy-derived citation contract for the planner."""
        references = self.explicit_policy_references or self.permitted_policy_references
        if not references:
            return ""
        rendered_references = references[:32]
        rendered = ", ".join(json.dumps(reference) for reference in rendered_references)
        suffix = "" if len(rendered_references) == len(references) else ", ..."
        authority = "producer-supplied structured" if self.explicit_policy_references else "policy-derived"
        return (
            "For parameters explicitly representing policy citations, "
            f"use only one or more exact values from this {authority} allowlist: {rendered}{suffix}. "
            "Do not add titles, prose, punctuation, or inferred identifiers."
        )

    def hierarchy_guidance(self) -> str:
        """Summarize explicit policy precedence without inferring new rules."""
        excerpts: List[str] = []
        for evidence in self.evidence_model:
            if evidence.kind.lower() != "policy":
                continue
            heading = "policy"
            for line in evidence.content.splitlines():
                match = POLICY_HEADING_PATTERN.match(line)
                if match:
                    heading = f"Section {match.group('number')}: {match.group('title')}"
                    continue
                for statement in POLICY_HIERARCHY_PATTERN.findall(line):
                    normalized = " ".join(statement.split())
                    if normalized:
                        excerpts.append(f"{heading} — {normalized}")
                        if len(excerpts) == 12:
                            break
                if len(excerpts) == 12:
                    break
            if len(excerpts) == 12:
                break
        if not excerpts:
            return ""
        return "Apply only these explicit precedence statements when policy provisions interact:\n- " + "\n- ".join(excerpts)

    @staticmethod
    def _policy_tokens(text: str) -> Set[str]:
        """Return decision-bearing lexical terms from supplied text only."""
        return {
            token.casefold()
            for token in POLICY_TOKEN_PATTERN.findall(text)
            if token.casefold() not in POLICY_RETRIEVAL_STOP_WORDS
        }

    def _policy_sections(self) -> List[Tuple[str, str]]:
        """Split supplied Markdown policy evidence into heading-bound sections."""
        sections: List[Tuple[str, str]] = []
        for evidence in self.evidence_model:
            if evidence.kind.lower() != "policy":
                continue
            heading = "Policy"
            body: List[str] = []
            for line in evidence.content.splitlines():
                match = POLICY_HEADING_PATTERN.match(line)
                if match:
                    if body:
                        sections.append((heading, "\n".join(body).strip()))
                    heading = f"Section {match.group('number')}: {match.group('title')}"
                    body = []
                else:
                    body.append(line)
            if body:
                sections.append((heading, "\n".join(body).strip()))
        return sections

    def _relevant_policy_sections(self, messages: Iterable[Dict[str, Any]]) -> List[Tuple[str, str]]:
        """Rank source policy sections using request and observed-evidence terms."""
        query_parts: List[str] = []
        for message in messages:
            if not isinstance(message, dict):
                continue
            # Assistant prose is a proposed interpretation, not ground truth.
            # Retrieval is driven by the request and observed runtime evidence
            # so an earlier model mistake cannot self-reinforce on later turns.
            if message.get("role") not in {"user", "tool"} and message.get("type") != "function_call_output":
                continue
            content = message.get("content", message.get("output", ""))
            if isinstance(content, str):
                query_parts.append(content)
        query_tokens = self._policy_tokens("\n".join(query_parts))
        if not query_tokens:
            return []
        sections = self._policy_sections()
        if not sections:
            return []
        document_frequency = {
            token: sum(token in self._policy_tokens(f"{heading}\n{body}") for heading, body in sections)
            for token in query_tokens
        }
        scored: List[Tuple[float, str, str]] = []
        for heading, body in sections:
            heading_tokens = self._policy_tokens(heading)
            section_tokens = self._policy_tokens(body)
            # A policy heading often carries the only explicit domain term
            # (for example, "Defective Electronics"). Treat it as source
            # evidence for retrieval, not merely as a score booster.
            shared = query_tokens & (section_tokens | heading_tokens)
            if not shared:
                continue
            # Rare matching terms discriminate sections; heading matches are
            # stronger but still retain their source text instead of becoming
            # a synthetic rule or classification.
            score = sum(1.0 / document_frequency[token] for token in shared)
            score += sum(2.0 / document_frequency[token] for token in query_tokens & heading_tokens)
            scored.append((score, heading, body))
        selected = [(heading, body) for _, heading, body in sorted(scored, key=lambda item: (-item[0], item[1]))[:4]]

        # A policy frequently states a general rule and then explicitly points
        # to the section defining a qualifying exception.  Follow only those
        # producer-authored links; this adds source evidence to the planner
        # without inferring that the exception applies.  It is deliberately
        # shallow and bounded so a broad overview section cannot pull in a
        # whole manual.
        section_by_number = {}
        for heading, body in sections:
            match = re.match(r"Section\s+(\d+(?:\.\d+)*):", heading, re.IGNORECASE)
            if match:
                section_by_number[match.group(1)] = (heading, body)
        selected_headings = {heading for heading, _ in selected}
        for _, body in list(selected):
            for reference in POLICY_SECTION_REFERENCE_PATTERN.findall(body):
                linked = section_by_number.get(reference)
                if linked is not None and linked[0] not in selected_headings:
                    selected.append(linked)
                    selected_headings.add(linked[0])
                    if len(selected) == 6:
                        return selected
        return selected

    def relevant_policy_guidance(self, messages: Iterable[Dict[str, Any]]) -> str:
        """Surface the most relevant supplied policy sections for this turn.

        This is intentionally an extractive, deterministic retrieval step: it
        never creates a policy rule, reference ID, or outcome.  The full policy
        remains controlling; excerpts make the evidence most connected to the
        current conversation visible near the decision point.
        """
        selected = self._relevant_policy_sections(messages)
        if not selected:
            return ""
        excerpts = [f"{heading}\n{body[:1800]}" for heading, body in selected]
        return (
            "Relevant policy evidence for the current turn (extractive; the full supplied policy remains controlling):\n"
            + "\n\n---\n\n".join(excerpts)
        )

    def directive_guidance(self, messages: Iterable[Dict[str, Any]]) -> str:
        """Highlight binding sentences from relevant source policy evidence.

        This is deliberately a salience aid, not a rules engine: the returned
        statements are verbatim policy sentences, each attached to the section
        from which it came.  It does not infer conditions, tool order, or a
        decision from prose.
        """
        directives: List[str] = []
        for heading, body in self._relevant_policy_sections(messages):
            for sentence in re.split(r"(?<=[.!?])\s+", " ".join(body.split())):
                if POLICY_DIRECTIVE_PATTERN.search(sentence):
                    directives.append(f"{heading} — {sentence}")
                    if len(directives) == 12:
                        break
            if len(directives) == 12:
                break
        if not directives:
            return ""
        return "Binding policy statements relevant to this turn (apply their conditions exactly):\n- " + "\n- ".join(directives)

    def ambiguity_guidance(self, messages: Iterable[Dict[str, Any]]) -> str:
        """Surface explicit policy clauses that require escalation for ambiguity.

        This is an extractive caution signal, not a classifier: it neither
        decides that a case is ambiguous nor invents an escalation requirement.
        It keeps source clauses that expressly connect ambiguity or conflict to
        escalation adjacent to the proposed action sequence.
        """
        clauses: List[str] = []
        for heading, body in self._relevant_policy_sections(messages):
            for sentence in re.split(r"(?<=[.!?])\s+", " ".join(body.split())):
                if POLICY_AMBIGUITY_PATTERN.search(sentence) and POLICY_ESCALATION_PATTERN.search(sentence):
                    clauses.append(f"{heading} — {sentence}")
                    if len(clauses) == 8:
                        break
            if len(clauses) == 8:
                break
        if not clauses:
            return ""
        return (
            "Explicit ambiguity/conflict escalation clauses relevant to this turn (apply only when their stated "
            "conditions are present; do not substitute a direct irreversible action for their declared review path):\n- "
            + "\n- ".join(clauses)
        )

    def relevant_structured_references(self, messages: Iterable[Dict[str, Any]]) -> List[str]:
        """Return provider-declared clause IDs whose labels match retrieved evidence."""
        headings = {heading.casefold() for heading, _ in self._relevant_policy_sections(messages)}
        references: List[str] = []
        for item in self.policy_context:
            metadata = item.get("metadata", {}) if isinstance(item, dict) else {}
            clauses = metadata.get("policy_clauses", []) if isinstance(metadata, dict) else []
            if not isinstance(clauses, list):
                continue
            for clause in clauses:
                if not isinstance(clause, dict):
                    continue
                clause_id = clause.get("clause_id") or clause.get("id")
                label = clause.get("section") or clause.get("label")
                if not isinstance(clause_id, str) or not isinstance(label, str):
                    continue
                normalized = label.casefold().replace("##", "").strip()
                if any(normalized == heading or normalized in heading or heading in normalized for heading in headings):
                    if clause_id not in references:
                        references.append(clause_id)
        return references

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
        self.explicit_policy_references = structured_policy_reference_ids(self.policy_context)

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
                    parsed_output = output
                    if isinstance(output, str):
                        try:
                            parsed_output = json.loads(output)
                        except (ValueError, TypeError):
                            pass
                    failed = msg.get("isError") is True or msg.get("error") is True
                    if isinstance(parsed_output, dict):
                        failed = failed or (
                            parsed_output.get("success") is False
                            or parsed_output.get("ok") is False
                            or parsed_output.get("isError") is True
                            or bool(parsed_output.get("error"))
                            or str(parsed_output.get("status", "")).casefold()
                            # Denial/cancellation can be successful business
                            # outcomes. They are not transport/execution errors.
                            in {"failed", "failure", "error"}
                        )
                    self.state.record_tool_result(
                        call_id=cid,
                        tool_name=info["name"],
                        arguments=info["arguments"],
                        output=output,
                        status="failed" if failed else "success",
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

    def inspection_tool_names(self) -> List[str]:
        """Identify declared read-only evidence tools from their contracts.

        This is deliberately conservative: a tool must advertise an inspection
        verb and must not advertise a consequential verb in its name or
        description. Producers can avoid heuristic classification by exposing
        explicit prerequisites, which continue to take precedence.
        """
        names: List[str] = []
        for tool in self.tools:
            text = f"{tool['name']} {tool.get('description', '')}"
            if INSPECTION_TOOL_PATTERN.search(text) and not CONSEQUENTIAL_TOOL_PATTERN.search(text):
                names.append(tool["name"])
        return names

    def initial_inspection_pending(self) -> bool:
        """Whether declared private state has an available evidence path."""
        inspection_names = set(self.inspection_tool_names())
        return bool(
            inspection_names
            and not any(record.tool_name in inspection_names for record in self.state.tool_results)
        )

    def finalization_pending(self) -> bool:
        """Whether observed work follows the latest successful finalization.

        This is session-local receipt ordering, not an entity-scoped commit or
        proof that every semantic obligation has been fulfilled. A failed
        finalizer cannot cover preceding work; a successful one cannot cover
        operations observed later.
        """
        if not self.finalization_tool:
            return False
        for record in reversed(self.state.tool_results):
            if record.tool_name != self.finalization_tool:
                return True
            if record.status == "success":
                return False
        return False

    def tools_for_planning(self) -> List[Dict[str, Any]]:
        """Return all declared tools throughout a multi-step workflow.

        A finalization record documents a completed workflow; it is not a
        barrier that may prematurely terminate subsequent required operations.
        """
        return list(self.tools)

    def required_tool_choice(self) -> Optional[Dict[str, str] | str]:
        """Avoid provider-level tool forcing for workflows with final records.

        The model needs to select the next grounded operation from the declared
        workflow; forcing an arbitrary function call or the finalizer can skip
        required inspection and multi-action procedures.
        """
        return "required" if self.initial_inspection_pending() else None

    def completion_guidance(self) -> str:
        """Return model-facing lifecycle guidance without exposing policy text."""
        if not self.finalization_tool:
            return ""
        if self.initial_inspection_pending():
            return (
                "Declared inspection tools can obtain material runtime state. "
                "Perform an admissible inspection before proposing an operational or finalization action."
            )
        if self.finalization_pending():
            return (
                "Operational work has begun and a declared finalization record remains outstanding. "
                "Continue any remaining declared inspection or operational actions before recording the final outcome. "
                "Record only after the required action sequence is complete; its outcome must agree with all observed operations and results."
            )
        if self.finalization_tool in self.state.executed_tools:
            return (
                "Finalization has already succeeded for the preceding observed work. "
                "Answer a conversational follow-up from observed results without repeating operations or replacing "
                "the recorded outcome merely to acknowledge, explain, or close the conversation. "
                "A genuinely new request, material evidence, an authorized correction, or an explicit ongoing-record "
                "requirement may justify further work under the original contract. Do not treat a request for "
                "information about completed work as evidence that its outcome changed."
            )
        return (
            "The supplied task declares a finalization tool. Complete any necessary inspection or "
            "operational actions first, then record the final grounded outcome through that declared tool."
        )

    @staticmethod
    def _identity_value(value: Any) -> str | None:
        """Render a stable, scalar producer value for an effect identity."""
        if value is None or isinstance(value, (dict, list, tuple, set)):
            return None
        if isinstance(value, str):
            return value if value else None
        if isinstance(value, (bool, int, float)):
            return json.dumps(value, sort_keys=True, separators=(",", ":"))
        return None

    def _effect_identity(self, tool: Dict[str, Any], arguments: Dict[str, Any]) -> Tuple[Tuple[str, str], ...]:
        """Derive a conservative identity for a declared business effect.

        Producers can explicitly declare ``x-entigram-idempotency-key`` as a
        string or list of argument names.  Otherwise stable ``*_id`` fields are
        used. A changed decision does not itself authorize a second effect:
        producers must explicitly declare ``x-entigram-allow-correction`` for
        an auditable correction path. Free text, policy citations,
        quantities, and model-generated explanations intentionally do not
        change the identity of an effect.
        Returning an empty tuple means the contract provides no safe identity;
        callers then only guard an exact duplicate.
        """
        schema = tool.get("parameters", {})
        configured = schema.get("x-entigram-idempotency-key") if isinstance(schema, dict) else None
        if isinstance(configured, str):
            names = [configured]
        elif isinstance(configured, list) and all(isinstance(name, str) for name in configured):
            names = configured
        else:
            names = sorted(name for name in arguments if EFFECT_IDENTITY_FIELD_PATTERN.search(name))
        values = []
        for name in names:
            value = self._identity_value(arguments.get(name))
            if value is None:
                if configured is not None:
                    return ()
                continue
            values.append((name, value))
        return tuple(values)

    def _completed_effect(self, tool: Dict[str, Any], arguments: Dict[str, Any]) -> ToolResultProvenance | None:
        """Return an equivalent successful effect unless its contract is repeatable.

        The executor has already observed the original receipt, so this is not
        a prediction of entity state. It prevents a model from replaying the
        same declared effect merely because a conversation is retransmitted or
        a follow-up asks for an explanation. Producers retain authority to mark
        an operation repeatable when repeated effects are valid by design.
        """
        schema = tool.get("parameters", {})
        if isinstance(schema, dict) and schema.get("x-entigram-repeatable") is True:
            return None
        allow_correction = isinstance(schema, dict) and schema.get("x-entigram-allow-correction") is True
        identity = self._effect_identity(tool, arguments)
        for receipt in self.state.tool_results:
            if receipt.status != "success" or receipt.tool_name != tool["name"]:
                continue
            if identity:
                if self._effect_identity(tool, receipt.arguments) == identity:
                    if allow_correction:
                        outcome_names = {
                            name for name in set(arguments) | set(receipt.arguments)
                            if EFFECT_OUTCOME_FIELD_PATTERN.fullmatch(name)
                        }
                        if outcome_names and any(arguments.get(name) != receipt.arguments.get(name) for name in outcome_names):
                            continue
                    return receipt
            elif receipt.arguments == arguments:
                return receipt
        return None

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

        errors.extend(tool_argument_errors(arguments, schema))
        if isinstance(schema, dict):
            properties = schema.get("properties", {})
            if not isinstance(properties, dict):
                return errors, missing_prereqs, sorted(set(evidence_links))

            for arg_key, val in arguments.items():
                expected_prop = properties.get(arg_key)

                references = self.explicit_policy_references or self.permitted_policy_references
                if is_policy_reference_field(arg_key, expected_prop) and references:
                    cited = val if isinstance(val, list) else [val]
                    if not all(is_permitted_policy_reference(ref, references) for ref in cited):
                        errors.append(
                            {
                                "code": "unverified_policy_reference",
                                "message": f"Parameter '{arg_key}' contains policy references not present in policy context.",
                            }
                        )

        completed = self._completed_effect(tool, arguments)
        if completed is not None:
            errors.append(
                {
                    "code": "duplicate_completed_action",
                    "message": (
                        f"Tool '{name}' already completed the same declared effect "
                        f"in receipt '{completed.call_id}'."
                    ),
                    "completed_call_id": completed.call_id,
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
