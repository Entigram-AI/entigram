import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from entigram.agent_hooks import handle_agent_hook
from entigram.antigravity_hooks import (
    ANTIGRAVITY_HOOK_NAME,
    ANTIGRAVITY_SESSION_PATH,
    handle_antigravity_hook,
    install_antigravity_hooks,
    remove_antigravity_hooks,
)
from entigram.injector import inject_entigram_manifest
from entigram.task_context import prepare_task
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

    def test_interrupted_initialization_is_not_ready_and_can_refresh(self):
        payload = {"conversationId": "interrupted-init"}
        with patch("entigram.antigravity_hooks.active_change_status", side_effect=OSError("observation unavailable")):
            result = handle_antigravity_hook(self.root, "pre-invocation", payload)
        self.assertIn("observation unavailable", str(result))
        self.assertFalse(self._session(payload["conversationId"])["ready"])
        self.assertEqual(handle_antigravity_hook(self.root, "pre-tool-use", self._write_payload(payload["conversationId"]))["decision"], "deny")
        result = handle_antigravity_hook(self.root, "pre-invocation", payload)
        self.assertTrue(result["injectSteps"])
        session = self._session(payload["conversationId"])
        self.assertTrue(session["ready"])
        self.assertEqual(session["readiness_version"], 1)
        self.assertEqual(session["workspace_path"], str(self.root.resolve()))

    def test_policy_refresh_invalidates_ready_before_observation(self):
        payload = {"conversationId": "refresh-interrupted"}
        handle_antigravity_hook(self.root, "pre-invocation", payload)
        policy = self.root / ".etg" / "agent_policy.md"
        policy.write_text(policy.read_text() + "\nReview the changed policy.\n")
        def interrupted(*args, **kwargs):
            self.assertFalse(self._session(payload["conversationId"])["ready"])
            raise OSError("refresh interrupted")
        with patch("entigram.antigravity_hooks.active_change_status", side_effect=interrupted):
            handle_antigravity_hook(self.root, "pre-invocation", payload)
        self.assertFalse(self._session(payload["conversationId"])["ready"])

    def test_legacy_session_cannot_skip_readiness_or_backfill_original_observation(self):
        payload = {"conversationId": "legacy-incomplete-init"}
        handle_antigravity_hook(self.root, "pre-invocation", payload)
        path = self.root / ANTIGRAVITY_SESSION_PATH
        document = json.loads(path.read_text())
        record = document["sessions"][payload["conversationId"]]
        for key in ("ready", "readiness_version", "workspace_path", "start_filesystem_observation", "start_changed_files"):
            record.pop(key, None)
        path.write_text(json.dumps(document))
        result = handle_antigravity_hook(self.root, "pre-invocation", payload)
        self.assertTrue(result["injectSteps"])
        refreshed = self._session(payload["conversationId"])
        self.assertTrue(refreshed["ready"])
        self.assertNotIn("start_filesystem_observation", refreshed)

    def test_copied_or_stale_ready_context_does_not_allow_writes(self):
        payload = {"conversationId": "bound-ready-context"}
        handle_antigravity_hook(self.root, "pre-invocation", payload)
        path = self.root / ANTIGRAVITY_SESSION_PATH
        document = json.loads(path.read_text())
        document["sessions"][payload["conversationId"]]["workspace_path"] = str(self.root / "another-root")
        path.write_text(json.dumps(document))
        self.assertEqual(handle_antigravity_hook(self.root, "pre-tool-use", self._write_payload(payload["conversationId"]))["decision"], "deny")
        handle_antigravity_hook(self.root, "pre-invocation", payload)
        policy = self.root / ".etg" / "agent_policy.md"
        policy.write_text(policy.read_text() + "\nChanged after readiness.\n")
        result = handle_antigravity_hook(self.root, "pre-tool-use", self._write_payload(payload["conversationId"]))
        self.assertEqual(result["decision"], "deny")
        self.assertIn("context changed", result["reason"])

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
        hook_path.chmod(0o600)
        install_antigravity_hooks(self.root)
        self.assertEqual(hook_path.stat().st_mode & 0o777, 0o600)
        removed = remove_antigravity_hooks(self.root)
        self.assertTrue(removed["removed"])
        preserved = json.loads(hook_path.read_text())
        self.assertEqual(
            preserved,
            {"team-review": {"Stop": [{"type": "command", "command": "review"}]}},
        )

    def test_ignore_policy_change_invalidates_ready_context(self):
        payload = {"conversationId": "changed-ignore-policy"}
        prepare_task(self.root, task_id="test-task", description="Implement work.py")
        handle_antigravity_hook(self.root, "pre-invocation", payload)
        (self.root / ".etgignore").write_text("runtime.log\n")
        result = handle_antigravity_hook(self.root, "pre-tool-use", self._write_payload(payload["conversationId"]))
        self.assertEqual(result["decision"], "deny")
        self.assertIn("context changed", result["reason"])
        handle_antigravity_hook(self.root, "pre-invocation", payload)
        self.assertEqual(handle_antigravity_hook(self.root, "pre-tool-use", self._write_payload(payload["conversationId"]))["decision"], "allow")

    def test_runtime_journal_drift_does_not_request_stop_handoff_but_source_edit_does(self):
        (self.root / ".etgignore").write_text("runtime.log\n")
        establish_active_change_baseline(self.root, reason="accepted runtime exclusion")
        payload = {"conversationId": "runtime-only-session"}
        handle_antigravity_hook(self.root, "pre-invocation", payload)
        (self.root / "runtime.log").write_text("background service activity")
        result = handle_antigravity_hook(self.root, "stop", payload)
        self.assertNotIn("broker handoff", str(result))
        (self.root / "source.py").write_text("unaccepted source edit")
        result = handle_antigravity_hook(self.root, "stop", payload)
        self.assertIn("broker handoff", str(result))

    def test_pre_invocation_loads_context_and_active_budget_blocks_next_write(self):
        write = self._write_payload()
        blocked = handle_antigravity_hook(self.root, "pre-tool-use", write)
        self.assertEqual(blocked["decision"], "deny")

        invoked = handle_antigravity_hook(
            self.root, "pre-invocation", {"conversationId": "conversation-1"}
        )
        injected = invoked["injectSteps"]
        messages = [step["userMessage"] for step in injected if "userMessage" in step]
        self.assertTrue(any("Entigram governance policy" in message for message in messages))
        self.assertTrue(any("ENTITY: WorkItem" in message for message in messages))
        self.assertFalse(any("toolCall" in step for step in injected))
        self.assertIn("0/15", injected[-1]["ephemeralMessage"])

        prepare_task(self.root, task_id="test-task", description="Implement work.py")
        allowed = handle_antigravity_hook(self.root, "pre-tool-use", write)
        self.assertEqual(allowed["decision"], "allow")

        for number in range(15):
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

    def test_stale_task_context_does_not_block_required_check_in(self):
        """A workspace over budget must be able to recover before task setup."""
        handle_antigravity_hook(
            self.root, "pre-invocation", {"conversationId": "conversation-4"}
        )
        for number in range(15):
            (self.root / f"stale-{number}.txt").write_text(f"{number}\n")

        for command in (
            "etg broker handoff",
            "etg broker status",
            "etg task prepare --id recovery --description 'Recover task context'",
        ):
            result = handle_antigravity_hook(
                self.root,
                "pre-tool-use",
                {
                    "conversationId": "conversation-4",
                    "toolCall": {
                        "name": "run_command",
                        "args": {"CommandLine": command},
                    },
                },
            )
            self.assertEqual(result["decision"], "allow")

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

    def _session(self, conversation):
        return json.loads((self.root / ANTIGRAVITY_SESSION_PATH).read_text())["sessions"][conversation]

    def _replace_session(self, conversation, record):
        path = self.root / ANTIGRAVITY_SESSION_PATH
        data = json.loads(path.read_text())
        data["sessions"][conversation] = record
        path.write_text(json.dumps(data))

    def _preexisting_drift(self):
        for number in range(2):
            (self.root / f"preexisting-{number}.txt").write_text("Before this session\n")

    def test_unchanged_preexisting_drift_does_not_force_a_new_session_handoff(self):
        self._preexisting_drift()
        baseline = self.root / ".etg/lifecycle/check-in-baseline.json"
        baseline_before = baseline.read_bytes()
        payload = {"conversationId": "no-new-drift"}
        handle_antigravity_hook(self.root, "pre-invocation", payload)

        self.assertEqual(handle_antigravity_hook(self.root, "stop", payload), {})
        status = active_change_status(self.root)
        self.assertEqual(status["budget"]["changed_files"], 2)
        self.assertEqual(status["changes"]["created"], ["preexisting-0.txt", "preexisting-1.txt"])
        self.assertEqual(baseline.read_bytes(), baseline_before)
        self.assertEqual(self._session("no-new-drift")["writes_observed"], 0)
        self.assertFalse(self._session("no-new-drift")["stop_reminded"])

    def test_new_changes_to_already_dirty_files_still_require_handoff(self):
        self._preexisting_drift()
        payload = {"conversationId": "dirty-file-changed"}
        handle_antigravity_hook(self.root, "pre-invocation", payload)
        (self.root / "preexisting-0.txt").write_text("A different size after the session started\n")

        self.assertEqual(active_change_status(self.root)["budget"]["changed_files"], 2)
        self.assertEqual(handle_antigravity_hook(self.root, "stop", payload)["decision"], "continue")

    def test_codex_adapter_does_not_continue_unchanged_dirt_but_still_blocks_new_drift(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertTrue(inject_entigram_manifest(str(root), ["Entigram Schemas"], "Codex"))
            establish_active_change_baseline(root, reason="codex_fixture")
            for number in range(2):
                (root / f"existing-{number}.txt").write_text("Preexisting change\n")
            payload = {"session_id": "read-only-observer"}
            started = handle_agent_hook(root, runtime="codex", event="session-start", payload=payload)
            self.assertIn("2/15", started["hookSpecificOutput"]["additionalContext"])
            self.assertEqual(handle_agent_hook(root, runtime="codex", event="stop", payload=payload), {})

            (root / "existing-0.txt").write_text("Changed again after the session began\n")
            stopped = handle_agent_hook(root, runtime="codex", event="stop", payload=payload)
            self.assertEqual(stopped["decision"], "block")
            self.assertIn("broker handoff", stopped["reason"])
            self.assertEqual(active_change_status(root)["budget"]["changed_files"], 2)

    def test_repeated_pre_invocation_after_write_cannot_reset_start_observation(self):
        payload = {"conversationId": "repeat-after-write"}
        handle_antigravity_hook(self.root, "pre-invocation", payload)
        original = self._session(payload["conversationId"])["start_filesystem_observation"]
        (self.root / "work.py").write_text("answer = 42\n")

        self.assertEqual(handle_antigravity_hook(self.root, "pre-invocation", payload), {"injectSteps": []})
        self.assertEqual(self._session(payload["conversationId"])["start_filesystem_observation"], original)
        self.assertEqual(handle_antigravity_hook(self.root, "stop", payload)["decision"], "continue")

    def test_semantic_context_refresh_after_write_preserves_start_observation(self):
        payload = {"conversationId": "context-refresh-after-write"}
        handle_antigravity_hook(self.root, "pre-invocation", payload)
        original = self._session(payload["conversationId"])["start_filesystem_observation"]
        (self.root / "work.py").write_text("answer = 42\n")
        policy = self.root / ".etg/agent_policy.md"
        policy.write_text(policy.read_text() + "\nRefreshed governance context.\n")

        self.assertTrue(handle_antigravity_hook(self.root, "pre-invocation", payload)["injectSteps"])
        self.assertEqual(self._session(payload["conversationId"])["start_filesystem_observation"], original)
        self.assertEqual(handle_antigravity_hook(self.root, "stop", payload)["decision"], "continue")

    def test_legacy_or_malformed_start_observations_keep_handoff_reminder(self):
        self._preexisting_drift()
        for number, invalid in enumerate([None, {}, {"version": True, "digest": "a" * 64},
                                         {"version": 2, "digest": "a" * 64}, {"version": 1, "digest": "bad"},
                                         {"version": 1, "digest": "a" * 64, "extra": True}]):
            with self.subTest(invalid=invalid):
                conversation = f"malformed-{number}"
                payload = {"conversationId": conversation}
                handle_antigravity_hook(self.root, "pre-invocation", payload)
                record = self._session(conversation)
                if invalid is None:
                    record.pop("start_filesystem_observation")
                else:
                    record["start_filesystem_observation"] = invalid
                self._replace_session(conversation, record)
                self.assertEqual(handle_antigravity_hook(self.root, "stop", payload)["decision"], "continue")

    def test_unknown_start_dirty_count_is_not_a_preexisting_drift_grant(self):
        self._preexisting_drift()
        for number, count in enumerate([None, True, "2", -1, 0]):
            with self.subTest(count=count):
                conversation = f"unknown-dirty-{number}"
                payload = {"conversationId": conversation}
                handle_antigravity_hook(self.root, "pre-invocation", payload)
                record = self._session(conversation)
                record["start_changed_files"] = count
                self._replace_session(conversation, record)
                self.assertEqual(handle_antigravity_hook(self.root, "stop", payload)["decision"], "continue")

    def test_context_refresh_cannot_backfill_a_legacy_observation(self):
        self._preexisting_drift()
        payload = {"conversationId": "legacy-refresh"}
        handle_antigravity_hook(self.root, "pre-invocation", payload)
        record = self._session(payload["conversationId"])
        record.pop("start_filesystem_observation")
        self._replace_session(payload["conversationId"], record)
        policy = self.root / ".etg/agent_policy.md"
        policy.write_text(policy.read_text() + "\nRefreshed policy.\n")

        self.assertTrue(handle_antigravity_hook(self.root, "pre-invocation", payload)["injectSteps"])
        self.assertNotIn("start_filesystem_observation", self._session(payload["conversationId"]))
        self.assertEqual(handle_antigravity_hook(self.root, "stop", payload)["decision"], "continue")

    def test_missing_current_observation_keeps_handoff_reminder(self):
        self._preexisting_drift()
        payload = {"conversationId": "current-unknown"}
        handle_antigravity_hook(self.root, "pre-invocation", payload)
        status = active_change_status(self.root)
        status.pop("filesystem_observation")
        with patch("entigram.antigravity_hooks.active_change_status", return_value=status):
            self.assertEqual(handle_antigravity_hook(self.root, "stop", payload)["decision"], "continue")

    def test_unavailable_observation_cannot_silently_allow_stop(self):
        self._preexisting_drift()
        payload = {"conversationId": "unavailable-current"}
        handle_antigravity_hook(self.root, "pre-invocation", payload)
        with patch("entigram.antigravity_hooks.active_change_status", side_effect=OSError("Unavailable")):
            result = handle_antigravity_hook(self.root, "stop", payload)
        self.assertEqual(result["decision"], "continue")
        self.assertIn("could not verify", result["reason"])

    def test_unavailable_start_stays_unknown_when_observation_recovers(self):
        self._preexisting_drift()
        payload = {"conversationId": "unavailable-start"}
        with patch("entigram.antigravity_hooks.active_change_status", side_effect=OSError("Unavailable")):
            handle_antigravity_hook(self.root, "pre-invocation", payload)
        self.assertNotIn("start_filesystem_observation", self._session(payload["conversationId"]))
        self.assertEqual(handle_antigravity_hook(self.root, "stop", payload)["decision"], "continue")

    def test_reminder_persistence_failure_cannot_silently_allow_stop(self):
        payload = {"conversationId": "unavailable-reminder"}
        handle_antigravity_hook(self.root, "pre-invocation", payload)
        (self.root / "work.py").write_text("answer = 42\n")
        with patch("entigram.antigravity_hooks._update_session", side_effect=OSError("Unavailable")):
            self.assertEqual(handle_antigravity_hook(self.root, "stop", payload)["decision"], "continue")


if __name__ == "__main__":
    unittest.main()
