import unittest
import os
from pathlib import Path
from entigram.governance.negotiator import AlignmentNegotiator

class TestAlignmentNegotiator(unittest.TestCase):
    def test_negotiate_simple(self):
        source_schema = """
        ENTITY Customer {
            id UUID PK
            full_name String
            registered_on Date
        }
        """
        target_schema = """
        ENTITY Client {
            id UUID PK
            name String
            created_on Date
        }
        """
        
        negotiator = AlignmentNegotiator(threshold=0.3)
        proposals = negotiator.negotiate(source_schema, target_schema)
        
        # Check if Customer and Client are matched (low but should be there)
        entity_matches = [p for p in proposals if '.' not in p['source_concept']]
        self.assertTrue(any(p['source_concept'] == 'Customer' and p['target_concept'] == 'Client' for p in entity_matches))
        
        # Check if attributes are matched
        prop_matches = [p for p in proposals if '.' in p['source_concept']]
        # Customer.id <-> Client.id should be an exact match
        self.assertTrue(any(p['source_concept'] == 'Customer.id' and p['target_concept'] == 'Client.id' for p in prop_matches))

if __name__ == "__main__":
    unittest.main()
