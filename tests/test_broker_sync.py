import unittest
import json
import os
import shutil
import sqlite3
from pathlib import Path
from entigram.broker import EntigramBroker

class TestBrokerSync(unittest.TestCase):
    def setUp(self):
        self.test_dir = Path("tests/tmp_sync")
        self.test_dir.mkdir(parents=True, exist_ok=True)
        self.etg_dir = self.test_dir / ".etg"
        self.etg_dir.mkdir(parents=True, exist_ok=True)
        self.states_dir = self.etg_dir / "states"
        self.states_dir.mkdir(parents=True, exist_ok=True)
        
        # Initialize manifest
        self.manifest_path = self.etg_dir / "entigram.yaml"
        with open(self.manifest_path, "w") as f:
            f.write("packages: [DomainA, DomainB]\n")
        
        self.ledger_path = self.etg_dir / "entigram_state.db"
        if self.ledger_path.exists():
            self.ledger_path.unlink()
            
        self.broker = EntigramBroker(str(self.test_dir))
        
        # Setup Aligment
        self.broker.authorize_alignment(
            "DomainA", "DomainB", "balance", "balance", 1.0, "Testing Sync"
        )
        
        # Setup initial conflicting states in SQLite DBs
        self._setup_db("DomainA", "balance", 100)
        self._setup_db("DomainB", "balance", 200)

    def _setup_db(self, domain_name, col_name, val):
        db_path = self.states_dir / f"{domain_name}.db"
        conn = sqlite3.connect(db_path)
        with conn:
            conn.execute(f"CREATE TABLE test_table (id TEXT PRIMARY KEY, {col_name} INTEGER)")
            conn.execute(f"INSERT INTO test_table (id, {col_name}) VALUES ('test_id', {val})")
        conn.close()

    def tearDown(self):
        if self.test_dir.exists():
            shutil.rmtree(self.test_dir)

    def test_sync_resolution(self):
        """Verifies that resolutions are propagated to domain states."""
        # 1. Sense conflict
        conflicts = self.broker.sense_all()
        self.assertEqual(len(conflicts), 1)
        conflict_id = conflicts[0]['id']
        
        # 2. Record human resolution (Resolved to 150)
        self.broker.propose_resolution(conflict_id, "balance", "150", "Human compromise")
        
        # 3. Run Sync
        self.broker.sync_resolutions()
            
        # 4. Verify states are updated in SQLite
        conn_a = sqlite3.connect(self.states_dir / "DomainA.db")
        cur_a = conn_a.cursor()
        cur_a.execute("SELECT balance FROM test_table")
        self.assertEqual(str(cur_a.fetchone()[0]), "150")
        conn_a.close()
            
        conn_b = sqlite3.connect(self.states_dir / "DomainB.db")
        cur_b = conn_b.cursor()
        cur_b.execute("SELECT balance FROM test_table")
        self.assertEqual(str(cur_b.fetchone()[0]), "150")
        conn_b.close()

    def test_sync_updates_only_the_conflict_alignment_and_latest_row(self):
        self.broker.authorize_alignment(
            "DomainC", "DomainD", "balance", "balance", 1.0, "Unrelated alignment"
        )
        self._setup_db("DomainC", "balance", 300)
        self._setup_db("DomainD", "balance", 400)

        for domain, current in (("DomainA", 100), ("DomainB", 200)):
            conn = sqlite3.connect(self.states_dir / f"{domain}.db")
            with conn:
                conn.execute(
                    "INSERT INTO test_table (id, balance) VALUES (?, ?)",
                    ("older", 50),
                )
                conn.execute(
                    "INSERT INTO test_table (id, balance) VALUES (?, ?)",
                    ("current", current),
                )
            conn.close()

        conflicts = self.broker.sense_all()
        conflict_id = conflicts[0]["id"]
        self.broker.propose_resolution(conflict_id, "balance", "150", "Human compromise")
        self.broker.sync_resolutions()

        for domain, expected in (("DomainA", [(100,), (50,), (150,)]), ("DomainB", [(200,), (50,), (150,)])):
            conn = sqlite3.connect(self.states_dir / f"{domain}.db")
            rows = conn.execute("SELECT balance FROM test_table ORDER BY rowid").fetchall()
            self.assertEqual(rows, expected)
            conn.close()

        for domain, expected in (("DomainC", 300), ("DomainD", 400)):
            conn = sqlite3.connect(self.states_dir / f"{domain}.db")
            value = conn.execute("SELECT balance FROM test_table").fetchone()[0]
            self.assertEqual(value, expected)
            conn.close()

if __name__ == "__main__":
    unittest.main()
