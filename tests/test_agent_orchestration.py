import os
import shutil
import sys
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from entigram.cli_runner.etg_cli import main
from entigram.sqlite_ledger.manager import LedgerManager


class TestAgentOrchestrationLedger(unittest.TestCase):
    def setUp(self):
        self.ledger = LedgerManager(":memory:")

    def tearDown(self):
        self.ledger.close()

    def test_register_agent_and_assign_safe_task(self):
        self.assertTrue(self.ledger.record_agent(
            "continuation-agent",
            agent_class="continuation",
            reliability_score=0.45,
            capability_scores={"test_run": 0.7},
            allowed_task_classes=["read_only", "test_run"],
        ))
        self.assertTrue(self.ledger.enqueue_agent_task(
            "task-tests",
            "Run focused tests",
            "test_run",
            risk_level="read_only",
        ))

        result = self.ledger.assign_agent_task("task-tests", "continuation-agent")

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "Assigned")
        task = self.ledger.get_agent_task("task-tests")
        self.assertEqual(task["assigned_agent_id"], "continuation-agent")

    def test_high_risk_assignment_rejected_and_recorded_as_conflict(self):
        self.ledger.record_agent(
            "weak-agent",
            agent_class="continuation",
            reliability_score=0.4,
            allowed_task_classes=["*"],
        )
        self.ledger.enqueue_agent_task(
            "task-prod",
            "Force push release branch",
            "git_history",
            risk_level="high_risk",
        )

        result = self.ledger.assign_agent_task("task-prod", "weak-agent")

        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "CAPABILITY_SCORE_TOO_LOW")
        self.assertTrue(result["serious_conflict"])
        conflicts = self.ledger.get_pending_conflicts()
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0]["entity_type"], "Entigram_Agent_Task")
        self.assertIn("agent-assignment:task-prod:weak-agent", conflicts[0]["conflict_id"])

    def test_hibernation_checkpoint_round_trips_resume_plan(self):
        plan = self.ledger.record_agent_hibernation(
            "codex-strong",
            run_id="run-1",
            remaining_tokens=900,
            token_threshold=1200,
            refresh_window_end="2026-07-05T18:00:00-05:00",
            resume_after="2026-07-05T18:05:00-05:00",
            checkpoint_summary="Tests are passing; next step is handoff.",
            next_action="Run make handoff.",
            pending_task_ids=["task-handoff"],
        )

        resume = self.ledger.get_resume_plan(agent_id="codex-strong")

        self.assertEqual(resume["hibernate_id"], plan["hibernate_id"])
        self.assertEqual(resume["remaining_tokens"], 900)
        self.assertEqual(resume["pending_task_ids"], ["task-handoff"])
        self.assertTrue(self.ledger.mark_hibernation_resumed(plan["hibernate_id"]))
        self.assertIsNone(self.ledger.get_resume_plan(agent_id="codex-strong"))

    def test_scoped_task_lifecycle_records_visibility_and_review(self):
        self.ledger.record_agent(
            "codex-local",
            agent_class="strong",
            reliability_score=0.9,
            capability_scores={"household_review": 0.9, "test_run": 0.9},
            allowed_task_classes=["household_review", "test_run"],
        )
        requested = self.ledger.request_agent_task(
            "task-supply-review",
            "Review a requested supply deletion",
            "household_review",
            entity_id="household",
            workspace_id="home-dashboard",
            requested_by="agent:mail-intake",
            idempotency_key="mail-message-123:supply-review",
            risk_level="low_risk",
        )
        self.assertTrue(requested["ok"])
        self.assertTrue(requested["created"])
        duplicate = self.ledger.request_agent_task(
            "ignored-duplicate-id", "Ignored", "household_review",
            entity_id="household", workspace_id="home-dashboard",
            requested_by="agent:mail-intake", idempotency_key="mail-message-123:supply-review",
        )
        self.assertTrue(duplicate["ok"])
        self.assertFalse(duplicate["created"])
        self.assertEqual(duplicate["task"]["task_id"], "task-supply-review")

        claimed = self.ledger.claim_agent_task("task-supply-review", "codex-local")
        self.assertTrue(claimed["ok"])
        self.assertEqual(claimed["task"]["status"], "Claimed")
        self.assertFalse(self.ledger.claim_agent_task("task-supply-review", "codex-local")["ok"])
        heartbeated = self.ledger.heartbeat_agent_task(
            "task-supply-review", "codex-local", summary="Checking linked maintenance protocol."
        )
        self.assertTrue(heartbeated["ok"])
        self.assertEqual(heartbeated["task"]["status"], "Running")
        review = self.ledger.request_task_review(
            "task-supply-review", "codex-local", "The supply is referenced by a maintenance protocol."
        )
        self.assertTrue(review["ok"])
        self.assertEqual(review["task"]["status"], "NeedsReview")
        self.assertEqual(review["task"]["approval_status"], "Pending")
        self.assertEqual(
            [event["event_type"] for event in self.ledger.get_agent_task_events("task-supply-review")],
            ["requested", "claimed", "heartbeat", "needs_review"],
        )

        self.ledger.request_agent_task(
            "task-focused-tests", "Run focused tests", "test_run",
            entity_id="entigram-ai", workspace_id="entigram", requested_by="user:founder",
            idempotency_key="task-focused-tests-v1", risk_level="read_only",
        )
        self.assertTrue(self.ledger.claim_agent_task("task-focused-tests", "codex-local")["ok"])
        completed = self.ledger.complete_agent_task("task-focused-tests", "codex-local", "Focused tests passed.")
        self.assertTrue(completed["ok"])
        self.assertEqual(completed["task"]["status"], "Completed")
        self.assertEqual(completed["task"]["result_summary"], "Focused tests passed.")


