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

class TestSemanticJoins(unittest.TestCase):
    def setUp(self):
        self.test_dir = Path("test_workspace_semantic")
        if self.test_dir.exists():
            shutil.rmtree(self.test_dir)
        self.test_dir.mkdir()

        # Domains: Internal (SupplyChain) and External (TechCorp)
        packages = ["SupplyChain", "TechCorp"]
        inject_entigram_manifest(str(self.test_dir), packages, "Antigravity")
        
        # SupplyChain: Supplier(id, name, tax_id)
        # TechCorp: Vendor(uid, vendor_name, ein)
        for pkg in packages:
            pkg_dir = self.test_dir / "packages" / pkg
            pkg_dir.mkdir(parents=True, exist_ok=True)
            if pkg == "SupplyChain":
                schema = "ENTITY Supplier { id UUID PK \n name String \n tax_id String }"
            elif pkg == "TechCorp":
                schema = "ENTITY Vendor { uid UUID PK \n vendor_name String \n ein String }"
            (pkg_dir / "schema.lds").write_text(schema)
        declare_schema_paths(
            self.test_dir,
            [self.test_dir / "packages" / pkg / "schema.lds" for pkg in packages],
        )

        injector = DomainSQLiteInjector(str(self.test_dir))
        injector.inject_all_active()

        # Populate Data
        sc_db = self.test_dir / ".etg" / "states" / "SupplyChain.db"
        conn = sqlite3.connect(sc_db)
        conn.execute("INSERT INTO suppliers (id, name, tax_id) VALUES ('s1', 'Acme', 'EIN-123')")
        conn.commit()
        conn.close()

        tc_db = self.test_dir / ".etg" / "states" / "TechCorp.db"
        conn = sqlite3.connect(tc_db)
        conn.execute("INSERT INTO vendors (uid, vendor_name, ein) VALUES ('v1', 'Acme External', 'EIN-123')")
        conn.commit()
        conn.close()

        self.router = FederatedRouter(str(self.test_dir))
        self.broker = EntigramBroker(str(self.test_dir))

    def tearDown(self):
        if self.test_dir.exists():
            shutil.rmtree(self.test_dir)

    def test_semantic_join_by_ein(self):
        # Authorize alignment: Supplier.tax_id == Vendor.ein
        # This is NOT a foreign key relationship. It's a semantic join.
        self.broker.authorize_alignment(
            source_domain="TechCorp",
            target_domain="SupplyChain",
            source_concept="Vendor.ein",
            target_concept="Supplier.tax_id",
            confidence=1.0,
            rationale="Shared tax identifier"
        )

        # Query: Supplier -> Vendor (linked via tax_id == ein)
        graphql = """
        {
          Supplier {
            name
            Vendor {
              vendor_name
            }
          }
        }
        """
        
        # Test Case 1: Primary Path (CozoDB if available)
        results = self.router.execute(graphql)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['name'], 'Acme')
        self.assertIsNotNone(results[0]['Vendor'], "Vendor should be joined via tax_id == ein (Primary Path)")
        self.assertEqual(results[0]['Vendor']['vendor_name'], 'Acme External')

    def test_semantic_join_sql_fallback(self):
        # Force SQL Fallback by mocking cozo property
        with unittest.mock.patch.object(FederatedRouter, 'cozo', new_callable=unittest.mock.PropertyMock) as mock_cozo:
            mock_cozo.return_value = None
            
            # Re-authorize alignment (or it might persist if using the same DB, but let's be safe)
            self.broker.authorize_alignment(
                source_domain="TechCorp",
                target_domain="SupplyChain",
                source_concept="Vendor.ein",
                target_concept="Supplier.tax_id",
                confidence=1.0,
                rationale="Shared tax identifier"
            )

            graphql = """
            {
              Supplier {
                name
                Vendor {
                  vendor_name
                }
              }
            }
            """
            results = self.router.execute(graphql)
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0]['name'], 'Acme')
            self.assertIsNotNone(results[0]['Vendor'], "Vendor should be joined via tax_id == ein (Fallback Path)")
            self.assertEqual(results[0]['Vendor']['vendor_name'], 'Acme External')

if __name__ == "__main__":
    unittest.main()
