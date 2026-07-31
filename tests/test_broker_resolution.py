import unittest
import os
import sqlite3
from pathlib import Path
from entigram.broker import EntigramBroker
from entigram.sqlite_ledger.manager import LedgerManager

class TestBrokerResolution(unittest.TestCase):
    def setUp(self):
        self.test_dir = Path("test_workspace")
        self.test_dir.mkdir(exist_ok=True)
        self.etg_dir = self.test_dir / ".etg"
        self.etg_dir.mkdir(exist_ok=True)
        self.states_dir = self.etg_dir / "states"
        self.states_dir.mkdir(exist_ok=True)
        
        self.db_path = str(self.etg_dir / "entigram_state.db")
        self.broker = EntigramBroker(str(self.test_dir))
        
        # Mock manifest
        with open(self.etg_dir / "entigram.yaml", "w") as f:
            f.write("packages:\n  - DomainA\n  - DomainB\n")

    def _setup_db(self, domain_name, data):
        db_path = self.states_dir / f"{domain_name}.db"
        conn = sqlite3.connect(db_path)
        with conn:
            cols = []
            vals = []
            placeholders = []
            for k, v in data.items():
                cols.append(f"{k} TEXT")
                vals.append(str(v))
                placeholders.append("?")
            col_def = ", ".join(cols)
            conn.execute(f"CREATE TABLE test_entity (id TEXT PRIMARY KEY, {col_def})")
            
            insert_cols = ", ".join(data.keys())
            insert_vals = ", ".join(placeholders)
            conn.execute(f"INSERT INTO test_entity (id, {insert_cols}) VALUES ('test_id', {insert_vals})", vals)
        conn.close()

    def tearDown(self):
        import shutil
        if self.test_dir.exists():
            shutil.rmtree(self.test_dir)

    def test_conflict_detection_and_resolution(self):
        # 1. Setup Alignment
        self.broker.authorize_alignment(
            "DomainA", "DomainB", "val_x", "val_y", 1.0, "Testing alignment",
            validate_schema=False,
        )
        
        # 2. Setup States (Contradictory) in SQLite
        self._setup_db("DomainA", {"val_x": 100})
        self._setup_db("DomainB", {"val_y": 200})
            
        # 3. Sense Conflicts
        conflicts = self.broker.sense_all()
        self.assertEqual(len(conflicts), 1)
        conflict_id = conflicts[0]['id']
        self.assertIn("DomainA", conflicts[0]['proposed_states'])
        self.assertIn("DomainB", conflicts[0]['proposed_states'])
        
        # 4. Resolve Conflict
        success = self.broker.propose_resolution(
            conflict_id, "val_x", "150", "Manual average resolution"
        )
        self.assertTrue(success)
        
        # 5. Verify Resolution in Ledger
        res = self.broker.check_decision(conflict_id)
        self.assertIsNotNone(res)
        self.assertEqual(res['state'], "150")
        
        # 6. Verify Conflict is removed
        pending = self.broker.ledger.get_pending_conflicts()
        self.assertEqual(len(pending), 0)

    def test_missing_state_handling(self):
        # DomainC exists in manifest but has no state file
        with open(self.etg_dir / "entigram.yaml", "w") as f:
            f.write("packages:\n  - DomainA\n  - DomainC\n")
        
        state = self.broker.load_domain_state("DomainC")
        self.assertEqual(state, {})
        
        # Sensing should not crash
        conflicts = self.broker.sense_all()
        self.assertEqual(len(conflicts), 0)

    def test_multiple_conflicts(self):
        with open(self.etg_dir / "entigram.yaml", "w") as f:
            f.write("packages:\n  - DomainA\n  - DomainB\n")
            
        self.broker.authorize_alignment("DomainA", "DomainB", "v1", "v1", 1.0, "Align 1", validate_schema=False)
        self.broker.authorize_alignment("DomainA", "DomainB", "v2", "v2", 1.0, "Align 2", validate_schema=False)
        
        self._setup_db("DomainA", {"v1": 10, "v2": 20})
        self._setup_db("DomainB", {"v1": 11, "v2": 21})
            
        conflicts = self.broker.sense_all()
        self.assertEqual(len(conflicts), 2)
        
    def test_asymmetric_alignment(self):
        # Alignment only from A to B
        self.broker.authorize_alignment("DomainA", "DomainB", "v1", "v1", 1.0, "A->B", validate_schema=False)
        
        self._setup_db("DomainA", {"v1": 100})
        self._setup_db("DomainB", {"v1": 200})
            
        # Sensing from A as source should find conflict
        # Use full table mapping for detect_cross_domain_conflict
        conflicts_a = self.broker.detect_cross_domain_conflict(
            "DomainA", "DomainB", {"test_entity.v1": 100, "v1": 100}, {"test_entity.v1": 200, "v1": 200}
        )
        self.assertEqual(len(conflicts_a), 1)
        
        # Sensing from B as source should NOT find conflict (no alignment B->A)
        conflicts_b = self.broker.detect_cross_domain_conflict(
            "DomainB", "DomainA", {"test_entity.v1": 200, "v1": 200}, {"test_entity.v1": 100, "v1": 100}
        )
        self.assertEqual(len(conflicts_b), 0)

if __name__ == "__main__":
    unittest.main()
