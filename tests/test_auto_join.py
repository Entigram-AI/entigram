import unittest
import os
import shutil
import sqlite3
from pathlib import Path
from entigram.federated_router import FederatedRouter
from entigram.broker import EntigramBroker
from entigram.injector import inject_entigram_manifest
from entigram.sqlite_ledger.injector import DomainSQLiteInjector
from tests.workspace_helpers import declare_schema_paths

class TestAutoJoinDiscovery(unittest.TestCase):
    def setUp(self):
        self.test_dir = Path("test_workspace_autojoin")
        if self.test_dir.exists():
            shutil.rmtree(self.test_dir)
        self.test_dir.mkdir()

        # Domains: Customer -> Order
        packages = ["Banking", "SupplyChain"]
        inject_entigram_manifest(str(self.test_dir), packages, "Antigravity")
        
        # Setup Schema with clear but un-mapped FK: Order.customer_ref -> Customer.id
        # Banking Domain
        pkg_dir = self.test_dir / ".etg" / "packages" / "Banking"
        pkg_dir.mkdir(parents=True, exist_ok=True)
        schema = "ENTITY Customer { id UUID PK \n name String }"
        (pkg_dir / "schema.lds").write_text(schema)

        # SupplyChain Domain
        pkg_dir = self.test_dir / ".etg" / "packages" / "SupplyChain"
        pkg_dir.mkdir(parents=True, exist_ok=True)
        schema = "ENTITY Order { id UUID PK \n amount Decimal \n customer_ref UUID }"
        (pkg_dir / "schema.lds").write_text(schema)
        declare_schema_paths(
            self.test_dir,
            [
                self.test_dir / ".etg" / "packages" / "Banking" / "schema.lds",
                self.test_dir / ".etg" / "packages" / "SupplyChain" / "schema.lds",
            ],
        )

        injector = DomainSQLiteInjector(str(self.test_dir))
        injector.inject_all_active()

        # Populate Data
        conn = sqlite3.connect(self.test_dir / ".etg" / "states" / "Banking.db")
        conn.execute("INSERT INTO customers (id, name) VALUES ('c-1', 'Alice')")
        conn.commit()
        conn.close()

        conn = sqlite3.connect(self.test_dir / ".etg" / "states" / "SupplyChain.db")
        conn.execute("INSERT INTO orders (id, amount, customer_ref) VALUES ('o-1', 99.99, 'c-1')")
        conn.commit()
        conn.close()

        self.router = FederatedRouter(str(self.test_dir))
        self.broker = EntigramBroker(str(self.test_dir))

    def tearDown(self):
        if self.test_dir.exists():
            shutil.rmtree(self.test_dir)

    def test_auto_join_discovery_on_query(self):
        # 1. Closed-World Enforcement: The join should be discovered but BLOCKED for execution
        # because it is cross-domain (Banking != SupplyChain) and unverified.
        
        graphql = """
        {
          Customer {
            name
            Order {
              amount
            }
          }
        }
        """
        # Trigger query
        results = self.router.execute(graphql)
        
        self.assertEqual(len(results), 1)
        alice = results[0]
        self.assertEqual(alice['name'], 'Alice')
        
        # Should be None because it's cross-domain and unverified
        self.assertIsNone(alice.get('Order'))
        
        # 2. Verify that an alignment PROPOSAL was recorded in the ledger
        alignments = self.broker.ledger.get_alignments(trusted_only=False)
        found = False
        proposal_id = None
        for aln in alignments:
            if "Order.customer_ref" in aln['source_concept'] or "Order.customer_ref" in aln['target_concept']:
                found = True
                proposal_id = aln['id']
                self.assertIn("Heuristic", aln['rationale'])
        self.assertTrue(found, "Router should have recorded an auto-discovered alignment proposal")

        # 3. Authorize the alignment and re-run
        # We need the full details from the proposal
        proposal = None
        for aln in alignments:
            if aln['id'] == proposal_id:
                proposal = aln
                break
        
        self.broker.authorize_alignment(
            source_domain=proposal['source_domain'],
            target_domain=proposal['target_domain'],
            source_concept=proposal['source_concept'],
            target_concept=proposal['target_concept'],
            confidence=1.0,
            rationale="Human evidence provided"
        )
        
        # Clear router cache
        self.router._join_cache = {}
        
        # Re-run query
        results = self.router.execute(graphql)
        self.assertIsNotNone(results[0].get('Order'))
        self.assertEqual(results[0]['Order']['amount'], 99.99)

if __name__ == "__main__":
    unittest.main()
