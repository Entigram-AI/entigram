"""Tests for the WebSocket panel bridge."""

import asyncio
import json
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch


class TestPanelBridgeHandler(unittest.TestCase):
    """Tests for the WebSocket connection handler."""

    def test_ping_pong(self):
        """Bridge should respond to ping with pong."""
        from entigram.panel_bridge import _handle_connection

        ws = AsyncMock()
        ws.__aiter__ = lambda self: self
        messages = [json.dumps({"type": "ping"})]
        ws.__anext__ = AsyncMock(side_effect=messages + [StopAsyncIteration()])

        asyncio.run(_handle_connection(ws, "http://localhost:11435", None))

        sent = [json.loads(call.args[0]) for call in ws.send.call_args_list]
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0]["type"], "pong")

    def test_unknown_message_type(self):
        """Bridge should return error for unknown message types."""
        from entigram.panel_bridge import _handle_connection

        ws = AsyncMock()
        ws.__aiter__ = lambda self: self
        messages = [json.dumps({"type": "unknown_thing"})]
        ws.__anext__ = AsyncMock(side_effect=messages + [StopAsyncIteration()])

        asyncio.run(_handle_connection(ws, "http://localhost:11435", None))

        sent = [json.loads(call.args[0]) for call in ws.send.call_args_list]
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0]["type"], "error")
        self.assertIn("Unknown message type", sent[0]["message"])

    def test_invalid_json(self):
        """Bridge should return error for invalid JSON."""
        from entigram.panel_bridge import _handle_connection

        ws = AsyncMock()
        ws.__aiter__ = lambda self: self
        messages = ["not valid json{{{"]
        ws.__anext__ = AsyncMock(side_effect=messages + [StopAsyncIteration()])

        asyncio.run(_handle_connection(ws, "http://localhost:11435", None))

        sent = [json.loads(call.args[0]) for call in ws.send.call_args_list]
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0]["type"], "error")
        self.assertIn("Invalid JSON", sent[0]["message"])

    def test_empty_prompt_rejected(self):
        """Bridge should reject bot_prompt with empty prompt."""
        from entigram.panel_bridge import _handle_connection

        ws = AsyncMock()
        ws.__aiter__ = lambda self: self
        messages = [json.dumps({"type": "bot_prompt", "actor_id": "bot1", "prompt": ""})]
        ws.__anext__ = AsyncMock(side_effect=messages + [StopAsyncIteration()])

        asyncio.run(_handle_connection(ws, "http://localhost:11435", None))

        sent = [json.loads(call.args[0]) for call in ws.send.call_args_list]
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0]["type"], "error")
        self.assertIn("Empty prompt", sent[0]["message"])

    @patch("entigram.panel_bridge._stream_from_proxy")
    def test_bot_prompt_streams_reply(self, mock_stream):
        """Bridge should stream chunks and send done=True at end."""
        from entigram.panel_bridge import _handle_connection

        async def fake_stream(*args, **kwargs):
            yield "Hello "
            yield "world!"

        mock_stream.return_value = fake_stream()

        ws = AsyncMock()
        ws.__aiter__ = lambda self: self
        messages = [json.dumps({
            "type": "bot_prompt",
            "actor_id": "bot1",
            "prompt": "Say hello",
        })]
        ws.__anext__ = AsyncMock(side_effect=messages + [StopAsyncIteration()])

        asyncio.run(_handle_connection(ws, "http://localhost:11435", None))

        sent = [json.loads(call.args[0]) for call in ws.send.call_args_list]
        self.assertEqual(len(sent), 3)  # 2 chunks + 1 done

        self.assertEqual(sent[0]["type"], "bot_reply")
        self.assertEqual(sent[0]["chunk"], "Hello ")
        self.assertFalse(sent[0]["done"])

        self.assertEqual(sent[1]["chunk"], "world!")
        self.assertFalse(sent[1]["done"])

        self.assertEqual(sent[2]["chunk"], "")
        self.assertTrue(sent[2]["done"])

    @patch("entigram.panel_bridge._stream_from_proxy")
    def test_ledger_evidence_recorded(self, mock_stream):
        """Bridge should record delivery evidence when ledger is available."""
        from entigram.panel_bridge import _handle_connection

        async def fake_stream(*args, **kwargs):
            yield "reply"

        mock_stream.return_value = fake_stream()

        ws = AsyncMock()
        ws.__aiter__ = lambda self: self
        messages = [json.dumps({
            "type": "bot_prompt",
            "actor_id": "bot1",
            "prompt": "Test",
            "panel_id": "panel-123",
        })]
        ws.__anext__ = AsyncMock(side_effect=messages + [StopAsyncIteration()])

        ledger = MagicMock()
        asyncio.run(_handle_connection(ws, "http://localhost:11435", ledger))

        ledger.record_delivery_evidence.assert_called_once()
        call_kwargs = ledger.record_delivery_evidence.call_args
        self.assertEqual(call_kwargs.kwargs.get("evidence_type") or call_kwargs[1].get("evidence_type", call_kwargs[0][0] if call_kwargs[0] else None), "panel_exchange")


class TestPanelBridgeCLI(unittest.TestCase):
    """Tests for the CLI subcommand registration."""

    def test_panel_bridge_subcommand_exists(self):
        """The panel-bridge subcommand should be registered."""
        import subprocess
        result = subprocess.run(
            [sys.executable, "-m", "entigram.cli_runner.etg_cli", "panel-bridge", "--help"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("panel-bridge", result.stdout)
        self.assertIn("--proxy-url", result.stdout)
        self.assertIn("--port", result.stdout)


if __name__ == "__main__":
    unittest.main()
