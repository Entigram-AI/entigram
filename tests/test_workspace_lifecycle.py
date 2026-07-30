import io
import json
import stat
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

import entigram.workspace_lifecycle as lifecycle_module
from entigram.cli_runner.etg_cli import get_hydration_vector, main
from entigram.injector import inject_entigram_manifest
from entigram.mcp_service import EntigramMCPService
from entigram.sqlite_ledger.manager import LedgerManager
from entigram.sqlite_ledger.paths import resolve_ledger_path
from entigram.usage import (
    MCP_TOOL_DECLARATIONS,
    build_usage_report,
    estimate_tokens,
    record_workspace_usage,
)
from entigram.workspace_lifecycle import (
    ENTIGRAM_END,
    ENTIGRAM_START,
    WorkspaceLifecycleError,
    eject_workspace,
    pause_workspace,
    plan_eject,
    resume_workspace,
    workspace_state,
)


SCHEMA = """
ENTITY: SecretEntity {
  id UUID PK
  name String
}
"""


class TestWorkspaceLifecycle(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.assertTrue(inject_entigram_manifest(str(self.root), ["Entigram Schemas"], "Codex"))
        (self.root / "schema.lds").write_text(SCHEMA)

    def tearDown(self):
        self.temp_dir.cleanup()

    def run_cli(self, args):
        stdout = io.StringIO()
        stderr = io.StringIO()
        exit_code = 0
        with patch("sys.argv", ["etg"] + args), patch("sys.stdout", stdout), patch("sys.stderr", stderr):
            try:
                main()
            except SystemExit as exc:
                exit_code = exc.code if isinstance(exc.code, int) else 1
        return exit_code, stdout.getvalue(), stderr.getvalue()

    def test_new_and_legacy_manifests_are_active(self):
        manifest_path = self.root / ".etg" / "entigram.yaml"
        manifest = yaml.safe_load(manifest_path.read_text())
        self.assertEqual(manifest["lifecycle"]["state"], "active")
        self.assertEqual(workspace_state(self.root), "active")

        manifest.pop("lifecycle")
        manifest_path.write_text(yaml.safe_dump(manifest, sort_keys=False))
        self.assertEqual(workspace_state(self.root), "active")

    def test_estimator_and_session_boundary_are_deterministic(self):
        self.assertEqual(estimate_tokens(0), 0)
        self.assertEqual(estimate_tokens(1), 1)
        self.assertEqual(estimate_tokens(4), 1)
        self.assertEqual(estimate_tokens(5), 2)

        record_workspace_usage(
            self.root,
            operation="init",
            surface="cli",
            input_characters=10,
            output_characters=20,
        )
        record_workspace_usage(
            self.root,
            operation="hydrate",
            surface="cli",
            input_characters=40,
            output_characters=80,
        )
        record_workspace_usage(
            self.root,
            operation="etg_get_schemas",
            surface="mcp",
            input_characters=8,
            output_characters=8,
        )

        ledger = LedgerManager(str(resolve_ledger_path(str(self.root))))
        try:
            summary = ledger.get_usage_summary()
        finally:
            ledger.close()

        self.assertEqual(summary["all_time"]["event_count"], 3)
        self.assertEqual(summary["all_time"]["estimated_total_tokens"], 42)
        self.assertEqual(summary["session"]["event_count"], 2)
        self.assertEqual(summary["session"]["estimated_total_tokens"], 34)
        self.assertIsNotNone(summary["session_boundary"])

    def test_usage_ledger_never_persists_raw_content(self):
        secret = "never-store-this-prompt-or-response"
        ledger = LedgerManager(str(resolve_ledger_path(str(self.root))))
        try:
            ledger.record_usage_event(
                operation="test",
                surface="cli",
                input_characters=len(secret),
                output_characters=len(secret),
                estimated_input_tokens=estimate_tokens(secret),
                estimated_output_tokens=estimate_tokens(secret),
                metadata={"prompt": secret, "result": "ok"},
            )
            conn = ledger._get_connection()
            try:
                columns = {
                    row[1]
                    for row in conn.execute("PRAGMA table_info(usage_events)").fetchall()
                }
                metadata = conn.execute(
                    "SELECT metadata FROM usage_events ORDER BY id DESC LIMIT 1"
                ).fetchone()[0]
            finally:
                conn.close()
        finally:
            ledger.close()

        self.assertNotIn("prompt", columns)
        self.assertNotIn("response", columns)
        self.assertNotIn(secret, metadata)
        self.assertNotIn(secret.encode(), resolve_ledger_path(str(self.root)).read_bytes())

    def test_pause_compacts_context_blocks_commands_and_mcp(self):
        original_policy = (self.root / ".etg" / "agent_policy.md").read_text()
        original_agent = (self.root / "AGENTS.md").read_text()

        result = pause_workspace(self.root, reason="test")
        repeated = pause_workspace(self.root)

        self.assertTrue(result["changed"])
        self.assertFalse(repeated["changed"])
        self.assertEqual(workspace_state(self.root), "paused")
        self.assertLess(
            len((self.root / ".etg" / "agent_policy.md").read_text()),
            len(original_policy),
        )
        self.assertLess(len((self.root / "AGENTS.md").read_text()), len(original_agent))

        hydration = get_hydration_vector(self.root, full=True)
        self.assertIn("WORKSPACE_PAUSED", hydration)
        self.assertNotIn("SecretEntity", hydration)

        exit_code, output, _ = self.run_cli(
            ["broker", "--dir", str(self.root), "status"]
        )
        self.assertEqual(exit_code, 2)
        self.assertIn("workspace governance is paused", output)

        mcp_result = json.loads(EntigramMCPService(str(self.root)).get_schemas())
        self.assertFalse(mcp_result["ok"])
        self.assertEqual(mcp_result["error"]["code"], "WORKSPACE_PAUSED")

    def test_pause_write_failure_rolls_back_owned_context(self):
        policy_path = self.root / ".etg" / "agent_policy.md"
        agent_path = self.root / "AGENTS.md"
        original_policy = policy_path.read_text()
        original_agent = agent_path.read_text()
        original_write = lifecycle_module._atomic_write_text
        failed = False

        def fail_manifest_once(path, content, **kwargs):
            nonlocal failed
            if path.name == "entigram.yaml" and not failed:
                failed = True
                raise OSError("simulated manifest failure")
            return original_write(path, content, **kwargs)

        with patch(
            "entigram.workspace_lifecycle._atomic_write_text",
            side_effect=fail_manifest_once,
        ):
            with self.assertRaises(WorkspaceLifecycleError) as context:
                pause_workspace(self.root)

        self.assertEqual(context.exception.code, "PAUSE_FAILED")
        self.assertEqual(workspace_state(self.root), "active")
        self.assertEqual(policy_path.read_text(), original_policy)
        self.assertEqual(agent_path.read_text(), original_agent)
        self.assertFalse((self.root / ".etg" / "lifecycle" / "pause-backup.json").exists())

    def test_resume_restores_owned_content_and_preserves_user_edits(self):
        policy_path = self.root / ".etg" / "agent_policy.md"
        agent_path = self.root / "AGENTS.md"
        original_policy = policy_path.read_text()
        original_block = _marker_blocks(agent_path.read_text())[0]

        pause_workspace(self.root)
        agent_path.write_text(agent_path.read_text() + "\nUser note added while paused.\n")
        result = resume_workspace(self.root)

        self.assertTrue(result["changed"])
        self.assertEqual(workspace_state(self.root), "active")
        self.assertEqual(policy_path.read_text(), original_policy)
        self.assertEqual(_marker_blocks(agent_path.read_text()), [original_block])
        self.assertIn("User note added while paused.", agent_path.read_text())
        self.assertFalse((self.root / ".etg" / "lifecycle" / "pause-backup.json").exists())
        self.assertFalse(resume_workspace(self.root)["changed"])

    def test_resume_recovers_interrupted_pause_before_manifest_flip(self):
        policy_path = self.root / ".etg" / "agent_policy.md"
        original_policy = policy_path.read_text()
        pause_workspace(self.root)
        backup_path = self.root / ".etg" / "lifecycle" / "pause-backup.json"
        backup = json.loads(backup_path.read_text())
        manifest_path = self.root / ".etg" / "entigram.yaml"
        manifest_path.write_text(backup["manifest"]["original_content"])

        result = resume_workspace(self.root)

        self.assertTrue(result["recovered_interrupted_transition"])
        self.assertEqual(workspace_state(self.root), "active")
        self.assertEqual(policy_path.read_text(), original_policy)
        self.assertFalse(backup_path.exists())

    def test_resume_refuses_owned_conflicts_and_force_archives_them(self):
        pause_workspace(self.root)
        policy_path = self.root / ".etg" / "agent_policy.md"
        policy_path.write_text("operator changed paused policy\n")

        with self.assertRaises(WorkspaceLifecycleError) as context:
            resume_workspace(self.root)
        self.assertEqual(context.exception.code, "PAUSED_CONTEXT_CHANGED")
        self.assertEqual(workspace_state(self.root), "paused")

        result = resume_workspace(self.root, force=True)
        conflict_path = self.root / result["conflict_archive"] / ".etg" / "agent_policy.md"
        self.assertTrue(conflict_path.is_file())
        self.assertEqual(conflict_path.read_text(), "operator changed paused policy\n")
        self.assertEqual(workspace_state(self.root), "active")

    def test_usage_json_reports_static_observed_and_optional_percentage(self):
        record_workspace_usage(
            self.root,
            operation="hydrate",
            surface="cli",
            input_characters=4,
            output_characters=36,
        )
        vectors = {
            "compact": get_hydration_vector(self.root, compact=True),
            "default": get_hydration_vector(self.root),
            "full": get_hydration_vector(self.root, full=True),
        }
        report = build_usage_report(
            self.root,
            hydration_vectors=vectors,
            total_tokens=100,
        )

        self.assertEqual(report["estimator"]["name"], "heuristic_chars_div_4_v1")
        self.assertEqual(report["observed"]["session"]["estimated_total_tokens"], 10)
        self.assertEqual(report["attribution"]["estimated_percent"], 10.0)
        self.assertEqual(report["footprint"]["mcp_tool_declarations"]["count"], 4)
        self.assertEqual(
            {tool["name"] for tool in MCP_TOOL_DECLARATIONS},
            {
                "etg_get_schemas",
                "etg_get_impact",
                "etg_propose_alignment",
                "etg_log_conflict",
            },
        )

    def test_cli_usage_json_suppresses_hydration_diagnostics(self):
        def noisy_vector(*args, **kwargs):
            print("internal warden diagnostic")
            return '{"vector":"measured"}'

        with patch(
            "entigram.cli_runner.etg_cli.get_hydration_vector",
            side_effect=noisy_vector,
        ):
            exit_code, output, error = self.run_cli(
                ["usage", "--dir", str(self.root), "--json"]
            )

        self.assertEqual(exit_code, 0)
        self.assertEqual(error, "")
        payload = json.loads(output)
        self.assertTrue(payload["ok"])
        self.assertNotIn("internal warden diagnostic", output)

    def test_mcp_success_and_error_calls_are_observed(self):
        service = EntigramMCPService(str(self.root))
        self.assertTrue(json.loads(service.get_schemas())["ok"])
        invalid = json.loads(service.propose_alignment("{}"))
        self.assertFalse(invalid["ok"])

        ledger = LedgerManager(str(resolve_ledger_path(str(self.root))))
        conn = ledger._get_connection()
        try:
            events = conn.execute(
                "SELECT operation, surface, metadata FROM usage_events ORDER BY id"
            ).fetchall()
        finally:
            conn.close()
            ledger.close()

        self.assertEqual(
            [(row[0], row[1]) for row in events],
            [
                ("etg_get_schemas", "mcp"),
                ("etg_propose_alignment", "mcp"),
            ],
        )
        self.assertEqual(
            json.loads(events[1][2])["error_code"],
            "MISSING_FIELD",
        )

    def test_eject_dry_run_and_archive_preserve_project_content(self):
        schema_content = (self.root / "schema.lds").read_text()
        agent_path = self.root / "AGENTS.md"
        agent_path.write_text("User-owned instructions.\n\n" + agent_path.read_text())
        archive = self.root / "workspace-entigram.tar.gz"
        record_workspace_usage(
            self.root,
            operation="hydrate",
            surface="cli",
            input_characters=4,
            output_characters=8,
        )

        plan = plan_eject(self.root, archive=archive)
        self.assertTrue(plan["dry_run"])
        self.assertTrue((self.root / ".etg").is_dir())

        result = eject_workspace(self.root, archive=archive)

        self.assertTrue(archive.is_file())
        self.assertEqual(stat.S_IMODE(archive.stat().st_mode), 0o600)
        self.assertFalse((self.root / ".etg").exists())
        self.assertEqual((self.root / "schema.lds").read_text(), schema_content)
        self.assertEqual(agent_path.read_text().strip(), "User-owned instructions.")
        self.assertEqual(result["state"], "ejected")
        with tarfile.open(archive, "r:gz") as tar:
            names = set(tar.getnames())
        self.assertIn(".etg/entigram.yaml", names)
        self.assertIn(".etg/state.db", names)
        self.assertFalse(any(name.endswith(("-wal", "-shm", "-journal")) for name in names))
        self.assertIn("entigram-eject-manifest.json", names)

    def test_eject_validation_failure_leaves_workspace_attached(self):
        archive = self.root / "failed.tar.gz"
        original_agent = (self.root / "AGENTS.md").read_text()
        failure = WorkspaceLifecycleError("ARCHIVE_VALIDATION_FAILED", "invalid")

        with patch(
            "entigram.workspace_lifecycle._validate_eject_archive",
            side_effect=failure,
        ):
            with self.assertRaises(WorkspaceLifecycleError):
                eject_workspace(self.root, archive=archive)

        self.assertTrue((self.root / ".etg" / "entigram.yaml").is_file())
        self.assertEqual((self.root / "AGENTS.md").read_text(), original_agent)

    def test_eject_rejects_unsafe_or_existing_archive_paths(self):
        inside = self.root / ".etg" / "archive.tar.gz"
        with self.assertRaises(WorkspaceLifecycleError) as context:
            plan_eject(self.root, archive=inside)
        self.assertEqual(context.exception.code, "INVALID_ARCHIVE_PATH")

        existing = self.root / "existing.tar.gz"
        existing.write_text("do not overwrite")
        with self.assertRaises(WorkspaceLifecycleError) as context:
            plan_eject(self.root, archive=existing)
        self.assertEqual(context.exception.code, "ARCHIVE_EXISTS")
        self.assertEqual(existing.read_text(), "do not overwrite")


def _marker_blocks(content):
    start = content.index(ENTIGRAM_START)
    end = content.index(ENTIGRAM_END, start) + len(ENTIGRAM_END)
    return [content[start:end]]


if __name__ == "__main__":
    unittest.main()
