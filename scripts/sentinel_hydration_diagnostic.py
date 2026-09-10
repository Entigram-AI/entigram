#!/usr/bin/env python3
"""Run a local, non-executing Sentinel hydration diagnostic.

The script submits a synthetic policy and declared tool contract to the same
A2A handler used by the agent.  It prints only aggregate hydration telemetry,
proposal names, and decision codes; it never prints policy text, tool outputs,
or credentials.  ``--live`` uses the configured OpenAI/Cloudflare provider to
exercise the model-facing route.  Without it, a deterministic proposal proves
the mediator gate itself without network or model cost.
"""
from __future__ import annotations

import argparse
import json
from typing import Any

from entigram.sentinel_agent import handle_request


POLICY_CONTEXT = [
    {
        "kind": "policy",
        "id": "DIAG-POL-001",
        "content": "issue_refund requires prior execution of verify_customer. "
        "All refund actions must cite DIAG-POL-001.",
    }
]
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "verify_customer",
            "description": "Verify the supplied customer identifier.",
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
            "description": "Issue a refund. Prerequisite: verify_customer.",
            "parameters": {
                "type": "object",
                "properties": {
                    "refund_id": {"type": "string"},
                    "policy_reference": {"type": "string"},
                },
                "required": ["refund_id", "policy_reference"],
                "additionalProperties": False,
            },
        },
    },
]


def request(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": "sentinel-hydration-diagnostic",
        "method": "message/send",
        "params": {"message": {"role": "user", "parts": [{"kind": "data", "data": data}]}},
    }


def result_data(response: dict[str, Any]) -> dict[str, Any]:
    return response["result"]["parts"][0]["data"]


def deterministic_model(_messages: list[dict[str, Any]], _tools: list[dict[str, Any]]) -> dict[str, Any]:
    """Propose an intentionally premature action to demonstrate the gate."""
    return {
        "output": [
            {
                "type": "function_call",
                "call_id": "diagnostic-refund",
                "name": "issue_refund",
                "arguments": json.dumps(
                    {"refund_id": "diagnostic-refund", "policy_reference": "DIAG-POL-001"}
                ),
            }
        ]
    }


def safe_summary(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "hydration": data["hydration"],
        "admitted_tool_names": [call["name"] for call in data.get("tool_calls", [])],
        "decision_outcomes": [event.get("outcome") for event in data.get("decision_events", [])],
        "decision_reason_codes": [event.get("reason_codes", []) for event in data.get("decision_events", [])],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Call the configured model provider once.")
    args = parser.parse_args()
    sessions: dict[str, dict[str, Any]] = {}

    status, bootstrap = handle_request(
        request({"bootstrap": True, "policy_context": POLICY_CONTEXT, "tools": TOOLS}), sessions=sessions
    )
    if status != 200:
        raise RuntimeError("bootstrap failed")
    bootstrap_data = result_data(bootstrap)
    print(json.dumps({"bootstrap_hydration": bootstrap_data["hydration"]}, sort_keys=True))

    client = None if args.live else deterministic_model
    status, turn = handle_request(
        request(
            {
                "context_id": bootstrap_data["context_id"],
                "messages": [
                    {
                        "role": "user",
                        "content": "Handle diagnostic refund. Use the declared tools only.",
                    }
                ],
            }
        ),
        sessions=sessions,
        model_client=client,
    )
    if status != 200:
        raise RuntimeError("diagnostic turn failed")
    print(json.dumps(safe_summary(result_data(turn)), sort_keys=True))


if __name__ == "__main__":
    main()
