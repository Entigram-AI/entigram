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

class TestPhase3Orchestration(unittest.TestCase):
    def setUp(self):
        self.test_dir = Path("test_workspace_phase3")
        if self.test_dir.exists():
            shutil.rmtree(self.test_dir)
        self.test_dir.mkdir()

        # Domains: Startup (Greenfield) -> VenturePartner -> GlobalSupplier
        packages = ["StartupFounder", "PartnerManagement", "SupplyChain"]
        inject_entigram_manifest(str(self.test_dir), packages, "Antigravity")
        
        # 1. Setup Schema for each domain
        for pkg in packages:
            # Prefer .etg/packages for test-specific overrides
            pkg_dir = self.test_dir / ".etg" / "packages" / pkg
            pkg_dir.mkdir(parents=True, exist_ok=True)
            if pkg == "StartupFounder":
                schema = "ENTITY Strategic_Initiative { id UUID PK \n name String }"
            elif pkg == "PartnerManagement":
                schema = "ENTITY Venture_Partner { id UUID PK \n partner_name String \n initiative_ref UUID }"
            elif pkg == "SupplyChain":
                schema = "ENTITY Global_Supplier { id UUID PK \n supplier_name String \n partner_ref UUID }"
            (pkg_dir / "schema.lds").write_text(schema)
        declare_schema_paths(
            self.test_dir,
            [self.test_dir / ".etg" / "packages" / pkg / "schema.lds" for pkg in packages],
        )

        injector = DomainSQLiteInjector(str(self.test_dir))
        injector.inject_all_active()

        # 2. Populate Data
        # Startup -> VenturePartner -> GlobalSupplier
        conn = sqlite3.connect(self.test_dir / ".etg" / "states" / "StartupFounder.db")
        conn.execute("INSERT INTO strategic_initiatives (id, name) VALUES ('init-1', 'AI Launch')")
        conn.commit()
        conn.close()

        conn = sqlite3.connect(self.test_dir / ".etg" / "states" / "PartnerManagement.db")
        conn.execute("INSERT INTO venture_partners (id, partner_name, initiative_ref) VALUES ('vp-1', 'TechCorp', 'init-1')")
        conn.commit()
        conn.close()

        conn = sqlite3.connect(self.test_dir / ".etg" / "states" / "SupplyChain.db")
        conn.execute("INSERT INTO global_suppliers (id, supplier_name, partner_ref) VALUES ('gs-1', 'Silicon Foundry', 'vp-1')")
        conn.commit()
        conn.close()

        self.router = FederatedRouter(str(self.test_dir))
        self.broker = EntigramBroker(str(self.test_dir))

    def tearDown(self):
        if self.test_dir.exists():
            shutil.rmtree(self.test_dir)

    def test_automated_negotiation_and_recursive_query(self):
        # 1. Automated Negotiation for first hop: Strategic_Initiative -> Venture_Partner
        # initiative_ref (Venture_Partner) vs id (Strategic_Initiative)
        # Negotiator should find this (init-ref vs id might be low score, but let's see)
        # Actually, let's use negotiate-auto logic
        src_schema = str(self.test_dir / ".etg" / "packages" / "PartnerManagement" / "schema.lds")
        tgt_schema = str(self.test_dir / ".etg" / "packages" / "StartupFounder" / "schema.lds")
        
        proposals = self.broker.negotiate_alignments(src_schema, tgt_schema, threshold=0.1) # Low threshold for test
        found_init_mapping = False
        for p in proposals:
            if p['source_concept'] == "Venture_Partner.initiative_ref" and p['target_concept'] == "Strategic_Initiative.id":
                self.broker.authorize_alignment("PartnerManagement", "StartupFounder", p['source_concept'], p['target_concept'], 1.0, "Auto-test")
                found_init_mapping = True
        
        # 2. Automated Negotiation for second hop: Venture_Partner -> Global_Supplier
        src_schema = str(self.test_dir / ".etg" / "packages" / "SupplyChain" / "schema.lds")
        tgt_schema = str(self.test_dir / ".etg" / "packages" / "PartnerManagement" / "schema.lds")
        
        proposals = self.broker.negotiate_alignments(src_schema, tgt_schema, threshold=0.1)
        found_supp_mapping = False
        for p in proposals:
            if p['source_concept'] == "Global_Supplier.partner_ref" and p['target_concept'] == "Venture_Partner.id":
                self.broker.authorize_alignment("SupplyChain", "PartnerManagement", p['source_concept'], p['target_concept'], 1.0, "Auto-test")
                found_supp_mapping = True

        # 3. Execute Recursive Multi-Hop Query
        graphql = """
        {
          Strategic_Initiative {
            name
            Venture_Partner {
              partner_name
              Global_Supplier {
                supplier_name
              }
            }
          }
        }
        """
        results = self.router.execute(graphql)
        
        self.assertEqual(len(results), 1)
        init = results[0]
        self.assertEqual(init['name'], 'AI Launch')
        
        self.assertIsNotNone(init['Venture_Partner'])
        vp = init['Venture_Partner']
        self.assertEqual(vp['partner_name'], 'TechCorp')
        
        self.assertIsNotNone(vp['Global_Supplier'])
        gs = vp['Global_Supplier']
        self.assertEqual(gs['supplier_name'], 'Silicon Foundry')

if __name__ == "__main__":
    unittest.main()
