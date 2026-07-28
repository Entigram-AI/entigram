import unittest
import os
import shutil
import sqlite3
import json
from pathlib import Path
from entigram.federated_router import FederatedRouter
from entigram.broker import EntigramBroker
from entigram.injector import inject_entigram_manifest
from entigram.sqlite_ledger.injector import DomainSQLiteInjector
from tests.workspace_helpers import declare_schema_paths

class TestFederatedRouterAlignments(unittest.TestCase):
    def setUp(self):
        self.test_dir = Path("test_workspace_alignments").absolute()
        if self.test_dir.exists():
            shutil.rmtree(self.test_dir)
        self.test_dir.mkdir(parents=True, exist_ok=True)

        # 1. Setup a workspace with two domains: Banking and PartnerPortal
        packages = ["Banking", "PartnerPortal"]
        inject_entigram_manifest(str(self.test_dir), packages, "Antigravity")
        
        # 2. Create package directories and Schema files
        for pkg in packages:
            pkg_dir = self.test_dir / "packages" / pkg
            pkg_dir.mkdir(parents=True, exist_ok=True)
            if pkg == "Banking":
                schema_content = """
                ENTITY Account {
                    id UUID PK
                    owner String
                }
                """
            elif pkg == "PartnerPortal":
                schema_content = """
                ENTITY Profile {
                    id UUID PK
                    bank_ref UUID
                    bio String
                }
                """
            (pkg_dir / "schema.lds").write_text(schema_content)
        declare_schema_paths(
            self.test_dir,
            [self.test_dir / "packages" / pkg / "schema.lds" for pkg in packages],
        )

        # 3. Inject SQLite databases
        injector = DomainSQLiteInjector(str(self.test_dir))
        # Ensure states dir exists
        (self.test_dir / ".etg" / "states").mkdir(parents=True, exist_ok=True)
        injector.inject_all_active()

        # 4. Populate some data
        banking_db = self.test_dir / ".etg" / "states" / "Banking.db"
        conn = sqlite3.connect(str(banking_db))
        conn.execute("CREATE TABLE IF NOT EXISTS accounts (id TEXT PRIMARY KEY, owner TEXT)")
        conn.execute("INSERT INTO accounts (id, owner) VALUES ('acc-1', 'Alice')")
        conn.commit()
        conn.close()

        partner_db = self.test_dir / ".etg" / "states" / "PartnerPortal.db"
        conn = sqlite3.connect(str(partner_db))
        conn.execute("CREATE TABLE IF NOT EXISTS profiles (id TEXT PRIMARY KEY, bank_ref TEXT, bio TEXT)")
        conn.execute("INSERT INTO profiles (id, bank_ref, bio) VALUES ('prof-1', 'acc-1', 'Loves code')")
        conn.commit()
        conn.close()

        self.router = FederatedRouter(str(self.test_dir))
        self.broker = EntigramBroker(str(self.test_dir))

    def tearDown(self):
        if self.test_dir.exists():
            shutil.rmtree(self.test_dir)

    def test_auto_discovery_records_proposal_without_joining(self):
        # This used to fail because 'bank_ref' does not match 'account_id' naming convention.
        # The router may discover the candidate, but closed-world routing must not trust it
        # until it has explicit verification evidence.
        graphql = """
        {
          Account {
            owner
            Profile {
              bio
            }
          }
        }
        """
        results = self.router.execute(graphql)
        self.assertEqual(len(results), 1)
        self.assertIsNone(results[0]['Profile'], "Unverified discovered alignments must not drive joins")

        alignments = self.broker.ledger.get_alignments()
        self.assertTrue(any(
            aln["source_concept"] == "Profile.bank_ref"
            and aln["target_concept"] == "Account.id"
            and aln["lifecycle_status"] == "proposed"
            and not aln["verified"]
            for aln in alignments
        ))

    def test_join_succeeds_with_alignment(self):
        # Now we authorize an alignment: PartnerPortal.Profile.bank_ref -> Banking.Account.id
        self.broker.authorize_alignment(
            source_domain="PartnerPortal",
            target_domain="Banking",
            source_concept="Profile.bank_ref",
            target_concept="Account.id",
            confidence=1.0,
            rationale="Manual mapping for partner portal integration"
        )
        
        graphql = """
        {
          Account {
            owner
            Profile {
              bio
            }
          }
        }
        """
        results = self.router.execute(graphql)
        self.assertEqual(len(results), 1)
        self.assertIsNotNone(results[0]['Profile'], "Profile should be resolved via alignment")
        self.assertEqual(results[0]['Profile']['bio'], 'Loves code')

if __name__ == "__main__":
    unittest.main()
