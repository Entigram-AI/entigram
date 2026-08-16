import unittest
from unittest.mock import patch, MagicMock
import os
import sys
from pathlib import Path
from entigram.cli_runner.etg_cli import main
from entigram.antigravity_hooks import install_antigravity_hooks

class TestHeadlessModeling(unittest.TestCase):
    def setUp(self):
        self.test_dir = Path("test_headless_ws")
        self.test_dir.mkdir(exist_ok=True)
        (self.test_dir / ".etg").mkdir(exist_ok=True)
        with open(self.test_dir / ".etg" / "entigram.yaml", "w") as f:
            f.write(
                "cli_engine: Antigravity\n"
                "agent_governance:\n"
                "  active_agents: [antigravity]\n"
            )
        install_antigravity_hooks(self.test_dir)
        
        self.draft_path = self.test_dir / "draft_schema.lds"
        with open(self.draft_path, "w") as f:
            f.write("ENTITY: Existing\nATTRIBUTES:\n  - .id (UUID)\n")

    def tearDown(self):
        import shutil
        if self.test_dir.exists():
            shutil.rmtree(self.test_dir)

    @patch("subprocess.run")
    @patch("shutil.which")
    def test_model_command_append(self, mock_which, mock_run):
        mock_which.return_value = "/usr/local/bin/agy"
        
        # Mock successful agy run output
        mock_run.return_value = MagicMock(
            stdout="ENTITY: Library\nATTRIBUTES:\n  - .id (UUID)\n  - name (String)\n\nRELATIONSHIPS:\n- Library (1) [MUST] --- [MAY] (MANY) Book",
            returncode=0
        )

        # Simulate CLI arguments
        test_args = [
            "etg", "model", "A library with books", 
            "--dir", str(self.test_dir), 
            "--append"
        ]
        
        with patch.object(sys, 'argv', test_args):
            main()

        # Verify agy was called headlessly (with input=prompt)
        # Call index 0 is likely the first agy check or similar, let's check all calls
        found_agy_call = False
        for call in mock_run.call_args_list:
            args, kwargs = call
            if args[0][0] == "agy" and "input" in kwargs:
                found_agy_call = True
                self.assertIn("A library with books", kwargs["input"])
                break
        self.assertTrue(found_agy_call)

        # Verify draft_schema.lds was updated
        with open(self.draft_path, "r") as f:
            content = f.read()
            self.assertIn("ENTITY: Library", content)
            self.assertIn("RELATIONSHIPS:", content)
            self.assertIn("Book", content)

    @patch("subprocess.run")
    @patch("shutil.which")
    def test_model_command_repairs_invalid_payload(self, mock_which, mock_run):
        mock_which.return_value = "/usr/local/bin/agy"
        mock_run.side_effect = [
            MagicMock(stdout="This is not an LDS schema.", returncode=0),
            MagicMock(
                stdout="ENTITY: Library\nATTRIBUTES:\n  - .id (UUID)\n  - name (String)\n",
                returncode=0,
            ),
        ]

        test_args = [
            "etg", "model", "A library with books",
            "--dir", str(self.test_dir),
            "--append",
        ]

        with patch.object(sys, 'argv', test_args):
            main()

        self.assertEqual(mock_run.call_count, 2)
        second_prompt = mock_run.call_args_list[1].kwargs["input"]
        self.assertIn("HALT_EVENT", second_prompt)
        self.assertIn("NO_ENTITIES", second_prompt)

        with open(self.draft_path, "r") as f:
            content = f.read()
            self.assertIn("ENTITY: Library", content)
            self.assertNotIn("This is not an LDS schema.", content)

    @patch("subprocess.run")
    @patch("shutil.which")
    def test_model_command_escalates_after_repair_limit(self, mock_which, mock_run):
        mock_which.return_value = "/usr/local/bin/agy"
        mock_run.side_effect = [
            MagicMock(stdout="This is not an LDS schema.", returncode=0),
            MagicMock(stdout="Still not a schema.", returncode=0),
        ]

        test_args = [
            "etg", "model", "A library with books",
            "--dir", str(self.test_dir),
            "--append",
            "--max-repair-attempts", "1",
        ]

        with patch.object(sys, 'argv', test_args):
            with self.assertRaises(SystemExit) as ctx:
                main()

        self.assertEqual(ctx.exception.code, 1)
        self.assertEqual(mock_run.call_count, 2)
        second_prompt = mock_run.call_args_list[1].kwargs["input"]
        self.assertIn("HALT_EVENT", second_prompt)
        self.assertIn("NO_ENTITIES", second_prompt)

        with open(self.draft_path, "r") as f:
            content = f.read()
            self.assertNotIn("Still not a schema.", content)

if __name__ == "__main__":
    unittest.main()
