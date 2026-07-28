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

class TestFederatedMultiHop(unittest.TestCase):
    def setUp(self):
        self.test_dir = Path("test_workspace_multihop")
        if self.test_dir.exists():
            shutil.rmtree(self.test_dir)
        self.test_dir.mkdir()

        # 3 Domains: PartnerManagement -> BusinessStrategy -> SupplyChain
        # Partner -> Strategic_Goal -> Supplier
        packages = ["PartnerManagement", "BusinessStrategy", "SupplyChain"]
        inject_entigram_manifest(str(self.test_dir), packages, "Antigravity")
        
        # PartnerManagement: Partner(id, name)
        # BusinessStrategy: Strategic_Goal(id, name, partner_id)
        # SupplyChain: Supplier(id, name, goal_id)
        
        for pkg in packages:
            pkg_dir = self.test_dir / "packages" / pkg
            pkg_dir.mkdir(parents=True, exist_ok=True)
            if pkg == "PartnerManagement":
                schema = "ENTITY Partner { id UUID PK \n name String }"
            elif pkg == "BusinessStrategy":
                schema = "ENTITY Strategic_Goal { id UUID PK \n name String \n partner_id UUID }"
            elif pkg == "SupplyChain":
                schema = "ENTITY Supplier { id UUID PK \n name String \n goal_id UUID }"
            (pkg_dir / "schema.lds").write_text(schema)
        declare_schema_paths(
            self.test_dir,
            [self.test_dir / "packages" / pkg / "schema.lds" for pkg in packages],
        )

        injector = DomainSQLiteInjector(str(self.test_dir))
        injector.inject_all_active()

        # Populate Data
        pm_db = self.test_dir / ".etg" / "states" / "PartnerManagement.db"
        conn = sqlite3.connect(pm_db)
        conn.execute("INSERT INTO partners (id, name) VALUES ('p1', 'Global Partner')")
        conn.commit()
        conn.close()

        bs_db = self.test_dir / ".etg" / "states" / "BusinessStrategy.db"
        conn = sqlite3.connect(bs_db)
        conn.execute("INSERT INTO strategic_goals (id, name, partner_id) VALUES ('g1', 'Expansion', 'p1')")
        conn.commit()
        conn.close()

        sc_db = self.test_dir / ".etg" / "states" / "SupplyChain.db"
        conn = sqlite3.connect(sc_db)
        conn.execute("INSERT INTO suppliers (id, name, goal_id) VALUES ('s1', 'Acme Corp', 'g1')")
        conn.commit()
        conn.close()

        self.router = FederatedRouter(str(self.test_dir))
        self.broker = EntigramBroker(str(self.test_dir))

    def tearDown(self):
        if self.test_dir.exists():
            shutil.rmtree(self.test_dir)

    def test_multi_hop_join(self):
        # Authorize alignments for both hops
        # 1. Partner -> Strategic_Goal (Convention works: partner_id, but let's be explicit)
        self.broker.authorize_alignment(
            source_domain="BusinessStrategy",
            target_domain="PartnerManagement",
            source_concept="Strategic_Goal.partner_id",
            target_concept="Partner.id",
            confidence=1.0,
            rationale="Explicit mapping"
        )
        
        # 2. Strategic_Goal -> Supplier (Convention fails: goal_id != strategic_goal_id)
        self.broker.authorize_alignment(
            source_domain="SupplyChain",
            target_domain="BusinessStrategy",
            source_concept="Supplier.goal_id",
            target_concept="Strategic_Goal.id",
            confidence=1.0,
            rationale="Mapping non-standard FK"
        )

        # Query: Partner -> Strategic_Goal -> Supplier
        graphql = """
        {
          Partner {
            name
            Strategic_Goal {
              name
              Supplier {
                name
              }
            }
          }
        }
        """
        results = self.router.execute(graphql)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['name'], 'Global Partner')
        self.assertIsNotNone(results[0]['Strategic_Goal'])
        self.assertEqual(results[0]['Strategic_Goal']['name'], 'Expansion')
        
        # THIS IS WHERE IT IS EXPECTED TO FAIL CURRENTLY
        self.assertIsNotNone(results[0]['Strategic_Goal']['Supplier'], "Supplier should be resolved in multi-hop join")
        self.assertEqual(results[0]['Strategic_Goal']['Supplier']['name'], 'Acme Corp')

if __name__ == "__main__":
    unittest.main()
