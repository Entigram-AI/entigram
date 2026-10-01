import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from entigram.broker import EntigramBroker
from entigram.cli_runner.etg_cli import _track_cli_operation
from entigram.sqlite_ledger.manager import LedgerManager


class ReadOnlyBrokerStatusTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temporary_directory.name)

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_read_only_ledger_does_not_create_files_or_allow_writes(self):
        ledger_path = self.workspace / ".etg" / "state.db"
        LedgerManager(str(ledger_path)).close()
        before = ledger_path.stat().st_mtime_ns
        before_files = set(ledger_path.parent.iterdir())

        ledger = LedgerManager(str(ledger_path), read_only=True)
        self.assertIsNone(ledger.get_latest_snapshot())
        connection = ledger._get_connection()
        try:
            with self.assertRaises(sqlite3.OperationalError):
                connection.execute("CREATE TABLE forbidden (id INTEGER)")
        finally:
            connection.close()
        ledger.close()

        self.assertEqual(ledger_path.stat().st_mtime_ns, before)
        self.assertEqual(set(ledger_path.parent.iterdir()), before_files)

    def test_read_only_ledger_refuses_missing_database(self):
        with self.assertRaises(FileNotFoundError):
            LedgerManager(str(self.workspace / ".etg" / "state.db"), read_only=True)
        self.assertFalse((self.workspace / ".etg").exists())

    def test_read_only_ledger_refuses_uncheckpointed_wal(self):
        ledger_path = self.workspace / "state.db"
        LedgerManager(str(ledger_path)).close()
        writer = sqlite3.connect(ledger_path)
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("CREATE TABLE pending (id INTEGER)")
        writer.commit()
        self.assertGreater((self.workspace / "state.db-wal").stat().st_size, 0)
        try:
            with self.assertRaisesRegex(sqlite3.OperationalError, "active WAL"):
                LedgerManager(str(ledger_path), read_only=True)
        finally:
            writer.close()

    def test_read_only_broker_does_not_update_baseline(self):
        (self.workspace / ".etg").mkdir()
        (self.workspace / ".etg" / "entigram.yaml").write_text("cli_engine: codex\n")
        LedgerManager(str(self.workspace / ".etg" / "state.db")).close()
        baseline = self.workspace / ".etg" / "lifecycle" / "check-in-baseline.json"

        with EntigramBroker(str(self.workspace), read_only=True) as broker:
            result = broker.delivery_status()

        self.assertEqual(result["status"], "no_snapshot")
        self.assertIsNone(result["snapshot"])
        self.assertFalse(baseline.exists())

    def test_read_only_broker_status_does_not_record_cli_usage(self):
        self.assertFalse(_track_cli_operation(
            "broker", ["etg", "broker", "--dir", "/workspace", "status", "--read-only"]
        ))
        self.assertTrue(_track_cli_operation(
            "broker", ["etg", "broker", "--dir", "/workspace", "status"]
        ))

    def test_cli_reports_missing_read_only_ledger_without_traceback(self):
        result = subprocess.run(
            [sys.executable, "-m", "entigram.cli_runner.etg_cli", "broker", "--dir",
             str(self.workspace), "status", "--read-only"],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn(
            "Read-only broker status unavailable: SQLite ledger does not exist", result.stderr
        )
        self.assertNotIn("Traceback", result.stderr)
        self.assertFalse((self.workspace / ".etg").exists())
