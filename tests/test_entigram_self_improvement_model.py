from pathlib import Path
import unittest

from entigram.governance.commissioner import Commissioner
from entigram.schema_compiler.parser import SchemaParser
from entigram.schema_compiler.compiler import SchemaCompiler
from entigram.ontology_compiler.compiler import OntologyCompiler


def _load_project_model():
    schema_text = Path("schema.lds").read_text()
    parser = SchemaParser(schema_text)
    entities, relationships = parser.parse()
    return schema_text, entities, relationships


class TestEntigramSelfImprovementModel(unittest.TestCase):
    def test_self_improvement_entities_are_modeled(self):
        _, entities, relationships = _load_project_model()

        expected_entities = {
            "Entigram_Expectation",
            "Entigram_Delivery_Evidence",
            "Entigram_Delivery_Evaluation",
            "Entigram_Improvement_Proposal",
            "Entigram_Lesson",
            "Entigram_Delivery_Snapshot",
            "Entigram_Artifact",
            "Entigram_Delivery_Drift",
            "Entigram_Agent_Task",
            "Entigram_Agent_Hibernation",
            "Entigram_Usage_Event",
        }
        self.assertTrue(expected_entities.issubset(entities.keys()))

        agent_attrs = {attr["name"] for attr in entities["Entigram_Agent"].attributes}
        self.assertTrue({
            "handoff_policy", "default_proof_policy", "proof_capabilities",
            "agent_id", "reliability_score", "capability_scores",
            "allowed_task_classes", "restricted_task_classes",
        }.issubset(agent_attrs))

        task_attrs = {attr["name"] for attr in entities["Entigram_Agent_Task"].attributes}
        self.assertTrue({"task_id", "risk_level", "required_score", "assigned_agent_id"}.issubset(task_attrs))

        hibernation_attrs = {attr["name"] for attr in entities["Entigram_Agent_Hibernation"].attributes}
        self.assertTrue({"hibernate_id", "remaining_tokens", "resume_after", "next_action"}.issubset(hibernation_attrs))

        proposal_attrs = {attr["name"] for attr in entities["Entigram_Agent_Proposal"].attributes}
        self.assertTrue({"expected_benefit", "rollback_plan"}.issubset(proposal_attrs))

        snapshot_attrs = {attr["name"] for attr in entities["Entigram_Delivery_Snapshot"].attributes}
        self.assertTrue({"snapshot_id", "schema_hash", "warden_status", "artifact_ids"}.issubset(snapshot_attrs))

        artifact_attrs = {attr["name"] for attr in entities["Entigram_Artifact"].attributes}
        self.assertTrue({"path", "artifact_role", "sha256", "size_bytes"}.issubset(artifact_attrs))

        drift_attrs = {attr["name"] for attr in entities["Entigram_Delivery_Drift"].attributes}
        self.assertTrue({"snapshot_id", "artifact_path", "drift_status"}.issubset(drift_attrs))

        relationship_pairs = {(rel.entity_a, rel.entity_b) for rel in relationships}
        self.assertIn(("Entigram_Project", "Entigram_Expectation"), relationship_pairs)
        self.assertIn(("Entigram_Expectation", "Entigram_Delivery_Evidence"), relationship_pairs)
        self.assertIn(("Entigram_Expectation", "Entigram_Delivery_Evaluation"), relationship_pairs)
        self.assertIn(("Entigram_Expectation", "Entigram_Improvement_Proposal"), relationship_pairs)
        self.assertIn(("Entigram_Project", "Entigram_Lesson"), relationship_pairs)
        self.assertIn(("Entigram_Project", "Entigram_Delivery_Snapshot"), relationship_pairs)
        self.assertIn(("Entigram_Project", "Entigram_Artifact"), relationship_pairs)
        self.assertIn(("Entigram_Delivery_Snapshot", "Entigram_Artifact"), relationship_pairs)
        self.assertIn(("Entigram_Delivery_Snapshot", "Entigram_Delivery_Drift"), relationship_pairs)
        self.assertIn(("Entigram_Project", "Entigram_Agent_Task"), relationship_pairs)
        self.assertIn(("Entigram_Project", "Entigram_Agent_Hibernation"), relationship_pairs)
        self.assertIn(("Entigram_Project", "Entigram_Usage_Event"), relationship_pairs)
        self.assertIn(("Entigram_Agent", "Entigram_Agent_Task"), relationship_pairs)
        self.assertIn(("Entigram_Agent", "Entigram_Agent_Hibernation"), relationship_pairs)

    def test_self_improvement_model_compiles_to_sql_and_ttl(self):
        _, entities, relationships = _load_project_model()

        sql = SchemaCompiler(entities, relationships).compile()
        self.assertIn("CREATE TABLE IF NOT EXISTS entigram_expectations", sql)
        self.assertIn("entigram_expectation_id TEXT", sql)

        ttl = OntologyCompiler(entities, relationships).compile()
        self.assertIn("mk:Entigram_Expectation a owl:Class", ttl)
        self.assertIn("mk:Entigram_Delivery_Evidence a owl:Class", ttl)
        self.assertIn("mk:Entigram_Delivery_Snapshot a owl:Class", ttl)
        self.assertIn("mk:Entigram_Artifact a owl:Class", ttl)
        self.assertIn("mk:Entigram_Delivery_Drift a owl:Class", ttl)
        self.assertIn("mk:Entigram_Agent_Task a owl:Class", ttl)
        self.assertIn("mk:Entigram_Agent_Hibernation a owl:Class", ttl)
        self.assertIn("mk:Entigram_Usage_Event a owl:Class", ttl)
        self.assertIn("mk:Entigram_Agent_proof_capabilities a owl:DatatypeProperty", ttl)
        self.assertIn("mk:Entigram_Agent_reliability_score a owl:DatatypeProperty", ttl)
        self.assertIn("mk:Entigram_Agent_Hibernation_next_action a owl:DatatypeProperty", ttl)
        self.assertIn("mk:Entigram_Artifact_sha256 a owl:DatatypeProperty", ttl)
        self.assertIn("mk:Entigram_Delivery_Drift_drift_status a owl:DatatypeProperty", ttl)
        self.assertIn("mk:Entigram_Expectation_developer_expectation a owl:DatatypeProperty", ttl)
        self.assertIn("mk:relates_Entigram_Expectation_to_Entigram_Delivery_Evidence", ttl)

    def test_self_improvement_expectations_are_commissioned(self):
        schema_text, _, _ = _load_project_model()

        checklist = Commissioner(schema_text).build_checklist(
            proofs=[
                "python -m unittest tests.test_entigram_self_improvement_model passed",
                "python -m unittest tests.test_commissioner passed",
                "python -m unittest tests.test_entigram_release_model passed",
                "python -m unittest tests.test_agent_policy passed",
                "python -m unittest tests.test_mcp_service passed",
                "python -m unittest tests.test_delivery_ledger passed",
                "python -m unittest tests.test_cli_integration passed",
                "python -m unittest tests.test_hydration passed",
                "python -m unittest tests.test_agent_orchestration passed",
                "python -m unittest tests.test_workspace_lifecycle passed",
            ]
        )

        names = {item["name"] for item in checklist["items"]}
        self.assertIn("Entigram Self-Improvement Loop", names)
        self.assertIn("Agent Delivery Proof", names)
        self.assertIn("Agent Capability Routing", names)
        self.assertIn("Agent Hibernation Protocol", names)
        self.assertIn("Transparent Token Attribution", names)
        self.assertIn("Reversible Workspace Lifecycle", names)
        self.assertIn("Out-of-the-box Expectation Guard", names)
        self.assertIn("Release Orchestration", names)
        self.assertIn("Agent Policy Discoverability", names)
        self.assertIn("Deterministic Pre-Handoff Gate", names)
        self.assertIn("MCP Closed-World Schema Scope", names)
        self.assertIn("Machine-Readable MCP Gate Responses", names)
        self.assertIn("MCP Tool Contract Documentation", names)
        self.assertIn("Tamper-Evident Audit Bundles", names)
        self.assertIn("Immutable Gate Smoke Demo", names)
        self.assertIn("Headless CLI Boundary", names)
        self.assertIn("CLI Version Introspection", names)
        self.assertIn("Workspace Manifest Version Clarity", names)
        self.assertIn("Signed Audit Bundles", names)
        self.assertIn("Entigram 1.6 Release Note", names)
        self.assertTrue(checklist["valid"])

    def test_product_surface_artifacts_exist(self):
        mcp_docs = Path("docs/mcp-tools.md").read_text()
        demo = Path("scripts/demo_immutable_gate.py").read_text()
        pyproject = Path("pyproject.toml").read_text()

        self.assertIn("etg_get_schemas", mcp_docs)
        self.assertIn("etg_propose_alignment", mcp_docs)
        self.assertIn("etg_log_conflict", mcp_docs)
        self.assertIn("etg_get_assessment_capabilities", mcp_docs)
        self.assertIn("etg_assess", mcp_docs)
        self.assertIn("UNKNOWN_CONCEPT", mcp_docs)
        self.assertIn("export-audit", Path("README.md").read_text())
        self.assertIn("Entigram 1.6 introduces", Path("CHANGELOG.md").read_text())
        self.assertIn("EntigramMCPService", demo)
        self.assertIn("export_audit_bundle", demo)
        self.assertIn("[project.optional-dependencies]", pyproject)
        self.assertRegex(pyproject, r'version = "\d+\.\d+\.\d+"')
        self.assertIn('"cryptography>=42.0.4"', pyproject)


if __name__ == "__main__":
    unittest.main()
