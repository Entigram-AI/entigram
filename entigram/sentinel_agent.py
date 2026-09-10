"""Minimal A2A service used to register Entigram Sentinel with AgentBeats.

This service is deliberately side-effect free.  It establishes the transport
and identity boundary for the benchmark participant; PiBench-specific policy
and tool mediation are added separately before any scored submission.
"""

from __future__ import annotations

import argparse
import json
import os
import uuid
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


AGENT_NAME = "Entigram Sentinel"
AGENT_VERSION = "0.1.0"


def agent_card(card_url: str) -> dict[str, Any]:
    """Return the public A2A Agent Card served by the container."""
    return {
        "protocolVersion": "0.3.0",
        "name": AGENT_NAME,
        "description": (
            "An Entigram-governed policy agent that interprets operational "
            "rules and records safe ALLOW, DENY, or ESCALATE decisions."
        ),
        "url": card_url.rstrip("/"),
        "version": AGENT_VERSION,
        "capabilities": {"streaming": False, "pushNotifications": False},
        "defaultInputModes": ["text/plain"],
        "defaultOutputModes": ["text/plain"],
        "skills": [
            {
                "id": "policy-governance",
                "name": "Policy governance",
                "description": "Safely evaluates policy-governed requests.",
                "tags": ["policy", "governance", "safety"],
            }
        ],
    }


def _text_from_message(params: dict[str, Any]) -> str:
    message = params.get("message", {})
    return "\n".join(
        str(part.get("text", ""))
        for part in message.get("parts", [])
        if isinstance(part, dict) and part.get("kind") == "text"
    ).strip()


def handle_request(request: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    """Handle the small, non-streaming A2A JSON-RPC surface used for smoke tests."""
    request_id = request.get("id")
    if request.get("jsonrpc") != "2.0" or request.get("method") != "message/send":
        return HTTPStatus.BAD_REQUEST, {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": -32601, "message": "Only message/send is supported."},
        }

    params = request.get("params")
    if not isinstance(params, dict):
        return HTTPStatus.BAD_REQUEST, {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": -32602, "message": "params must be an object."},
        }

    _text_from_message(params)  # Validate the supported input shape without retaining content.
    task_id = str(uuid.uuid4())
    response = (
        "Entigram Sentinel is online. This registration build is side-effect "
        "free; PiBench policy and tool mediation have not yet been enabled."
    )
    return HTTPStatus.OK, {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": {
            "id": task_id,
            "contextId": params.get("message", {}).get("contextId", task_id),
            "status": {
                "state": "completed",
                "message": {"role": "agent", "parts": [{"kind": "text", "text": response}]},
            },
        },
    }


class SentinelRequestHandler(BaseHTTPRequestHandler):
    server_version = "EntigramSentinel/0.1"

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler convention
        if self.path == "/healthz":
            self._send_json(HTTPStatus.OK, {"ok": True, "agent": AGENT_NAME})
        elif self.path == "/.well-known/agent.json":
            self._send_json(HTTPStatus.OK, agent_card(self.server.card_url))
        else:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler convention
        if self.path not in ("/", "/a2a"):
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            request = json.loads(self.rfile.read(size))
        except (ValueError, json.JSONDecodeError):
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid JSON"})
            return
        if not isinstance(request, dict):
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "JSON object required"})
            return
        status, payload = handle_request(request)
        self._send_json(status, payload)

    def log_message(self, format: str, *args: object) -> None:
        if os.environ.get("ENTIGRAM_SENTINEL_LOG_REQUESTS") == "1":
            super().log_message(format, *args)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Entigram Sentinel A2A service.")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=9010)
    parser.add_argument("--card-url", default=os.environ.get("A2A_CARD_URL", "http://localhost:9010"))
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), SentinelRequestHandler)
    server.card_url = args.card_url
    server.serve_forever()


if __name__ == "__main__":
    main()
