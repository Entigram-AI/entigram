"""WebSocket bridge for Agent-Hosted Panel bot proxying.

Accepts WebSocket connections from the Influence Lab browser and proxies
bot prompts through the local Cloudflare/Ollama proxy for LLM completion,
streaming chunked replies back to the browser in real time.

Usage::

    etg panel-bridge --port 9090 --proxy-url http://127.0.0.1:11435
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
from pathlib import Path
from typing import Any, AsyncIterator, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Proxy streaming helpers
# ---------------------------------------------------------------------------


async def _stream_from_proxy(
    proxy_url: str,
    actor_id: str,
    prompt: str,
    context: str = "",
    model: str = "",
) -> AsyncIterator[str]:
    """Streams chat completion chunks from the Ollama-compatible proxy."""
    import aiohttp

    url = f"{proxy_url.rstrip('/')}/api/chat"
    messages = []
    if context:
        messages.append({"role": "system", "content": context})
    messages.append({"role": "user", "content": prompt})

    payload = {
        "model": model or "claude",
        "messages": messages,
        "stream": True,
    }

    timeout = aiohttp.ClientTimeout(total=120)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(url, json=payload) as resp:
            if resp.status != 200:
                body = (await resp.text())[:2000]
                yield f"[proxy error {resp.status}] {body}"
                return
            async for line in resp.content:
                line = line.strip()
                if not line:
                    continue
                try:
                    chunk = json.loads(line)
                    content = ""
                    if "message" in chunk:
                        content = chunk["message"].get("content", "")
                    elif "response" in chunk:
                        content = chunk.get("response", "")
                    if content:
                        yield content
                    if chunk.get("done"):
                        return
                except json.JSONDecodeError:
                    continue


# ---------------------------------------------------------------------------
# WebSocket handler
# ---------------------------------------------------------------------------


async def _handle_connection(
    ws: Any,
    proxy_url: str,
    ledger: Any | None,
) -> None:
    """Handles a single WebSocket connection from the Influence Lab browser."""
    import websockets

    try:
        async for raw in ws:
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await ws.send(json.dumps({
                    "type": "error",
                    "message": "Invalid JSON",
                }))
                continue

            if not isinstance(msg, dict):
                await ws.send(json.dumps({
                    "type": "error",
                    "message": "Message must be a JSON object",
                }))
                continue

            msg_type = msg.get("type")

            if msg_type == "ping":
                await ws.send(json.dumps({"type": "pong"}))
                continue

            if msg_type != "bot_prompt":
                await ws.send(json.dumps({
                    "type": "error",
                    "message": f"Unknown message type: {msg_type}",
                }))
                continue

            actor_id = msg.get("actor_id", "unknown")
            prompt = msg.get("prompt", "")
            context = msg.get("context", "")
            model = msg.get("model", "")
            panel_id = msg.get("panel_id", "")

            if not isinstance(prompt, str) or not prompt:
                await ws.send(json.dumps({
                    "type": "error",
                    "message": "Empty prompt",
                }))
                continue
            if len(prompt) > 100_000 or not isinstance(context, str) or len(context) > 100_000:
                await ws.send(json.dumps({
                    "type": "error",
                    "message": "Prompt or context exceeds 100000 characters",
                }))
                continue

            full_reply = []
            async for chunk in _stream_from_proxy(
                proxy_url, actor_id, prompt, context, model
            ):
                full_reply.append(chunk)
                await ws.send(json.dumps({
                    "type": "bot_reply",
                    "actor_id": actor_id,
                    "chunk": chunk,
                    "done": False,
                }))

            await ws.send(json.dumps({
                "type": "bot_reply",
                "actor_id": actor_id,
                "chunk": "",
                "done": True,
            }))

            # Audit trail
            if ledger:
                try:
                    ledger.record_delivery_evidence(
                        evidence_type="panel_exchange",
                        artifact_ref=actor_id,
                        result_summary=(
                            f"Panel prompt for {actor_id}: "
                            f"{len(full_reply)} chunks, "
                            f"{sum(len(c) for c in full_reply)} chars"
                        ),
                        agent_id=panel_id or None,
                    )
                except Exception as e:
                    logger.warning("Failed to record panel exchange: %s", e)

    except websockets.exceptions.ConnectionClosed:
        logger.info("Client disconnected")


# ---------------------------------------------------------------------------
# Server entry point
# ---------------------------------------------------------------------------


def run_panel_bridge(
    *,
    host: str = "127.0.0.1",
    port: int = 9090,
    proxy_url: str = "http://127.0.0.1:11435",
    target_dir: str = ".",
    allowed_origins: Optional[list[str]] = None,
) -> None:
    """Starts the WebSocket panel bridge server."""
    try:
        loopback = ipaddress.ip_address(host).is_loopback
    except ValueError:
        loopback = host.lower() == "localhost"
    if not loopback:
        raise ValueError(
            "panel bridge is restricted to loopback until authenticated remote transport is available"
        )
    try:
        import websockets
    except ImportError:
        print(
            "The panel bridge requires the 'websockets' package.\\n"
            "Install it with: pip install entigram-ai[panel]"
        )
        raise SystemExit(1)

    try:
        import aiohttp  # noqa: F401
    except ImportError:
        print(
            "The panel bridge requires the 'aiohttp' package.\\n"
            "Install it with: pip install aiohttp"
        )
        raise SystemExit(1)

    # Set up ledger for audit trail
    ledger = None
    try:
        from entigram.sqlite_ledger.manager import LedgerManager
        from entigram.sqlite_ledger.paths import resolve_ledger_path

        ledger_path = resolve_ledger_path(target_dir)
        ledger = LedgerManager(str(ledger_path))
    except Exception as e:
        logger.warning("Could not initialize ledger: %s (audit disabled)", e)

    async def handler(ws: Any) -> None:
        await _handle_connection(ws, proxy_url, ledger)

    async def main() -> None:
        print(f"🔌 Panel bridge listening on ws://{host}:{port}")
        print(f"   Proxy: {proxy_url}")
        print(f"   Ledger: {'enabled' if ledger else 'disabled'}")
        print(f"   Browser origins: {', '.join(allowed_origins or []) or 'none (native clients only)'}")
        print("   Waiting for Influence Lab connections...")
        async with websockets.serve(
            handler,
            host,
            port,
            origins=[None, *(allowed_origins or [])],
            max_size=1024 * 1024,
            max_queue=16,
            ping_timeout=20,
        ):
            await asyncio.Future()  # run forever

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\\n🛑 Panel bridge stopped.")
    finally:
        if ledger:
            ledger.close()
