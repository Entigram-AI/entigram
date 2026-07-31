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

class TestFederatedRouter(unittest.TestCase):
    def setUp(self):
        self.test_dir = Path("test_workspace_router")
        if self.test_dir.exists():
            shutil.rmtree(self.test_dir)
        self.test_dir.mkdir()

        # 1. Setup a workspace with two domains: TestBanking and TestPF
        packages = ["TestBanking", "TestPF"]
        inject_entigram_manifest(str(self.test_dir), packages, "Antigravity")
        
        # 2. Create package directories and Schema files
        for pkg in packages:
            pkg_dir = self.test_dir / "packages" / pkg
            pkg_dir.mkdir(parents=True, exist_ok=True)
            if pkg == "TestBanking":
                schema_content = """
                ENTITY Account {
                    id UUID PK
                    balance Decimal
                    owner String
                }
                """
            elif pkg == "TestPF":
                schema_content = """
                ENTITY SpendingLimit {
                    id UUID PK
                    max_amount Decimal
                    account_id UUID
                }
                """
            (pkg_dir / "schema.lds").write_text(schema_content)
        declare_schema_paths(
            self.test_dir,
            [self.test_dir / "packages" / pkg / "schema.lds" for pkg in packages],
        )

        # 3. Inject SQLite databases
        injector = DomainSQLiteInjector(str(self.test_dir))
        injector.inject_all_active()

        # 4. Populate some data
        # Banking domain gets Account
        banking_db = self.test_dir / ".etg" / "states" / "TestBanking.db"
        conn = sqlite3.connect(banking_db)
        conn.execute("INSERT INTO accounts (id, balance, owner) VALUES ('acc-1', 1000.50, 'Alice')")
        conn.commit()
        conn.close()

        # PersonalFinance domain gets SpendingLimit
        pf_db = self.test_dir / ".etg" / "states" / "TestPF.db"
        conn = sqlite3.connect(pf_db)
        conn.execute("INSERT INTO spendinglimits (id, max_amount, account_id) VALUES ('lim-1', 500.00, 'acc-1')")
        conn.commit()
        conn.close()

        self.router = FederatedRouter(str(self.test_dir))
        
        # New Enforcement: Authorize the alignment before querying cross-domain
        self.broker = EntigramBroker(str(self.test_dir))
        self.broker.ledger.record_alignment(
            source_domain="TestPF",
            target_domain="TestBanking",
            source_concept="SpendingLimit.account_id",
            target_concept="Account.id",
            confidence=1.0,
            rationale="Test authorization",
            lifecycle_status="verified",
            evidence_type="human_review",
            verified=True,
            verified_by="test",
        )

    def tearDown(self):
        if self.test_dir.exists():
            shutil.rmtree(self.test_dir)

    def test_federated_query_cross_domain(self):
        # Query that spans both domains: Account (Banking) -> SpendingLimit (PersonalFinance)
        # We need to define how they are linked. 
        # In this case, SpendingLimit has account_id.
        graphql = """
        {
          Account {
            owner
            SpendingLimit {
              max_amount
            }
          }
        }
        """
        results = self.router.execute(graphql)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['owner'], 'Alice')
        self.assertEqual(results[0]['SpendingLimit']['max_amount'], 500.00)

    def test_federated_query_multi_row(self):
        # Add another account and spending limit
        banking_db = self.test_dir / ".etg" / "states" / "TestBanking.db"
        conn = sqlite3.connect(banking_db)
        conn.execute("INSERT INTO accounts (id, balance, owner) VALUES ('acc-2', 5000.00, 'Bob')")
        conn.commit()
        conn.close()

        pf_db = self.test_dir / ".etg" / "states" / "TestPF.db"
        conn = sqlite3.connect(pf_db)
        conn.execute("INSERT INTO spendinglimits (id, max_amount, account_id) VALUES ('lim-2', 1500.00, 'acc-2')")
        conn.commit()
        conn.close()

        graphql = """
        {
          Account {
            owner
            SpendingLimit {
              max_amount
            }
          }
        }
        """
        results = self.router.execute(graphql)
        self.assertEqual(len(results), 2)
        
        # Verify Alice
        alice = next(r for r in results if r['owner'] == 'Alice')
        self.assertEqual(alice['SpendingLimit']['max_amount'], 500.00)
        
        # Verify Bob
        bob = next(r for r in results if r['owner'] == 'Bob')
        self.assertEqual(bob['SpendingLimit']['max_amount'], 1500.00)

    def test_unlisted_package_schema_is_not_authoritative(self):
        inactive = self.test_dir / ".etg" / "packages" / "Inactive"
        inactive.mkdir(parents=True)
        (inactive / "schema.lds").write_text(
            "ENTITY Undeclared { id UUID PK }"
        )

        with FederatedRouter(str(self.test_dir)) as router:
            self.assertNotIn("Undeclared", router.entities)

if __name__ == "__main__":
    unittest.main()