class TestAgentOrchestrationCLI(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.old_cwd = os.getcwd()
        self.old_stdout = sys.stdout
        os.chdir(self.test_dir)
        sys.stdout = StringIO()

    def tearDown(self):
        os.chdir(self.old_cwd)
        shutil.rmtree(self.test_dir)
        sys.stdout = self.old_stdout

    def run_cli(self, args):
        sys.stdout = StringIO()
        with patch.object(sys, "argv", ["etg"] + args):
            try:
                main()
                return True, sys.stdout.getvalue()
            except SystemExit as exc:
                return exc.code == 0, sys.stdout.getvalue()

    def test_broker_agent_task_and_hibernate_commands(self):
        success, _ = self.run_cli(["init", "--dir", ".", "--force"])
        self.assertTrue(success)

        success, output = self.run_cli([
            "broker",
            "agent-register",
            "--agent",
            "codex-strong",
            "--score",
            "0.9",
            "--capability",
            "schema_change=0.9",
        ])
        self.assertTrue(success)
        self.assertIn("Agent registered", output)

        success, output = self.run_cli([
            "broker",
            "task-enqueue",
            "--id",
            "schema-task",
            "--title",
            "Update governed schema",
            "--type",
            "schema_change",
            "--risk",
            "high_risk",
        ])
        self.assertTrue(success)
        self.assertIn("Task queued", output)

        success, output = self.run_cli([
            "broker",
            "task-assign",
            "--id",
            "schema-task",
            "--agent",
            "codex-strong",
        ])
        self.assertTrue(success)
        self.assertIn("Assigned schema-task", output)

        success, output = self.run_cli([
            "broker",
            "hibernate",
            "--agent",
            "codex-strong",
            "--remaining-tokens",
            "1000",
            "--threshold",
            "1200",
            "--summary",
            "Ready to resume after refresh.",
            "--next-action",
            "Run broker status.",
            "--pending-task",
            "schema-task",
        ])
        self.assertTrue(success)
        self.assertIn("Hibernation checkpoint recorded", output)

        success, output = self.run_cli(["broker", "resume", "--agent", "codex-strong"])
        self.assertTrue(success)
        self.assertIn("Resume checkpoint", output)
        self.assertIn("Run broker status.", output)

    def test_broker_task_lifecycle_commands(self):
        success, _ = self.run_cli(["init", "--dir", ".", "--force"])
        self.assertTrue(success)
        success, _ = self.run_cli([
            "broker", "agent-register", "--agent", "codex-local", "--score", "0.9",
            "--capability", "test_run=0.9", "--allow", "test_run",
        ])
        self.assertTrue(success)
        success, output = self.run_cli([
            "broker", "task-request", "--id", "task-cli", "--entity", "entigram-ai",
            "--workspace", "entigram", "--requested-by", "user:founder",
            "--idempotency-key", "task-cli-v1", "--title", "Run focused tests",
            "--type", "test_run", "--risk", "read_only",
        ])
        self.assertTrue(success)
        self.assertIn("Task requested", output)
        success, output = self.run_cli(["broker", "task-claim", "--id", "task-cli", "--agent", "codex-local"])
        self.assertTrue(success)
        self.assertIn("Claimed task-cli", output)
        success, output = self.run_cli([
            "broker", "task-review", "--id", "task-cli", "--actor", "codex-local",
            "--summary", "The request conflicts with the workspace maintenance policy.",
        ])
        self.assertTrue(success)
        self.assertIn("needs operator review", output)
        success, output = self.run_cli(["broker", "task-events", "--id", "task-cli"])
        self.assertTrue(success)
        self.assertIn("needs_review", output)


if __name__ == "__main__":
    unittest.main()
