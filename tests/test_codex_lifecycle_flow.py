import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

from entigram.injector import inject_entigram_manifest
from entigram.task_context import task_context_status


SCHEMA = """/* Authoritative Schema */
ENTITY: WorkItem {
  id UUID PK
  title String MUST
}

EXPECTATION: WorkItem Integrity {
  developer_expectation: WorkItems must have a title.
  implementation_rule: title must not be empty.
  validation_check: python3 -c "print('ok')"
  proof: python3 -c "print('ok')"
}
"""


class TestCodexLifecycleFlow(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name).resolve()
        # Initialize Git repo
        subprocess.run(["git", "init"], cwd=self.root, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test Agent"], cwd=self.root, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "agent@entigram.ai"], cwd=self.root, check=True, capture_output=True)

        # Initialize Entigram workspace with Codex
        self.assertTrue(inject_entigram_manifest(str(self.root), ["Entigram Schemas"], "Codex"))
        (self.root / "schema.lds").write_text(SCHEMA)
        
        # Configure active agents
        manifest_path = self.root / ".etg" / "entigram.yaml"
        manifest = yaml.safe_load(manifest_path.read_text()) or {}
        governance = manifest.setdefault("agent_governance", {})
        governance["active_agents"] = ["antigravity", "codex"]
        manifest.setdefault("governance", {})["require_task_prepare"] = True
        manifest_path.write_text(yaml.safe_dump(manifest, sort_keys=False))

        # Initial commit
        subprocess.run(["git", "add", "."], cwd=self.root, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "initial"], cwd=self.root, check=True, capture_output=True)

        # Install agent hooks for codex
        self._run_cli(["agent-hooks", "install", "--dir", str(self.root), "--engine", "codex"])

    def tearDown(self):
        self.temp_dir.cleanup()

    def _run_cli(self, args, *, input_text=None):
        env = dict(os.environ)
        env["ENTIGRAM_AGENT_RUNTIME"] = "codex"
        cmd = [sys.executable, "-m", "entigram.cli_runner.etg_cli"] + args
        proc = subprocess.run(
            cmd,
            input=input_text,
            capture_output=True,
            text=True,
            cwd=str(self.root),
            env=env,
        )
        return proc

    def test_exact_codex_lifecycle_sequence_cross_process(self):
        """Verify the exact Codex sequence across separate processes:
        prepare -> handoff/status -> prepare again -> read command -> pause -> read/edit within budget -> resume -> hydrate.
        """
        # 1. prepare
        p1 = self._run_cli([
            "task", "prepare",
            "--id", "task-1",
            "--description", "First task working on schema.lds",
        ])
        self.assertEqual(p1.returncode, 0, f"task prepare failed: {p1.stderr}")
        self.assertIn("Task prepared: task-1", p1.stdout)
        status_after_prep1 = task_context_status(self.root)
        self.assertEqual(status_after_prep1["status"], "prepared")

        # 2. handoff/status
        p2 = self._run_cli(["broker", "handoff"])
        self.assertEqual(p2.returncode, 0, f"broker handoff failed: {p2.stderr}")
        self.assertIn("Delivery status: current", p2.stdout)

        p2_status = self._run_cli(["broker", "status"])
        self.assertEqual(p2_status.returncode, 0, f"broker status failed: {p2_status.stderr}")
        self.assertIn("Delivery status: current", p2_status.stdout)

        # Critical: successful handoff must NOT make just-prepared task stale!
        status_after_handoff = task_context_status(self.root)
        self.assertEqual(
            status_after_handoff["status"],
            "prepared",
            "Successful handoff/status must preserve prepared task context",
        )

        # 3. prepare again (start next task)
        p3 = self._run_cli([
            "task", "prepare",
            "--id", "task-2",
            "--description", "Second task starting new work",
        ])
        self.assertEqual(p3.returncode, 0, f"task prepare 2 failed: {p3.stderr}")
        status_after_prep2 = task_context_status(self.root)
        self.assertEqual(status_after_prep2["status"], "prepared")

        # 4. read command
        # Codex hook pre-tool-use for read-only commands: pwd, git status
        read_payload_pwd = json.dumps({
            "session_id": "codex-test-session",
            "tool_name": "Bash",
            "tool_input": {"cmd": "pwd"},
        })
        p4_pwd = self._run_cli([
            "agent-hook",
            "--dir", str(self.root),
            "--runtime", "codex",
            "--event", "pre-tool-use",
        ], input_text=read_payload_pwd)
        self.assertEqual(p4_pwd.returncode, 0)
        resp_pwd = json.loads(p4_pwd.stdout.strip() or "{}")
        self.assertEqual(resp_pwd, {}, "Read-only command `pwd` must be allowed without gating")

        read_payload_git = json.dumps({
            "session_id": "codex-test-session",
            "tool_name": "Bash",
            "tool_input": {"cmd": "git status"},
        })
        p4_git = self._run_cli([
            "agent-hook",
            "--dir", str(self.root),
            "--runtime", "codex",
            "--event", "pre-tool-use",
        ], input_text=read_payload_git)
        self.assertEqual(p4_git.returncode, 0)
        resp_git = json.loads(p4_git.stdout.strip() or "{}")
        self.assertEqual(resp_git, {}, "Read-only command `git status` must be allowed")

        # 5. pause
        # Hook must allow etg pause
        pause_payload = json.dumps({
            "session_id": "codex-test-session",
            "tool_name": "Bash",
            "tool_input": {"cmd": "etg pause"},
        })
        p5_hook = self._run_cli([
            "agent-hook",
            "--dir", str(self.root),
            "--runtime", "codex",
            "--event", "pre-tool-use",
        ], input_text=pause_payload)
        self.assertEqual(p5_hook.returncode, 0)
        self.assertEqual(json.loads(p5_hook.stdout.strip() or "{}"), {})

        # Run etg pause
        p5_pause = self._run_cli(["pause"])
        self.assertEqual(p5_pause.returncode, 0, f"pause failed: {p5_pause.stderr}")
        self.assertIn("paused", p5_pause.stdout.lower())

        # Verify pause-status reads persisted state
        p5_status = self._run_cli(["pause-status"])
        self.assertEqual(p5_status.returncode, 0)
        self.assertIn("Within paused change budget", p5_status.stdout)

        # 6. read/edit within budget
        # Read command while paused is allowed
        p6_read = self._run_cli([
            "agent-hook",
            "--dir", str(self.root),
            "--runtime", "codex",
            "--event", "pre-tool-use",
        ], input_text=read_payload_pwd)
        self.assertEqual(json.loads(p6_read.stdout.strip() or "{}"), {})

        # Edit within budget
        (self.root / "file1.txt").write_text("edit 1\n")
        p6_status = self._run_cli(["pause-status"])
        self.assertEqual(p6_status.returncode, 0)
        self.assertIn("1/5", p6_status.stdout)

        # 7. resume
        resume_payload = json.dumps({
            "session_id": "codex-test-session",
            "tool_name": "Bash",
            "tool_input": {"cmd": "etg resume"},
        })
        p7_hook = self._run_cli([
            "agent-hook",
            "--dir", str(self.root),
            "--runtime", "codex",
            "--event", "pre-tool-use",
        ], input_text=resume_payload)
        self.assertEqual(json.loads(p7_hook.stdout.strip() or "{}"), {})

        p7_resume = self._run_cli(["resume"])
        self.assertEqual(p7_resume.returncode, 0, f"resume failed: {p7_resume.stderr}")
        self.assertIn("resumed", p7_resume.stdout.lower())

        # 8. hydrate
        p8_hydrate = self._run_cli(["hydrate"])
        self.assertEqual(p8_hydrate.returncode, 0, f"hydrate failed: {p8_hydrate.stderr}")
        self.assertIn("ENTIGRAM_BOOT", p8_hydrate.stdout)
