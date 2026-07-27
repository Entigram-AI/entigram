import unittest
import ast
import os
import sys
import shutil
import tempfile
import json
import yaml
import sqlite3
from io import StringIO
from pathlib import Path
from unittest.mock import patch
from entigram.cli_runner.etg_cli import main

class TestCLIIntegration(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.old_cwd = os.getcwd()
        os.chdir(self.test_dir)
        self.old_stdout = sys.stdout
        sys.stdout = StringIO()

    def tearDown(self):
        os.chdir(self.old_cwd)
        shutil.rmtree(self.test_dir)
        sys.stdout = self.old_stdout

    def run_cli(self, args):
        sys.stdout = StringIO()
        with patch.object(sys, 'argv', ['etg'] + args):
            try:
                main()
                return True, sys.stdout.getvalue()
            except SystemExit as e:
                return e.code == 0, sys.stdout.getvalue()
            except Exception as e:
                return False, str(e)

    def run_executable(self, executable, args=None):
        sys.stdout = StringIO()
        with patch.object(sys, 'argv', [executable] + (args or [])):
            try:
                main()
                return True, sys.stdout.getvalue()
            except SystemExit as e:
                return e.code == 0, sys.stdout.getvalue()
            except Exception as e:
                return False, str(e)

    def test_init_command(self):
        # Test init without --dir (requires Y/n)
        with patch('builtins.input', return_value='y'):
            success, output = self.run_cli(['init'])
            self.assertTrue(success)
            self.assertIn("Workspace initialized", output)
            self.assertTrue(os.path.exists(".etg/entigram.yaml"))
            with open(".etg/entigram.yaml", "r") as f:
                manifest = yaml.safe_load(f)
            self.assertEqual(manifest["workspace_schema_version"], 1)
            self.assertNotIn("entigram_version", manifest)
            self.assertEqual(manifest["schema_paths"], ["schema.lds"])
            self.assertTrue(manifest["state_ledger"].endswith(".etg/state.db"))
            policy = Path(".etg/agent_policy.md")
            self.assertTrue(policy.exists())
            self.assertIn("Run `hydrate`", policy.read_text())
            agent_files = [
                Path("AGENTS.md"),
                Path("CLAUDE.md"),
                Path("AGY.md"),
                Path("OLLAMA.md"),
                Path("AGENT_INSTRUCTIONS.md"),
            ]
            self.assertTrue(any(".etg/agent_policy.md" in p.read_text() for p in agent_files if p.exists()))

    def test_version_flag(self):
        success, output = self.run_cli(['--version'])
        self.assertTrue(success)
        import re
        from pathlib import Path
        pyproject = (Path(__file__).parent.parent / "pyproject.toml").read_text()
        version = re.search(r'version = "(.*?)"', pyproject).group(1)
        self.assertIn(f"etg {version}", output)

    def test_hydrate_executable_alias_runs_hydrate_command(self):
        self.run_cli(['init', '--dir', '.', '--force'])

        success, output = self.run_executable('hydrate', ['--compact'])

        self.assertTrue(success)
        self.assertIn("--- ENTIGRAM HYDRATION SEQUENCE ---", output)
        self.assertIn('"workspace_schema_version"', output)
        self.assertIn('"agent_policy"', output)
        self.assertIn("Run `hydrate`", output)

    def test_cli_module_does_not_import_yaml_or_injector_at_module_load(self):
        source = (Path(__file__).parent.parent / "entigram" / "cli_runner" / "etg_cli.py").read_text()
        tree = ast.parse(source)
        module_imports = [node for node in tree.body if isinstance(node, (ast.Import, ast.ImportFrom))]

        imported_modules = []
        for node in module_imports:
            if isinstance(node, ast.Import):
                imported_modules.extend(alias.name for alias in node.names)
            elif node.module:
                imported_modules.append(node.module)

        self.assertNotIn("yaml", imported_modules)
        self.assertNotIn("entigram.injector", imported_modules)

    def test_agent_instructions_command_keeps_hydrate_first(self):
        success, output = self.run_cli(['agent', 'instructions'])

        self.assertTrue(success)
        self.assertIn("Start every repository session with:\n   hydrate", output)
        self.assertIn("etg broker preflight --file <path>", output)
        self.assertIn("etg broker handoff", output)

    def test_broker_preflight_reports_schema_risk_as_json(self):
        self.run_cli(['init', '--dir', '.', '--force'])

        success, output = self.run_cli(['broker', 'preflight', '--file', 'schema.lds', '--json'])

        self.assertTrue(success)
        preflight = json.loads(output)
        self.assertEqual(preflight["file"], "schema.lds")
        self.assertEqual(preflight["risk"], "high")
        self.assertTrue(preflight["requires_impact"])
        self.assertTrue(preflight["requires_handoff"])

    def test_broker_handoff_runs_without_make(self):
        self.run_cli(['init', '--dir', '.', '--force'])

        success, output = self.run_cli(['broker', 'handoff'])

        self.assertTrue(success)
        self.assertIn("Step 1/4: broker guard", output)
        self.assertIn("Step 4/4: broker status", output)
        self.assertIn("Delivery status: current", output)

    def test_config_command(self):
        # First initialize
        self.run_cli(['init', '--dir', '.', '--force'])
        
        # Test config --list
        success, output = self.run_cli(['config', '--list'])
        self.assertTrue(success)
        self.assertIn("cli_engine", output)

        # Test config --engine (Catch the NameError fix)
        success, output = self.run_cli(['config', '--engine', 'Claude Code'])
        self.assertTrue(success)
        self.assertIn("engine updated to: Claude Code", output)

    def test_build_command(self):
        # Initialize
        self.run_cli(['init', '--dir', '.', '--force'])
        
        # Create a dummy Schema
        with open("schema.lds", "w") as f:
            f.write("""
ENTITY: Test
ATTRIBUTES:
  - id (String, PK)
""")
            
        with patch('builtins.input', return_value='y'):
            success, output = self.run_cli(['build', '--format', 'sql'])
            self.assertTrue(success)
            self.assertIn("CREATE TABLE", output)

    def test_broker_export_audit_command(self):
        self.run_cli(['init', '--dir', '.', '--force'])
        with open("schema.lds", "w") as f:
            f.write("""
ENTITY: Test
ATTRIBUTES:
  - id (String, PK)
""")

        self.run_cli(['broker', 'deliver'])
        success, output = self.run_cli(['broker', 'export-audit', '--out', 'audit.json'])

        self.assertTrue(success)
        self.assertIn("Audit bundle", output)
        self.assertIn("SHA-256", output)
        self.assertIn("Signature: ed25519", output)
        self.assertIn("Signing key:", output)
        self.assertTrue(Path("audit.json").exists())

    def test_interview_command_init_check(self):
        # Test interview without init (should fail gracefully)
        # Manually remove .etg if it exists from previous tests
        if os.path.exists(".etg"):
            shutil.rmtree(".etg")
        if os.path.exists("interview_prompt.md"):
            os.remove("interview_prompt.md")

        with patch('builtins.input', return_value='y'):
            success, output = self.run_cli(['interview'])
            self.assertFalse(success)
            self.assertIn("Run 'etg init' first.", output)

    @patch('entigram.cli_runner.etg_cli.launch_agent')
    def test_interview_command_success(self, mock_launch):
        # Initialize
        self.run_cli(['init', '--dir', '.', '--force'])
        mock_launch.return_value = (True, "Mock Launch")
        
        with patch('builtins.input', return_value='y'):
            success, output = self.run_cli(['interview'])
            self.assertTrue(success)
            self.assertIn("Starting Autonomous Modeler Interview", output)
            mock_launch.assert_called_once()

    @patch('entigram.cli_runner.etg_cli.launch_agent')
    def test_agent_command_headless(self, mock_launch):
        self.run_cli(['init', '--dir', '.', '--force'])
        mock_launch.return_value = (True, "Mock Launch")
        
        with patch('builtins.input', return_value='y'):
            success, output = self.run_cli(['agent', '--headless'])
            self.assertTrue(success)
            self.assertIn("Launching AI Agent", output)
            # Check that launch_agent was called with headless=True
            args, kwargs = mock_launch.call_args
            self.assertTrue(kwargs.get('headless'))

    @patch('entigram.cli_runner.etg_cli.launch_agent')
    def test_interview_command_headless(self, mock_launch):
        self.run_cli(['init', '--dir', '.', '--force'])
        mock_launch.return_value = (True, "Mock Launch")
        
        with patch('builtins.input', return_value='y'):
            success, output = self.run_cli(['interview', '--headless'])
            self.assertTrue(success)
            self.assertIn("Starting Autonomous Modeler Interview", output)
            # Check that launch_agent was called with headless=True
            args, kwargs = mock_launch.call_args
            self.assertTrue(kwargs.get('headless'))

    def test_discover_command_empty_db(self):
        # Create an empty DB
        db_path = "empty.db"
        open(db_path, 'a').close()
        
        success, output = self.run_cli(['discover', '--db', db_path])
        self.assertTrue(success)
        self.assertEqual(output.strip(), "")

    def test_discover_command_accepts_csv_source_adapter(self):
        Path("orders.csv").write_text(
            "id,total,created_at\n"
            "ord-1,12.50,2026-07-01\n"
        )

        success, output = self.run_cli([
            'discover',
            '--source', 'csv',
            '--path', 'orders.csv',
            '--domain', 'Order',
            '--metadata',
        ])

        self.assertTrue(success)
        self.assertIn("Discovered by Entigram adapter: csv", output)
        self.assertIn("ENTITY: Order", output)
        self.assertIn("- total (Decimal, MUST)", output)

    def test_discover_command_can_emit_structured_report(self):
        Path("accounts.json").write_text('[{"id":"acct-1","owner":"Alice"}]')

        success, output = self.run_cli([
            'discover',
            '--source', 'json',
            '--path', 'accounts.json',
            '--report-json',
        ])

        self.assertTrue(success)
        self.assertIn('"adapter": "json"', output)
        self.assertIn('"trusted": false', output)
        self.assertIn('"findings":', output)

    def test_discover_command_reports_review_findings_when_writing_schema(self):
        conn = sqlite3.connect("legacy.db")
        with conn:
            conn.execute("CREATE TABLE customers (name TEXT, email TEXT);")
        conn.close()

        success, output = self.run_cli([
            'discover',
            '--source', 'sqlite',
            '--path', 'legacy.db',
            '--out', 'draft_schema.lds',
        ])

        self.assertTrue(success)
        self.assertTrue(Path("draft_schema.lds").exists())
        self.assertIn("Discovery review findings", output)
        self.assertIn("NO_PRIMARY_KEY", output)

    def test_package_suggest_reads_catalog(self):
        Path("standard_package_catalog.json").write_text("""
{
  "packages": [
    {
      "name": "@entigram/salesforce",
      "title": "Salesforce",
      "description": "Salesforce describe metadata discovery",
      "tags": ["crm", "salesforce"],
      "source_kinds": ["salesforce-describe"],
      "adapters": ["salesforce-describe"]
    }
  ]
}
""")

        success, output = self.run_cli([
            'package',
            'suggest',
            '--query', 'crm describe',
        ])

        self.assertTrue(success)
        self.assertIn("@entigram/salesforce", output)
        self.assertIn("salesforce-describe", output)

    def test_package_audit_requires_provenance_metadata(self):
        Path("standard_package_catalog.json").write_text("""
{
  "packages": [
    {
      "name": "@entigram/salesforce",
      "title": "Salesforce",
      "description": "Salesforce describe metadata discovery",
      "tags": ["crm", "salesforce"],
      "source_kinds": ["salesforce-describe"],
      "adapters": ["salesforce-describe"],
      "adapter_module": "@entigram/salesforce/source_adapter.py"
    }
  ]
}
""")

        success, output = self.run_cli([
            'package',
            'audit',
        ])

        self.assertFalse(success)
        self.assertIn("license", output)
        self.assertIn("provenance", output)

    def test_package_audit_accepts_complete_catalog_metadata(self):
        Path("standard_package_catalog.json").write_text("""
{
  "packages": [
    {
      "name": "@entigram/salesforce",
      "title": "Salesforce",
      "description": "Salesforce describe metadata discovery",
      "tags": ["crm", "salesforce"],
      "source_kinds": ["salesforce-describe"],
      "adapters": ["salesforce-describe"],
      "adapter_module": "@entigram/salesforce/source_adapter.py",
      "license": {"spdx": "Apache-2.0", "notice_required": true},
      "publisher": {"name": "Entigram", "namespace": "@entigram"},
      "provenance": {
        "source_repository": "https://github.com/entigram/entigram-standard-packages",
        "package_path": "@entigram/salesforce",
        "release_channel": "standard",
        "signed": true
      },
      "certification": {
        "status": "community",
        "compatibility": "entigram>=1.7",
        "test_evidence": ["mock-endpoint"],
        "trademark_use": "nominative"
      }
    }
  ]
}
""")

        success, output = self.run_cli([
            'package',
            'audit',
        ])

        self.assertTrue(success)
        self.assertIn("Package catalog audit passed", output)

    def test_package_signing_commands_support_catalog_audit(self):
        package_dir = Path("@entigram/demo")
        package_dir.mkdir(parents=True)
        (package_dir / "schema.lds").write_text("ENTITY: Demo\nATTRIBUTES:\n  - id (String, PK)\n")
        (package_dir / "source_adapter.py").write_text("ADAPTER_NAME = 'demo'\n")
        Path("standard_package_catalog.json").write_text("""
{
  "packages": [
    {
      "name": "@entigram/demo",
      "title": "Demo",
      "description": "Demo package",
      "tags": ["demo"],
      "source_kinds": ["demo-source"],
      "adapters": ["demo-source"],
      "adapter_module": "@entigram/demo/source_adapter.py",
      "license": {"spdx": "Apache-2.0", "notice_required": true},
      "publisher": {"name": "Entigram", "namespace": "@entigram"},
      "provenance": {
        "source_repository": "https://github.com/entigram/entigram-standard-packages",
        "package_path": "@entigram/demo",
        "release_channel": "standard",
        "signed": true
      },
      "certification": {
        "status": "community",
        "compatibility": "entigram>=1.7",
        "test_evidence": ["mock-endpoint"],
        "trademark_use": "nominative"
      }
    }
  ]
}
""")

        success, output = self.run_cli([
            'package',
            'sign',
            '--package', str(package_dir),
            '--catalog', 'standard_package_catalog.json',
            '--key', 'keys/package.pem',
        ])

        self.assertTrue(success, output)
        self.assertTrue((package_dir / "package.manifest.json").exists())
        self.assertTrue((package_dir / "package.manifest.sig").exists())

        success, output = self.run_cli(['package', 'verify', '--package', str(package_dir)])
        self.assertTrue(success, output)
        self.assertIn("Package verification passed", output)

        success, output = self.run_cli([
            'package',
            'audit',
            '--catalog', 'standard_package_catalog.json',
            '--verify-signatures',
            '--packages-root', '.',
        ])
        self.assertTrue(success, output)
        self.assertIn("Package catalog audit passed", output)

        success, output = self.run_cli([
            'package',
            'sign-catalog',
            '--catalog', 'standard_package_catalog.json',
            '--key', 'keys/package.pem',
        ])
        self.assertTrue(success, output)
        self.assertTrue(Path("standard_package_catalog.json.sig").exists())

        success, output = self.run_cli([
            'package',
            'verify-catalog',
            '--catalog', 'standard_package_catalog.json',
        ])
        self.assertTrue(success, output)
        self.assertIn("Catalog verification passed", output)

    @patch('entigram.cli_runner.etg_cli.launch_ui')
    def test_no_command_prints_help_without_launching_ui(self, mock_launch_ui):
        success, output = self.run_cli([])
        self.assertTrue(success)
        self.assertIn("Entigram Headless Compiler CLI", output)
        mock_launch_ui.assert_not_called()

    @patch('entigram.cli_runner.etg_cli.importlib.util.find_spec', return_value=None)
    def test_ui_command_reports_missing_streamlit(self, _mock_find_spec):
        success, output = self.run_cli(['ui'])
        self.assertFalse(success)
        self.assertIn("Streamlit is not installed", output)
        self.assertIn("headless by default", output)
        self.assertIn("pipx install 'entigram-ai[ui]'", output)
        self.assertIn("pipx inject entigram-ai streamlit", output)

if __name__ == "__main__":
    unittest.main()
