import unittest
from entigram.governance.negotiator import AlignmentNegotiator

class TestNegotiatorBlocking(unittest.TestCase):
    def setUp(self):
        self.negotiator = AlignmentNegotiator(threshold=0.4)

    def test_semantic_blocking_identity(self):
        source_schema = """
        ENTITY: Customer
        - .id (UUID)
        - name (String)
        """
        target_schema = """
        ENTITY: User
        - .id (UUID)
        - full_name (String)
        """
        # Both "Customer" and "User" contain keywords for the "identity" block
        proposals = self.negotiator.negotiate(source_schema, target_schema)
        
        # Check if Customer and User were matched
        match = next((p for p in proposals if p['source_concept'] == 'Customer' and p['target_concept'] == 'User'), None)
        self.assertIsNotNone(match)
        self.assertIn("Block Match [identity]", match['rationale'])

    def test_semantic_blocking_isolation(self):
        source_schema = """
        ENTITY: Customer
        - .id (UUID)
        """
        target_schema = """
        ENTITY: Product
        - .id (UUID)
        """
        # "Customer" is identity, "Product" is product. They should NOT match if blocks are disjoint.
        # Note: If fallback prefixes overlap, they might still compare, but here they are in different categories.
        proposals = self.negotiator.negotiate(source_schema, target_schema)
        
        match = next((p for p in proposals if p['source_concept'] == 'Customer' and p['target_concept'] == 'Product'), None)
        self.assertIsNone(match, "Customer and Product should be in different blocks and not compared/matched")

    def test_generic_supplier_provider_alignment(self):
        source = "ENTITY Supplier { id UUID PK }"
        target = "ENTITY Provider { id UUID PK }"
        proposals = self.negotiator.negotiate(source, target)
        match = next((p for p in proposals if p["source_concept"] == "Supplier" and p["target_concept"] == "Provider"), None)
        self.assertIsNotNone(match)
        self.assertIn("Block Match [identity]", match["rationale"])

    def test_fallback_blocking(self):
        source_schema = """
        ENTITY: WidgetSpec
        - .id (UUID)
        """
        target_schema = """
        ENTITY: WidgetImage
        - .id (UUID)
        """
        # Neither matches a category, so they fallback to prefix_wid
        proposals = self.negotiator.negotiate(source_schema, target_schema)
        
        match = next((p for p in proposals if p['source_concept'] == 'WidgetSpec' and p['target_concept'] == 'WidgetImage'), None)
        self.assertIsNotNone(match)
        self.assertIn("Block Match [prefix_wid]", match['rationale'])

if __name__ == "__main__":
    unittest.main()
