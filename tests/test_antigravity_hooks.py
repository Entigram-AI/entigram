import json
import tempfile
import unittest
from pathlib import Path

from entigram.antigravity_hooks import (
    ANTIGRAVITY_HOOK_NAME,
    handle_antigravity_hook,
    install_antigravity_hooks,
    remove_antigravity_hooks,
)
from entigram.injector import inject_entigram_manifest
from entigram.workspace_lifecycle import (
    active_change_status,
    establish_active_change_baseline,
)


class TestAntigravityHooks(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.assertTrue(
            inject_entigram_manifest(
                str(self.root), ["Entigram Schemas"], "Antigravity"
            )
        )
        (self.root / "schema.lds").write_text(
            "ENTITY: WorkItem {\n  id UUID PK\n}\n"
        )
        establish_active_change_baseline(self.root, reason="test_setup")

    def tearDown(self):
        self.temp_dir.cleanup()

    def _write_payload(self, conversation_id="conversation-1"):
        return {
            "conversationId": conversation_id,
            "toolCall": {
                "name": "write_to_file",
                "args": {"AbsolutePath": str(self.root / "work.py")},
            },
        }

    def test_init_installs_namespaced_hooks_and_preserves_user_hooks(self):
        hook_path = self.root / ".agents" / "hooks.json"
        hooks = json.loads(hook_path.read_text())
        self.assertIn(ANTIGRAVITY_HOOK_NAME, hooks)
        entigram_hooks = hooks[ANTIGRAVITY_HOOK_NAME]
        self.assertEqual(
            set(entigram_hooks),
            {"PreInvocation", "PreToolUse", "PostToolUse", "Stop"},
        )
        self.assertIn("antigravity-hook", entigram_hooks["PreInvocation"][0]["command"])
        self.assertIn("generate_image", entigram_hooks["PreToolUse"][0]["matcher"])

        hooks["team-review"] = {"Stop": [{"type": "command", "command": "review"}]}
        hook_path.write_text(json.dumps(hooks))
        install_antigravity_hooks(self.root)
        removed = remove_antigravity_hooks(self.root)
        self.assertTrue(removed["removed"])
        preserved = json.loads(hook_path.read_text())
        self.assertEqual(
            preserved,
            {"team-review": {"Stop": [{"type": "command", "command": "review"}]}},
        )

    def test_pre_invocation_loads_context_and_active_budget_blocks_next_write(self):
        write = self._write_payload()
        blocked = handle_antigravity_hook(self.root, "pre-tool-use", write)
        self.assertEqual(blocked["decision"], "deny")

        invoked = handle_antigravity_hook(
            self.root, "pre-invocation", {"conversationId": "conversation-1"}
        )
        injected = invoked["injectSteps"]
        tool_paths = [
            step["toolCall"]["args"]["AbsolutePath"]
            for step in injected
            if "toolCall" in step
        ]
        self.assertIn(str((self.root / ".etg" / "agent_policy.md").resolve()), tool_paths)
        self.assertIn(str((self.root / "schema.lds").resolve()), tool_paths)
        self.assertIn("0/5", injected[-1]["ephemeralMessage"])

        allowed = handle_antigravity_hook(self.root, "pre-tool-use", write)
        self.assertEqual(allowed["decision"], "allow")

        for number in range(5):
            (self.root / f"drift-{number}.txt").write_text(f"{number}\n")
        status = active_change_status(self.root)
        self.assertTrue(status["budget"]["exhausted"])

        requires_check_in = handle_antigravity_hook(self.root, "pre-tool-use", write)
        self.assertEqual(requires_check_in["decision"], "deny")
        self.assertIn("broker handoff", requires_check_in["reason"])

        check_in_payload = {
            "conversationId": "conversation-1",
            "toolCall": {"name": "run_command", "args": {"CommandLine": "etg broker handoff"}},
        }
        self.assertEqual(
            handle_antigravity_hook(self.root, "pre-tool-use", check_in_payload)["decision"],
            "allow",
        )

    def test_pre_invocation_refreshes_context_when_policy_changes(self):
        payload = {"conversationId": "conversation-2"}
        first = handle_antigravity_hook(self.root, "pre-invocation", payload)
        self.assertTrue(first["injectSteps"])
        second = handle_antigravity_hook(self.root, "pre-invocation", payload)
        self.assertEqual(second["injectSteps"], [])

        policy_path = self.root / ".etg" / "agent_policy.md"
        policy_path.write_text(policy_path.read_text() + "\nUpdated policy.\n")
        refreshed = handle_antigravity_hook(self.root, "pre-invocation", payload)
        self.assertTrue(refreshed["injectSteps"])

    def test_stop_requests_one_handoff_after_observed_workspace_change(self):
        payload = self._write_payload("conversation-3")
        handle_antigravity_hook(
            self.root, "pre-invocation", {"conversationId": "conversation-3"}
        )
        (self.root / "work.py").write_text("answer = 42\n")

        first_stop = handle_antigravity_hook(
            self.root, "stop", {"conversationId": "conversation-3"}
        )
        self.assertEqual(first_stop["decision"], "continue")
        self.assertIn("broker handoff", first_stop["reason"])
        self.assertEqual(
            handle_antigravity_hook(
                self.root, "stop", {"conversationId": "conversation-3"}
            ),
            {},
        )


if __name__ == "__main__":
    unittest.main()
