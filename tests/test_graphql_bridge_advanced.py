import unittest
from entigram.graphql_bridge import GraphQLBridge

class TestGraphQLBridgeAdvanced(unittest.TestCase):
    def setUp(self):
        self.context = {
            "Customer": "https://example.com/schema/Customer",
            "name": "https://example.com/schema/Customer.name",
            "Order": "https://example.com/schema/Order",
            "total": "https://example.com/schema/Order.total",
            "hasOrder": "mk:hasOrder"
        }
        self.bridge = GraphQLBridge(self.context)

    def test_nested_query(self):
        graphql = """
        query {
          Customer {
            name
            hasOrder {
              total
            }
          }
        }
        """
        sparql = self.bridge.translate(graphql)
        
        # Check for nested pattern
        # Prefixed names should not have <>, full URIs should.
        self.assertIn("?subject mk:hasOrder ?hasOrder", sparql)
        self.assertIn("?hasOrder <https://example.com/schema/Order.total> ?total", sparql)
        self.assertIn("SELECT ?name ?total", sparql)

    def test_pagination(self):
        graphql = """
        query {
          Customer(first: 10, offset: 20) {
            name
          }
        }
        """
        sparql = self.bridge.translate(graphql)
        self.assertIn("LIMIT 10", sparql)
        self.assertIn("OFFSET 20", sparql)

    def test_optional_fields(self):
        # By default, we should use OPTIONAL for attributes to avoid failing the whole query if one property is missing
        graphql = """
        {
          Customer {
            name
            createdAt
          }
        }
        """
        sparql = self.bridge.translate(graphql)
        self.assertIn("OPTIONAL { ?subject <https://example.com/schema/Customer.name> ?name }", sparql)

if __name__ == "__main__":
    unittest.main()
