import json
import tempfile
import unittest
from pathlib import Path

from entigram.agent_hooks import (
    agent_hook_status,
    handle_agent_hook,
    install_agent_hooks,
    remove_agent_hooks,
)
from entigram.injector import inject_entigram_manifest
from entigram.workspace_lifecycle import active_change_status, establish_active_change_baseline


class TestAgentHooks(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.assertTrue(
            inject_entigram_manifest(str(self.root), ["Entigram Schemas"], "Codex")
        )
        (self.root / "schema.lds").write_text("ENTITY: WorkItem {\n  id UUID PK\n}\n")
        establish_active_change_baseline(self.root, reason="test_setup")

    def tearDown(self):
        self.temp_dir.cleanup()

    def _payload(self, *, session_id="session-1", tool_name="apply_patch"):
        return {
            "session_id": session_id,
            "tool_name": tool_name,
            "tool_input": {"command": "apply_patch"},
        }

    def test_init_installs_supported_native_adapters(self):
        codex = json.loads((self.root / ".codex" / "hooks.json").read_text())
        claude = json.loads((self.root / ".claude" / "settings.json").read_text())
        self.assertIn("SessionStart", codex["hooks"])
        self.assertIn("PreToolUse", claude["hooks"])
        self.assertIn("--runtime codex", json.dumps(codex))
        self.assertIn("--runtime claude", json.dumps(claude))

        status = agent_hook_status(self.root)
        self.assertTrue(status["runtimes"]["antigravity"])
        self.assertTrue(status["runtimes"]["codex"])
        self.assertTrue(status["runtimes"]["claude"])
        self.assertFalse(status["git_checkin_guard"]["installed"])

    def test_codex_session_context_write_admission_and_stop_gate(self):
        blocked = handle_agent_hook(
            self.root,
            runtime="codex",
            event="pre-tool-use",
            payload=self._payload(),
        )
        self.assertEqual(
            blocked["hookSpecificOutput"]["permissionDecision"], "deny"
        )

        started = handle_agent_hook(
            self.root,
            runtime="codex",
            event="session-start",
            payload={"session_id": "session-1"},
        )
        context = started["hookSpecificOutput"]["additionalContext"]
        self.assertIn("Entigram lifecycle gate is active", context)
        self.assertIn("agent_policy.md", context)
        self.assertIn("schema.lds", context)

        allowed = handle_agent_hook(
            self.root,
            runtime="codex",
            event="pre-tool-use",
            payload=self._payload(),
        )
        self.assertEqual(allowed, {})

        (self.root / "changed.py").write_text("answer = 42\n")
        stopped = handle_agent_hook(
            self.root,
            runtime="codex",
            event="stop",
            payload={"session_id": "session-1"},
        )
        self.assertEqual(stopped["decision"], "block")
        self.assertIn("broker handoff", stopped["reason"])

    def test_claude_denies_next_write_when_change_budget_is_exhausted(self):
        handle_agent_hook(
            self.root,
            runtime="claude",
            event="session-start",
            payload={"session_id": "claude-1"},
        )
        for number in range(5):
            (self.root / f"drift-{number}.txt").write_text(f"{number}\n")
        self.assertTrue(active_change_status(self.root)["budget"]["exhausted"])

        denied = handle_agent_hook(
            self.root,
            runtime="claude",
            event="pre-tool-use",
            payload=self._payload(session_id="claude-1", tool_name="Write"),
        )
        self.assertEqual(
            denied["hookSpecificOutput"]["permissionDecision"], "deny"
        )
        self.assertIn("broker handoff", denied["hookSpecificOutput"]["permissionDecisionReason"])

    def test_adapter_removal_preserves_user_owned_configuration(self):
        codex_path = self.root / ".codex" / "hooks.json"
        codex = json.loads(codex_path.read_text())
        codex["approval_policy"] = "untrusted"
        codex_path.write_text(json.dumps(codex))
        install_agent_hooks(self.root, engine="all")

        removed = remove_agent_hooks(self.root)
        self.assertTrue(removed["runtimes"]["codex"]["removed"])
        preserved = json.loads(codex_path.read_text())
        self.assertEqual(preserved["approval_policy"], "untrusted")
        self.assertNotIn("hooks", preserved)


if __name__ == "__main__":
    unittest.main()
