import csv
import json
import os
import sqlite3
import stat
import sys
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from entigram.cli_runner.agent_builder import generate_agent_boilerplate
from entigram.cli_runner.plugin_builder import generate_plugin_boilerplate
from entigram.cli_runner.etg_cli import main
from entigram.cli_runner.cloudflare_ollama_proxy import main as proxy_main
from entigram.broker import EntigramBroker
from entigram.mcp_server import run_mcp_server
from entigram.panel_bridge import run_panel_bridge
from entigram.project_history import add_project_to_history, get_project_history
from entigram.registry import EntigramRegistry
from entigram.sensing.partner_sensor import PartnerCSVSensor
from entigram.server import run_server
from entigram.sqlite_ledger.manager import LedgerManager


class TestSecurityHardening(unittest.TestCase):
    def test_legacy_graphql_refuses_unauthenticated_non_loopback_bind(self):
        with self.assertRaisesRegex(ValueError, "requires a bearer token"):
            run_server(host="0.0.0.0")

    def test_unauthenticated_services_are_loopback_only(self):
        with self.assertRaisesRegex(ValueError, "SSE transport is restricted"):
            run_mcp_server(transport="sse", host="0.0.0.0")
        with self.assertRaisesRegex(ValueError, "panel bridge is restricted"):
            run_panel_bridge(host="0.0.0.0")
        self.assertEqual(proxy_main(["--host", "0.0.0.0", "--env-file", "missing.env"]), 1)

    def test_legacy_graphql_honors_host_and_configures_auth(self):
        with patch("entigram.server.http.server.ThreadingHTTPServer") as server_cls:
            server_cls.return_value.serve_forever.return_value = None
            run_server(
                host="127.0.0.1",
                port=9191,
                auth_token="secret",
                allowed_origins=["http://127.0.0.1:3000"],
            )

        address, handler = server_cls.call_args.args
        self.assertEqual(address, ("127.0.0.1", 9191))
        self.assertEqual(handler.auth_token, "secret")
        self.assertEqual(handler.allowed_origins, {"http://127.0.0.1:3000"})
        server_cls.return_value.server_close.assert_called_once()

    def test_registry_token_never_enters_git_arguments_or_remote_url(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(
                os.environ,
                {
                    "ENTIGRAM_TOKEN": "super-secret-token",
                    "ENTIGRAM_REGISTRY_CACHE_DIR": directory,
                    "ENTIGRAM_REGISTRY_OFFLINE": "0",
                },
                clear=False,
            ):
                registry = EntigramRegistry(directory)
                with patch("entigram.registry.subprocess.run") as run:
                    registry._fetch_registry("https://github.com/example/packages.git")

        command = run.call_args.args[0]
        self.assertIn("https://github.com/example/packages.git", command)
        self.assertNotIn("super-secret-token", " ".join(command))
        self.assertNotIn("set-url", command)

    def test_cloud_credentials_use_restrictive_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            output = StringIO()
            with (
                patch.object(Path, "home", return_value=Path(directory)),
                patch.object(sys, "argv", ["etg", "cloud", "login"]),
                patch("getpass.getpass", return_value="prompt-token"),
                patch("sys.stdout", output),
            ):
                main()

            credentials = Path(directory) / ".etg" / "credentials"
            self.assertEqual(json.loads(credentials.read_text())["token"], "prompt-token")
            self.assertEqual(stat.S_IMODE(credentials.stat().st_mode), 0o600)

    def test_cloud_sync_fails_instead_of_claiming_upload(self):
        ledger = LedgerManager(":memory:")
        try:
            with patch("sys.stdout", new_callable=StringIO) as output:
                self.assertFalse(ledger.sync_with_cloud("https://example.invalid", "token"))
            self.assertIn("no data was uploaded", output.getvalue())
        finally:
            ledger.close()

    def test_scaffold_names_reject_path_and_code_injection(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                generate_agent_boilerplate("../../escape", directory)
            with self.assertRaises(ValueError):
                generate_plugin_boilerplate("bad-name;exec", directory)

            generate_agent_boilerplate("stripe", directory)
            hook = Path(directory, "stripe_edge", "ledger_hook.py").read_text()
            self.assertIn("EntigramMCPService", hook)
            self.assertNotIn("sqlite3", hook)

    def test_partner_sensor_rejects_unsafe_identifiers_and_quotes_headers(self):
        with tempfile.TemporaryDirectory() as directory:
            csv_path = Path(directory, "partner.csv")
            with csv_path.open("w", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(["id", 'display"name'])
                writer.writerow(["1", "safe"])

            sensor = PartnerCSVSensor(directory)
            with self.assertRaises(ValueError):
                sensor.ingest_csv(str(csv_path), "../../escape", "records")
            with self.assertRaises(ValueError):
                sensor.ingest_csv(str(csv_path), "Partner", 'records; DROP TABLE x')

            self.assertTrue(sensor.ingest_csv(str(csv_path), "Partner", "records"))
            connection = sqlite3.connect(Path(directory, ".etg", "states", "Partner.db"))
            try:
                row = connection.execute('SELECT "display""name" FROM records').fetchone()
            finally:
                connection.close()
            self.assertEqual(row[0], "safe")

    def test_alignment_storage_defaults_to_unverified(self):
        ledger = LedgerManager(":memory:")
        try:
            self.assertTrue(
                ledger.record_alignment("A", "B", "x", "y", 0.9, "unreviewed proposal")
            )
            self.assertEqual(ledger.get_alignments(trusted_only=True), [])
            alignment = ledger.get_alignments(trusted_only=False)[0]
            self.assertFalse(alignment["verified"])
            self.assertEqual(alignment["lifecycle_status"], "proposed")
            foreign_keys = ledger._get_connection().execute("PRAGMA foreign_keys").fetchone()[0]
            self.assertEqual(foreign_keys, 1)
        finally:
            ledger.close()

    def test_alignment_negotiation_confines_inputs_to_workspace(self):
        with tempfile.TemporaryDirectory() as directory:
            broker = EntigramBroker(directory)
            try:
                with self.assertRaisesRegex(ValueError, "must stay within"):
                    broker.negotiate_alignments("/tmp/outside.lds", "/tmp/other.lds")
            finally:
                broker.close()

    def test_project_history_uses_user_data_file_atomically(self):
        import entigram.project_history as history

        with tempfile.TemporaryDirectory() as directory:
            history_path = Path(directory, "config", "projects.json")
            with (
                patch.object(history, "HISTORY_FILE", history_path),
                patch.object(history, "LEGACY_HISTORY_FILE", Path(directory, "missing.json")),
            ):
                add_project_to_history(directory)
                self.assertEqual(get_project_history(), [str(Path(directory).resolve())])
                self.assertEqual(stat.S_IMODE(history_path.stat().st_mode), 0o600)


if __name__ == "__main__":
    unittest.main()
