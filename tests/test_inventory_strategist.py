import unittest
import sqlite3
import os
import shutil
from pathlib import Path
from entigram.inventory_strategist import InventoryStrategist
from entigram.broker import EntigramBroker

class TestInventoryStrategist(unittest.TestCase):
    def setUp(self):
        self.test_dir = Path("test_workspace_strategist")
        self.test_dir.mkdir(exist_ok=True)
        self.states_dir = self.test_dir / ".etg" / "states"
        self.states_dir.mkdir(parents=True, exist_ok=True)
        
        # 1. Setup Salesforce Domain (Sales Forecasts)
        sf_db_path = self.states_dir / "Salesforce.db"
        conn = sqlite3.connect(sf_db_path)
        with conn:
            conn.execute("CREATE TABLE sf_products (id TEXT PRIMARY KEY, name TEXT, product_code TEXT)")
            conn.execute("CREATE TABLE sf_opportunitylineitems (id TEXT PRIMARY KEY, product_id TEXT, quantity INTEGER)")
            
            # Product 1: Forecast 100
            conn.execute("INSERT INTO sf_products VALUES ('sf-p-001', 'Widget A', 'SKU-001')")
            conn.execute("INSERT INTO sf_opportunitylineitems VALUES ('sf-oli-501', 'sf-p-001', 100)")
            
            # Product 2: Forecast 50
            conn.execute("INSERT INTO sf_products VALUES ('sf-p-002', 'Widget B', 'SKU-002')")
            conn.execute("INSERT INTO sf_opportunitylineitems VALUES ('sf-oli-502', 'sf-p-002', 50)")
        conn.close()
        
        # 2. Setup SupplyChain Domain (Inventory)
        sc_db_path = self.states_dir / "SupplyChain.db"
        conn = sqlite3.connect(sc_db_path)
        with conn:
            conn.execute("CREATE TABLE products (id TEXT PRIMARY KEY, sku TEXT, name TEXT)")
            conn.execute("CREATE TABLE inventory_items (id TEXT PRIMARY KEY, product_id TEXT, quantity INTEGER)")
            
            # Product 1: Stock 45 (LOW STOCK - Violation)
            conn.execute("INSERT INTO products VALUES ('sc-p-101', 'SKU-001', 'Widget A')")
            conn.execute("INSERT INTO inventory_items VALUES ('sc-inv-901', 'sc-p-101', 45)")
            
            # Product 2: Stock 60 (SUFFICIENT STOCK)
            conn.execute("INSERT INTO products VALUES ('sc-p-102', 'SKU-002', 'Widget B')")
            conn.execute("INSERT INTO inventory_items VALUES ('sc-inv-902', 'sc-p-102', 60)")
        conn.close()

        # 3. Create Schema files
        sf_schema = """
ENTITY: SF_Product
ATTRIBUTES:
  . id (TEXT)
  - name (TEXT)
  - product_code (TEXT)

ENTITY: SF_OpportunityLineItem
ATTRIBUTES:
  . id (TEXT)
  - product_id (TEXT)
  - quantity (INTEGER)

RELATIONSHIP: SF_Product (1) [MUST] --- [MAY] (MANY) SF_OpportunityLineItem
"""
        sc_schema = """
ENTITY: Product
ATTRIBUTES:
  . id (TEXT)
  - sku (TEXT)
  - name (TEXT)

ENTITY: Inventory_Item
ATTRIBUTES:
  . id (TEXT)
  - product_id (TEXT)
  - quantity (INTEGER)

RELATIONSHIP: Product (1) [MUST] --- [MAY] (MANY) Inventory_Item
"""
        (self.test_dir / "salesforce.lds").write_text(sf_schema)
        (self.test_dir / "supplychain.lds").write_text(sc_schema)

        # 4. Initialize Entigram Infrastructure
        (self.test_dir / ".etg" / "entigram.yaml").write_text("""
packages:
  - Salesforce
  - SupplyChain
cli_engine: Antigravity
""")

        # 5. Negotiate Alignment
        broker = EntigramBroker(str(self.test_dir))
        broker.authorize_alignment("Salesforce", "SupplyChain", "SF_Product", "Product", 0.95, "Fuzzy match on 'Product'", validate_schema=False)
        broker.authorize_alignment("Salesforce", "SupplyChain", "SF_Product.product_code", "Product.sku", 0.95, "Domain knowledge: Product Code == SKU", validate_schema=False)
        broker.authorize_alignment("Salesforce", "SupplyChain", "SF_OpportunityLineItem.quantity", "Inventory_Item.quantity", 0.85, "Quantity mapping", validate_schema=False)

        self.strategist = InventoryStrategist(str(self.test_dir))

    def tearDown(self):
        if self.test_dir.exists():
            shutil.rmtree(self.test_dir)

    def test_check_inventory_levels(self):
        violations = self.strategist.check_inventory_levels()
        self.assertEqual(len(violations), 1)
        
        violation = violations[0]
        self.assertEqual(violation["product_sku"], "SKU-001")
        self.assertEqual(violation["stock"], "45")
        self.assertEqual(violation["demand"], "100")

if __name__ == "__main__":
    unittest.main()
