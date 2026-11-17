import unittest
from entigram.graphql_bridge import GraphQLBridge

class TestGraphQLBridge(unittest.TestCase):
    def test_basic_translation(self):
        context = {
            "Customer": "https://example.com/schema/Customer",
            "name": "https://example.com/schema/Customer.name",
            "createdAt": "https://example.com/schema/Customer.createdAt"
        }
        bridge = GraphQLBridge(context)
        
        graphql = """
        query {
          Customer {
            name
            createdAt
          }
        }
        """
        
        sparql = bridge.translate(graphql)
        
        # Check for variables in SELECT (order independent)
        self.assertIn("?name", sparql.split("WHERE")[0])
        self.assertIn("?createdAt", sparql.split("WHERE")[0])
        
        self.assertIn("?subject a <https://example.com/schema/Customer>", sparql)
        self.assertIn("OPTIONAL { ?subject <https://example.com/schema/Customer.name> ?name }", sparql)
        self.assertIn("OPTIONAL { ?subject <https://example.com/schema/Customer.createdAt> ?createdAt }", sparql)

    def test_translation_without_query_keyword(self):
        context = {"Account": "mk:SalesforceAccount", "id": "mk:sf_id"}
        bridge = GraphQLBridge(context)
        
        graphql = "{ Account { id } }"
        sparql = bridge.translate(graphql)
        
        self.assertIn("SELECT ?id", sparql)
        self.assertIn("?subject a mk:SalesforceAccount", sparql)

if __name__ == "__main__":
    unittest.main()
