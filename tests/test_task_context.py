import json
import os
import shutil
import sys
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from entigram.antigravity_hooks import handle_antigravity_hook
from entigram.injector import inject_entigram_manifest
from entigram.task_context import (
    build_expectation_envelope,
    load_task_context,
    prepare_task,
    task_context_status,
)
from entigram.workspace_lifecycle import establish_active_change_baseline


class TestTaskContext(unittest.TestCase):
    def setUp(self):
        self.old_cwd = os.getcwd()
        self.root = Path(tempfile.mkdtemp())
        os.chdir(self.root)
        self.assertTrue(inject_entigram_manifest(str(self.root), ["Entigram Schemas"], "Antigravity"))
        (self.root / "schema.lds").write_text("ENTITY: WorkItem {\n  id UUID PK\n}\n")
        (self.root / "work.py").write_text("answer = 42\n")
        establish_active_change_baseline(self.root, reason="test_setup")

    def tearDown(self):
        os.chdir(self.old_cwd)
        shutil.rmtree(self.root)

    def test_prepare_records_hydration_and_inventory(self):
        result = prepare_task(
            self.root,
            task_id="issue-123",
            description="Fix work.py and preserve the WorkItem schema.",
            scope=["work.py"],
            agent="antigravity",
            model="test-model",
        )
        context = load_task_context(self.root)
        self.assertTrue(result["ok"])
        self.assertEqual(result["expectation"]["task_id"], "issue-123")
        self.assertEqual(result["expectation"]["context_sha256"], context["context_sha256"])
        self.assertEqual(context["task_id"], "issue-123")
        self.assertIn("work.py", context["referenced_files"])
        self.assertEqual(context["scope"], ["work.py"])
        self.assertEqual(context["schema_entities"], ["WorkItem"])
        self.assertEqual(task_context_status(self.root)["status"], "prepared")
        self.assertTrue(context["hydration"])
        envelope = build_expectation_envelope(context)
        self.assertEqual(envelope["kind"], "entigram.task_expectation")
        self.assertEqual(envelope["scope"], ["work.py"])
        self.assertEqual(envelope["referenced_files"], ["work.py"])
        self.assertTrue(envelope["discovery"]["allowed"])
        self.assertEqual(envelope["trust_boundary"]["model_interpretation"], "proposal_only")
        self.assertIn("semantic_acceptance_criteria_require_agent_or_human_interpretation", envelope["unknowns"])

    def test_required_task_context_blocks_writes_until_prepared(self):
        session = {"conversationId": "conversation-1"}
        handle_antigravity_hook(self.root, "pre-invocation", session)
        write = {
            "conversationId": "conversation-1",
            "toolCall": {
                "name": "write_to_file",
                "args": {"AbsolutePath": str(self.root / "work.py")},
            },
        }
        blocked = handle_antigravity_hook(self.root, "pre-tool-use", write)
        self.assertEqual(blocked["decision"], "deny")
        self.assertIn("task prepare", blocked["reason"])

        prepare_command = {
            "conversationId": "conversation-1",
            "toolCall": {
                "name": "run_command",
                "args": {"CommandLine": "etg task prepare --id issue-123 --description 'Fix work.py'"},
            },
        }
        self.assertEqual(
            handle_antigravity_hook(self.root, "pre-tool-use", prepare_command)["decision"],
            "allow",
        )
        prepare_task(self.root, task_id="issue-123", description="Fix work.py")
        self.assertEqual(handle_antigravity_hook(self.root, "pre-tool-use", write)["decision"], "allow")

    def test_policy_drift_stales_task_context(self):
        prepare_task(self.root, task_id="issue-123", description="Fix work.py")
        policy = self.root / ".etg" / "agent_policy.md"
        policy.write_text(policy.read_text() + "\nchanged\n")
        self.assertEqual(task_context_status(self.root)["status"], "stale")

    def test_cli_task_prepare_emits_json(self):
        from entigram.cli_runner.etg_cli import main

        output = StringIO()
        with patch.object(
            sys,
            "argv",
            [
                "etg", "task", "prepare", "--dir", str(self.root),
                "--id", "issue-123", "--description", "Fix work.py", "--json",
            ],
        ), patch("sys.stdout", output):
            main()
        payload = json.loads(output.getvalue())
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["task"]["task_id"], "issue-123")

    def test_cli_task_context_emits_stable_envelope(self):
        from entigram.cli_runner.etg_cli import main

        prepare_task(
            self.root,
            task_id="issue-123",
            description="Fix work.py",
            scope=["work.py"],
        )
        output = StringIO()
        with patch.object(
            sys,
            "argv",
            ["etg", "task", "context", "--dir", str(self.root), "--json"],
        ), patch("sys.stdout", output):
            main()
        payload = json.loads(output.getvalue())
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["envelope"]["task_id"], "issue-123")
        self.assertEqual(payload["envelope"]["original_prompt"], "Fix work.py")
        self.assertEqual(payload["envelope"]["referenced_files"], ["work.py"])
        self.assertNotIn("authorization", payload["envelope"]["trust_boundary"])

    def test_context_unknowns_do_not_block_read_only_discovery(self):
        context = {
            "task_id": "issue-123",
            "description": "Investigate the failing behavior",
            "description_sha256": "prompt-hash",
            "scope": [],
            "referenced_files": [],
            "dependency_files": [],
            "schema_entities": [],
        }
        envelope = build_expectation_envelope(context)
        self.assertTrue(envelope["discovery"]["allowed"])
        self.assertIn("write_scope_not_explicitly_declared", envelope["unknowns"])
        self.assertIn("no_prompt_file_references_detected", envelope["unknowns"])


if __name__ == "__main__":
    unittest.main()
