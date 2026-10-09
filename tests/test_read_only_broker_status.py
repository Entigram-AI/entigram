import sqlite3
from pathlib import Path

import pytest

from entigram.broker import EntigramBroker
from entigram.cli_runner.etg_cli import _track_cli_operation
from entigram.sqlite_ledger.manager import LedgerManager


def test_read_only_ledger_does_not_create_files_or_allow_writes(tmp_path: Path):
    ledger_path = tmp_path / ".etg" / "state.db"
    LedgerManager(str(ledger_path)).close()
    before = ledger_path.stat().st_mtime_ns
    before_files = set(ledger_path.parent.iterdir())

    ledger = LedgerManager(str(ledger_path), read_only=True)
    assert ledger.get_latest_snapshot() is None
    connection = ledger._get_connection()
    try:
        with pytest.raises(sqlite3.OperationalError):
            connection.execute("CREATE TABLE forbidden (id INTEGER)")
    finally:
        connection.close()
    ledger.close()

    assert ledger_path.stat().st_mtime_ns == before
    assert set(ledger_path.parent.iterdir()) == before_files


def test_read_only_ledger_refuses_missing_database(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        LedgerManager(str(tmp_path / ".etg" / "state.db"), read_only=True)
    assert not (tmp_path / ".etg").exists()


def test_read_only_ledger_refuses_uncheckpointed_wal(tmp_path: Path):
    ledger_path = tmp_path / "state.db"
    LedgerManager(str(ledger_path)).close()
    writer = sqlite3.connect(ledger_path)
    writer.execute("PRAGMA journal_mode=WAL")
    writer.execute("PRAGMA wal_autocheckpoint=0")
    writer.execute("CREATE TABLE pending (id INTEGER)")
    writer.commit()
    assert (tmp_path / "state.db-wal").stat().st_size > 0
    try:
        with pytest.raises(sqlite3.OperationalError, match="active WAL"):
            LedgerManager(str(ledger_path), read_only=True)
    finally:
        writer.close()


def test_read_only_broker_does_not_update_baseline(tmp_path: Path):
    (tmp_path / ".etg").mkdir()
    (tmp_path / ".etg" / "entigram.yaml").write_text("cli_engine: codex\n")
    LedgerManager(str(tmp_path / ".etg" / "state.db")).close()
    baseline = tmp_path / ".etg" / "lifecycle" / "check-in-baseline.json"

    with EntigramBroker(str(tmp_path), read_only=True) as broker:
        result = broker.delivery_status()

    assert result["status"] == "no_snapshot"
    assert result["snapshot"] is None
    assert not baseline.exists()


def test_read_only_broker_status_does_not_record_cli_usage():
    assert not _track_cli_operation(
        "broker", ["etg", "broker", "--dir", "/workspace", "status", "--read-only"]
    )
    assert _track_cli_operation(
        "broker", ["etg", "broker", "--dir", "/workspace", "status"]
    )
