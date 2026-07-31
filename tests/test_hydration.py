import unittest
import os
import shutil
import tempfile
import json
import yaml
from pathlib import Path
from entigram.injector import inject_entigram_manifest
from entigram.sqlite_ledger.manager import LedgerManager
from entigram.sqlite_ledger.paths import resolve_ledger_path
from entigram.cli_runner.etg_cli import main
from unittest.mock import patch
import sys

class TestHydration(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.old_cwd = os.getcwd()
        os.chdir(self.test_dir)
        
        # Initialize an Entigram workspace
        inject_entigram_manifest(self.test_dir, ["Banking"], "Codex")
        
        # Add some data to the ledger
        self.ledger_path = resolve_ledger_path(self.test_dir)
        manager = LedgerManager(str(self.ledger_path))
        conn = manager._get_connection()
        conn.execute("INSERT INTO semantic_alignments (source_domain, target_domain, source_concept, target_concept, status) VALUES (?, ?, ?, ?, ?)",
                     ("DomainA", "DomainB", "User", "Account", "approved"))
        conn.execute("INSERT INTO human_resolutions (conflict_id, entity_type, resolved_state, rationale) VALUES (?, ?, ?, ?)",
                     ("C1", "Supplier", "active", "verified"))
        conn.commit()
        conn.close()
        
        # Add a Schema
        with open(Path(self.test_dir) / "schema.lds", "w") as f:
            f.write("ENTITY: User\n  - name (String)")

    def tearDown(self):
        os.chdir(self.old_cwd)
        shutil.rmtree(self.test_dir)

    def test_hydrate_command(self):
        """Verifies that the hydrate command produces a concise session summary."""
        test_args = ["entigram", "hydrate"]
        with patch.object(sys, 'argv', test_args):
            from io import StringIO
            captured_output = StringIO()
            with patch('sys.stdout', captured_output):
                try:
                    main()
                except SystemExit:
                    pass

            output = captured_output.getvalue()
            self.assertIn("--- ENTIGRAM HYDRATION SEQUENCE ---", output)
            self.assertIn("--- SEQUENCE COMPLETE ---", output)
            self.assertIn("ENTIGRAM_BOOT_SUMMARY", output)
            self.assertNotIn("ENTIGRAM_BOOT_VECTOR", output)
            import re
            from pathlib import Path
            pyproject = (Path(__file__).parent.parent / "pyproject.toml").read_text()
            version = re.search(r'version = "(.*?)"', pyproject).group(1)
            self.assertIn(f'"version": "{version}"', output)
            self.assertIn(f'"package_version": "{version}"', output)
            self.assertIn('"workspace_schema_version": 1', output)
            self.assertIn('"missing_proof_count": 0', output)
            self.assertIn("User", output)
            self.assertIn("hydrate --full", output)

    def test_hydrate_full_preserves_deep_state_vector(self):
        """Verifies that --full keeps the schema, ledger, and delivery details."""
        test_args = ["entigram", "hydrate", "--full"]
        with patch.object(sys, 'argv', test_args):
            from io import StringIO
            captured_output = StringIO()
            with patch('sys.stdout', captured_output):
                try:
                    main()
                except SystemExit:
                    pass

            output = captured_output.getvalue()
            self.assertIn("ENTIGRAM_BOOT_VECTOR", output)
            self.assertIn("DomainA", output)
            self.assertIn("DomainB", output)
            self.assertIn("User", output)
            self.assertIn("active", output)

    def test_default_hydrate_is_smaller_than_full_hydrate(self):
        from io import StringIO

        with patch.object(sys, 'argv', ["entigram", "hydrate"]):
            concise_output = StringIO()
            with patch('sys.stdout', concise_output):
                main()

        with patch.object(sys, 'argv', ["entigram", "hydrate", "--full"]):
            full_output = StringIO()
            with patch('sys.stdout', full_output):
                main()

        self.assertLess(len(concise_output.getvalue()), len(full_output.getvalue()))

    def test_boot_alias(self):
        """Verifies that 'boot' is a valid alias for 'hydrate'."""
        test_args = ["entigram", "boot"]
        with patch.object(sys, 'argv', test_args):
            from io import StringIO
            captured_output = StringIO()
            with patch('sys.stdout', captured_output):
                try:
                    main()
                except SystemExit:
                    pass
            
            output = captured_output.getvalue()
            self.assertIn("--- ENTIGRAM HYDRATION SEQUENCE ---", output)

    def test_hydrate_warns_only_for_declared_missing_artifact_capabilities(self):
        manifest_path = Path(self.test_dir) / ".etg" / "entigram.yaml"
        manifest = yaml.safe_load(manifest_path.read_text())
        manifest["external_artifacts"] = {
            "modalities": ["image"],
            "trust": "untrusted",
            "mode": "advisory",
            "required_capabilities": ["visual-prompt-injection-screening/v1"],
        }
        manifest_path.write_text(yaml.safe_dump(manifest, sort_keys=True))

        from entigram.cli_runner.etg_cli import get_hydration_vector

        output = json.loads(
            get_hydration_vector(Path(self.test_dir)).split("\n", 1)[1].rsplit("\n", 1)[0]
        )
        summary = output["ENTIGRAM_BOOT_SUMMARY"]
        self.assertEqual(summary["assessment_mode"], "advisory")
        self.assertEqual(
            summary["missing_security_capabilities"],
            ["visual-prompt-injection-screening/v1"],
        )
        self.assertEqual(summary["risk_advisories"][0]["code"], "ETG-RISK-MISSING-CAPABILITY")

if __name__ == "__main__":
    unittest.main()
