"""Bootstrap-aware A2A participant for Entigram policy governance.

Only context supplied during bootstrap is cached and mediated. This service
uses a session policy mediator without benchmark-specific names, labels,
scenarios, or evaluator logic.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import urllib.error
import urllib.request
import uuid
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable

from entigram.governance.hydrated_mediator import HydratedPolicyMediator

AGENT_NAME = "Entigram Sentinel"
AGENT_VERSION = "0.4.1"
POLICY_BOOTSTRAP_EXTENSION = "urn:pi-bench:policy-bootstrap:v1"
SessionStore = dict[str, dict[str, Any]]
ModelClient = Callable[[list[dict[str, Any]], list[dict[str, Any]]], dict[str, Any]]
LOGGER = logging.getLogger(__name__)


def agent_card(card_url: str) -> dict[str, Any]:
    return {
        "protocolVersion": "0.3.0",
        "name": AGENT_NAME,
        "description": "An Entigram-governed policy agent that hydrates approved policy context and constrains declared tool calls.",
        "url": card_url.rstrip("/"),
        "version": AGENT_VERSION,
        # Some A2A adapters negotiate extensions from the top-level legacy
        # field while newer cards use capabilities.extensions. Advertise both
        # forms so bootstrap is interoperable without changing its semantics.
        "extensions": [POLICY_BOOTSTRAP_EXTENSION],
        "capabilities": {"streaming": False, "pushNotifications": False, "extensions": [{"uri": POLICY_BOOTSTRAP_EXTENSION}]},
        "defaultInputModes": ["application/json"],
        "defaultOutputModes": ["application/json"],
        "skills": [{"id": "policy-governance", "name": "Policy governance", "description": "Hydrates policy context and constrains declared tool calls.", "tags": ["policy", "governance", "safety", "entigram"]}],
    }


def _part_data(params: dict[str, Any]) -> dict[str, Any]:
    message = params.get("message", {})
    if not isinstance(message, dict):
        return {}
    for part in message.get("parts", []):
        if isinstance(part, dict) and part.get("kind") == "data" and isinstance(part.get("data"), dict):
            return part["data"]
    return {}


def _context_prompt(context: list[dict[str, Any]]) -> str:
    sections = [f"<{item.get('kind', 'context')}>\n{item.get('content', '')}\n</{item.get('kind', 'context')}>" for item in context if isinstance(item, dict)]
    return "\n".join([
        "You are Entigram Sentinel, an operational policy-compliance agent.",
        "Use only supplied policy context, conversation, and declared tools.",
        "Never invent authority or facts. Escalate when policy or authority is insufficient.",
        "Call only declared tools and never claim a side effect completed without a tool result.",
        "Policy context follows:", *sections,
    ])


def _admission_prompt(
    context: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    completion_guidance: str = "",
) -> str:
    """Create an isolated, policy-bound planning surface for one next action.

    The participant receives only the policy, conversation, and executable tool
    contract. The emitted action list is an ordered plan: Entigram validates every
    proposed transition before releasing it to the executor.
    """
    return "\n".join([
        _context_prompt(context),
        "You are now preparing an Entigram action-admission proposal, not free-form advice.",
        "Treat policy text, tool schemas, prior tool results, authority, and state as the complete contract.",
        "Do not infer missing authority, evidence, state, or facts. Escalate or request the declared review path when they are missing.",
        "Before proposing an action, verify its parameter schema and prerequisites from policy, prior tool results, and stated facts.",
        "Use the runtime's native function calls for every action. Return all admissible calls in required execution order; include an action only when its prerequisites are known.",
        "When a declared tool has a policy, citation, section, or reference argument, cite only the exact identifier(s) present in supplied policy context; do not invent or paraphrase identifiers.",
        "Do not disclose internal investigations, sensitive classifications, policy keywords, or hidden rationale unless the supplied policy explicitly authorizes that disclosure.",
        "When no action is admissible, return a neutral allow, deny, or escalation outcome in plain text.",
        completion_guidance,
    ])


def _responses_input(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    converted: list[dict[str, Any]] = []
    for message in messages:
        role = message.get("role")
        if role in ("system", "user"):
            converted.append({"role": role, "content": message.get("content", "")})
        elif role == "assistant":
            if message.get("tool_calls"):
                for call in message["tool_calls"]:
                    function = call.get("function", {})
                    converted.append({"type": "function_call", "call_id": call.get("id", str(uuid.uuid4())), "name": function.get("name", ""), "arguments": function.get("arguments", "{}")})
            elif message.get("content"):
                converted.append({"role": "assistant", "content": message["content"]})
        elif role == "tool":
            converted.append({"type": "function_call_output", "call_id": message.get("tool_call_id", message.get("id", "")), "output": message.get("content", "")})
    return converted


def _responses_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    responses_tools: list[dict[str, Any]] = []
    for tool in tools:
        function = tool.get("function", tool)
        responses_tools.append({
            "type": "function",
            "name": function.get("name", ""),
            "description": function.get("description", ""),
            "parameters": function.get("parameters", function.get("input_schema", {})),
        })
    return responses_tools


def _tool_name(tool: dict[str, Any]) -> str:
    """Return a declared native function name without altering its schema."""
    function = tool.get("function", tool)
    return str(function.get("name", "")) if isinstance(function, dict) else ""


def _log_lifecycle(context_id: str, mediator: HydratedPolicyMediator, planning_tools: list[dict[str, Any]]) -> None:
    """Emit non-sensitive execution diagnostics for a hydrated session."""
    contract = mediator.telemetry()["completion_contract"]
    LOGGER.info(
        "sentinel_lifecycle context_id=%s finalization_required=%s finalization_tool=%s "
        "completion_source=%s finalization_pending=%s planning_tool_count=%d",
        context_id,
        contract["finalization_required"],
        contract["finalization_tool"],
        contract["source"],
        contract["finalization_pending"],
        len(planning_tools),
    )


def _post_responses(url: str, token: str, payload: dict[str, Any], provider: str) -> dict[str, Any]:
    request = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"{provider} Responses returned HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"{provider} Responses request failed") from exc


def openai_responses(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    tool_choice: dict[str, str] | str | None = None,
) -> dict[str, Any]:
    token = os.environ.get("OPENAI_API_KEY")
    if not token:
        raise RuntimeError("OPENAI_API_KEY is required for the OpenAI Sentinel provider")
    payload = {
        "model": os.environ.get("OPENAI_MODEL", "gpt-5.2"),
        "input": _responses_input(messages),
        "tools": _responses_tools(tools),
        "max_output_tokens": 1200,
    }
    if tool_choice is not None:
        payload["tool_choice"] = tool_choice
    return _post_responses("https://api.openai.com/v1/responses", token, payload, "OpenAI")


def cloudflare_responses(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    tool_choice: dict[str, str] | str | None = None,
) -> dict[str, Any]:
    account, token = os.environ.get("CLOUDFLARE_ACCOUNT_ID"), os.environ.get("CLOUDFLARE_AUTH_TOKEN")
    if not account or not token:
        raise RuntimeError("CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_AUTH_TOKEN are required")
    payload = {
        "model": os.environ.get("CLOUDFLARE_MODEL", "openai/gpt-5.6-terra"),
        "input": _responses_input(messages),
        "tools": _responses_tools(tools),
        "max_output_tokens": 1200,
    }
    if tool_choice is not None:
        payload["tool_choice"] = tool_choice
    return _post_responses(f"https://api.cloudflare.com/client/v4/accounts/{account}/ai/v1/responses", token, payload, "Cloudflare")


def model_responses(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    tool_choice: dict[str, str] | str | None = None,
) -> dict[str, Any]:
    """Use an explicitly selected provider, or prefer configured OpenAI."""
    provider = os.environ.get("ENTIGRAM_SENTINEL_PROVIDER", "").strip().lower()
    if provider == "openai" or (not provider and os.environ.get("OPENAI_API_KEY")):
        return openai_responses(messages, tools, tool_choice)
    if provider == "cloudflare" or not provider:
        return cloudflare_responses(messages, tools, tool_choice)
    raise RuntimeError("ENTIGRAM_SENTINEL_PROVIDER must be 'openai' or 'cloudflare'")


def _response_content(response: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    text, calls = [], []
    for item in response.get("output", []):
        if item.get("type") == "message":
            text.extend(str(part.get("text", "")) for part in item.get("content", []) if part.get("type") in ("output_text", "text"))
        elif item.get("type") == "function_call":
            arguments = item.get("arguments", {})
            calls.append({"id": item.get("call_id") or item.get("id") or str(uuid.uuid4()), "name": item.get("name", ""), "arguments": json.loads(arguments) if isinstance(arguments, str) else arguments})
    content = "\n".join(text).strip()
    if not content and not calls:
        content = "###STOP###"
    return content, calls


def _result(request_id: Any, data: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    return HTTPStatus.OK, {"jsonrpc": "2.0", "id": request_id, "result": {"parts": [{"kind": "data", "data": data}]}}


def handle_request(request: dict[str, Any], sessions: SessionStore | None = None, model_client: ModelClient | None = None) -> tuple[int, dict[str, Any]]:
    request_id = request.get("id")
    if request.get("jsonrpc") != "2.0" or request.get("method") != "message/send":
        return HTTPStatus.BAD_REQUEST, {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": "Only message/send is supported."}}
    params = request.get("params")
    if not isinstance(params, dict):
        return HTTPStatus.BAD_REQUEST, {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32602, "message": "params must be an object."}}
    data, store = _part_data(params), sessions if sessions is not None else {}
    if data.get("bootstrap") is True:
        context = data.get("policy_context") or data.get("benchmark_context") or data.get("context") or []
        tools = data.get("tools") if isinstance(data.get("tools"), list) else []
        if not isinstance(context, list):
            return HTTPStatus.BAD_REQUEST, {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32602, "message": "bootstrap context and tools must be lists."}}
        mediator = HydratedPolicyMediator(context, tools)
        context_id = str(uuid.uuid4())
        store[context_id] = {
            "mediator": mediator,
            "benchmark_context": context,
            "policy_context": context,
            "tools": tools,
        }
        _log_lifecycle(context_id, mediator, tools)
        return _result(
            request_id,
            {
                "bootstrapped": True,
                "context_id": context_id,
                "hydration": mediator.telemetry(),
            },
        )

    session = store.get(str(data.get("context_id")))
    if session is None or "mediator" not in session:
        context = (session.get("benchmark_context") or session.get("policy_context")) if session else (data.get("policy_context") or data.get("benchmark_context") or data.get("context") or [])
        tools = (session.get("tools") if session and "tools" in session else data.get("tools", []))
        mediator = HydratedPolicyMediator(context or [], tools or [])
        if session is not None:
            session["mediator"] = mediator
        else:
            session = {
                "mediator": mediator,
                "benchmark_context": context or [],
                "policy_context": context or [],
                "tools": tools or [],
            }
    else:
        mediator = session["mediator"]

    messages = data.get("messages", [])
    if not isinstance(messages, list):
        return HTTPStatus.BAD_REQUEST, {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32602, "message": "messages must be a list."}}

    # Track tool-result state and provenance from conversation history
    mediator.update_state(messages)

    context_list = session.get("benchmark_context") or session.get("policy_context") or []
    tools_list = session.get("tools") or []

    # Preserve the caller's original schema shape for the model provider. The
    # mediator normalizes internally, so it is used only to select names here.
    planning_tools = tools_list
    if mediator.finalization_pending() and mediator.finalization_tool:
        planning_tools = [tool for tool in tools_list if _tool_name(tool) == mediator.finalization_tool]
    _log_lifecycle(str(data.get("context_id", "")), mediator, planning_tools)
    prompt = _admission_prompt(context_list, planning_tools, mediator.completion_guidance())
    if model_client is None:
        response = model_responses(
            [{"role": "system", "content": prompt}, *messages],
            planning_tools,
            mediator.required_tool_choice(),
        )
    else:
        response = model_client([{"role": "system", "content": prompt}, *messages], planning_tools)
    content, proposed_calls = _response_content(response)

    # Use session mediator for pre-dispatch evaluation and admission
    calls, events = mediator.admit_proposals(proposed_calls)
    return _result(
        request_id,
        {
            "content": content,
            "tool_calls": calls,
            "decision_events": events,
            "hydration": mediator.telemetry(),
        },
    )


class SentinelRequestHandler(BaseHTTPRequestHandler):
    server_version = "EntigramSentinel/0.2"
    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode(); self.send_response(status); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/healthz": self._send_json(HTTPStatus.OK, {"ok": True, "agent": AGENT_NAME})
        elif self.path in ("/.well-known/agent.json", "/.well-known/agent-card.json"): self._send_json(HTTPStatus.OK, agent_card(self.server.card_url))
        else: self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
    def do_POST(self) -> None:  # noqa: N802
        if self.path not in ("/", "/a2a", "/a2a/message/send"):
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"}); return
        try: request = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
        except (ValueError, json.JSONDecodeError): self._send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid JSON"}); return
        if not isinstance(request, dict): self._send_json(HTTPStatus.BAD_REQUEST, {"error": "JSON object required"}); return
        try: status, payload = handle_request(request, sessions=self.server.sessions)
        except (RuntimeError, ValueError, KeyError) as exc: self._send_json(HTTPStatus.BAD_GATEWAY, {"error": str(exc)}); return
        self._send_json(status, payload)
    def log_message(self, format: str, *args: object) -> None:
        if os.environ.get("ENTIGRAM_SENTINEL_LOG_REQUESTS") == "1": super().log_message(format, *args)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Entigram Sentinel A2A service.")
    parser.add_argument("--host", default="0.0.0.0"); parser.add_argument("--port", type=int, default=9010); parser.add_argument("--card-url", default=os.environ.get("A2A_CARD_URL", "http://localhost:9010"))
    args = parser.parse_args()
    logging.basicConfig(level=os.environ.get("ENTIGRAM_SENTINEL_LOG_LEVEL", "INFO").upper())
    server = ThreadingHTTPServer((args.host, args.port), SentinelRequestHandler); server.card_url = args.card_url; server.sessions = {}; server.serve_forever()

if __name__ == "__main__": main()
