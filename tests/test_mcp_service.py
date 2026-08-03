import json
import shutil
import tempfile
import unittest
from pathlib import Path

import yaml
from entigram.injector import inject_entigram_manifest
from entigram.mcp_service import EntigramMCPService
from entigram.sqlite_ledger.manager import LedgerManager
from entigram.sqlite_ledger.paths import resolve_ledger_path


SCHEMA = """
ENTITY: User {
  id UUID PK
  name String
}

ENTITY: Account {
  id UUID PK
  owner_name String
}
"""

PARENT_CHILD_SCHEMA = """
ENTITY: Parent {
  id UUID PK
  name String
}

ENTITY: Child {
  id UUID PK
  name String
}

RELATIONSHIPS:
- Parent (1) [MUST] --- [MAY] (MANY) Child
"""


class TestMCPService(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        inject_entigram_manifest(self.test_dir, ["Entigram Schemas"], "Codex")
        Path(self.test_dir, "schema.lds").write_text(SCHEMA)
        self.service = EntigramMCPService(self.test_dir)

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    def test_get_schemas_returns_lds_boundaries(self):
        output = json.loads(self.service.get_schemas())

        self.assertTrue(output["ok"])
        self.assertEqual(output["schemas"][0]["path"], "schema.lds")
        self.assertIn("User", output["schemas"][0]["entities"])
        self.assertIn("Account", output["schemas"][0]["entities"])

    def test_get_schemas_honors_explicit_manifest_schema_paths(self):
        Path(self.test_dir, "template.lds").write_text(
            """
ENTITY: Ghost {
  id UUID PK
}
"""
        )
        manifest_path = Path(self.test_dir, ".etg", "entigram.yaml")
        manifest = yaml.safe_load(manifest_path.read_text())
        manifest["schema_paths"] = ["schema.lds"]
        manifest_path.write_text(yaml.dump(manifest, default_flow_style=False))

        output = json.loads(self.service.get_schemas())

        self.assertEqual([schema["path"] for schema in output["schemas"]], ["schema.lds"])
        self.assertNotIn("Ghost", output["schemas"][0]["entities"])

    def test_get_workspace_context_returns_read_only_boot_context(self):
        output = json.loads(self.service.get_workspace_context())

        self.assertTrue(output["ok"])
        workspace = output["workspace"]
        self.assertEqual(workspace["state"], "active")
        self.assertEqual(workspace["manifest_path"], ".etg/entigram.yaml")
        self.assertEqual(workspace["schema_paths"], ["schema.lds"])
        self.assertEqual(workspace["schemas"][0]["entities"], ["Account", "User"])
        self.assertIn("etg broker preflight --file <path>", workspace["next_commands"])

    def test_get_capabilities_classifies_tools_and_transport_boundary(self):
        output = json.loads(self.service.get_capabilities())

        self.assertTrue(output["ok"])
        self.assertEqual(output["server"]["name"], "entigram")
        capabilities = {item["name"]: item for item in output["capabilities"]}
        self.assertTrue(capabilities["etg_get_workspace_context"]["read_only"])
        self.assertFalse(capabilities["etg_propose_alignment"]["read_only"])
        self.assertTrue(capabilities["etg_propose_alignment"]["writes_ledger"])
        self.assertIn("loopback-only", output["server"]["transport_boundary"])

    def test_propose_alignment_validates_and_writes_proposal(self):
        result = json.loads(
            self.service.propose_alignment(
                json.dumps(
                    {
                        "source_domain": "CRM",
                        "target_domain": "Finance",
                        "source_concept": "User.name",
                        "target_concept": "Account.owner_name",
                        "confidence": 0.91,
                        "rationale": "Both values identify the account owner name.",
                    }
                )
            )
        )

        self.assertTrue(result["ok"])
        ledger = LedgerManager(str(resolve_ledger_path(self.test_dir)))
        try:
            alignments = ledger.get_alignments()
        finally:
            ledger.close()

        self.assertEqual(len(alignments), 1)
        self.assertEqual(alignments[0]["lifecycle_status"], "proposed")
        self.assertFalse(alignments[0]["verified"])

    def test_propose_alignment_rejects_unknown_entity_without_crashing(self):
        result = self.service.propose_alignment(
            json.dumps(
                {
                    "source_domain": "CRM",
                    "target_domain": "Finance",
                    "source_concept": "Ghost.name",
                    "target_concept": "Account.owner_name",
                    "confidence": 0.91,
                    "rationale": "Bad payload.",
                }
            )
        )

        error = json.loads(result)
        self.assertFalse(error["ok"])
        self.assertEqual(error["error"]["code"], "UNKNOWN_CONCEPT")
        self.assertEqual(
            error["error"]["message"],
            "Error: Invalid Schema Alignment - Entity Ghost not found",
        )
        ledger = LedgerManager(str(resolve_ledger_path(self.test_dir)))
        try:
            self.assertEqual(ledger.get_alignments(), [])
        finally:
            ledger.close()

    def test_propose_alignment_rejects_child_when_parent_alignment_is_only_proposed(self):
        Path(self.test_dir, "schema.lds").write_text(PARENT_CHILD_SCHEMA)

        parent_result = json.loads(
            self.service.propose_alignment(
                json.dumps(
                    {
                        "source_domain": "CRM",
                        "target_domain": "ERP",
                        "source_concept": "Parent.name",
                        "target_concept": "Parent.name",
                        "confidence": 0.91,
                        "rationale": "Proposed parent mapping.",
                    }
                )
            )
        )
        self.assertTrue(parent_result["ok"])

        child_result = self.service.propose_alignment(
            json.dumps(
                {
                    "source_domain": "CRM",
                    "target_domain": "ERP",
                    "source_concept": "Child.name",
                    "target_concept": "Child.name",
                    "confidence": 0.91,
                    "rationale": "Child mapping should wait for a verified parent.",
                }
            )
        )

        error = json.loads(child_result)
        self.assertFalse(error["ok"])
        self.assertEqual(error["error"]["code"], "RELATIONAL_GUARD_FAILED")
        self.assertIn("RA Precedence Violation", error["error"]["message"])

    def test_propose_alignment_allows_child_when_parent_alignment_is_trusted(self):
        Path(self.test_dir, "schema.lds").write_text(PARENT_CHILD_SCHEMA)
        ledger = LedgerManager(str(resolve_ledger_path(self.test_dir)))
        try:
            ledger.record_alignment(
                source_domain="CRM",
                target_domain="ERP",
                source_concept="Parent.name",
                target_concept="Parent.name",
                confidence=0.95,
                rationale="Verified parent mapping.",
                lifecycle_status="verified",
                evidence_type="human_review",
                verified=True,
                verified_by="test",
            )
        finally:
            ledger.close()

        child_result = json.loads(
            self.service.propose_alignment(
                json.dumps(
                    {
                        "source_domain": "CRM",
                        "target_domain": "ERP",
                        "source_concept": "Child.name",
                        "target_concept": "Child.name",
                        "confidence": 0.91,
                        "rationale": "Child mapping follows verified parent.",
                    }
                )
            )
        )

        self.assertTrue(child_result["ok"])

    def test_log_conflict_rejects_unknown_attribute(self):
        result = self.service.log_conflict(
            json.dumps(
                {
                    "conflict_id": "Conflict_1",
                    "entity_type": "User",
                    "agent_id": "AgentA",
                    "proposed_states": {
                        "AgentA": {"invented": "x"},
                        "AgentB": {"name": "y"},
                    },
                }
            )
        )

        error = json.loads(result)
        self.assertFalse(error["ok"])
        self.assertEqual(error["error"]["code"], "UNKNOWN_ATTRIBUTE")
        self.assertEqual(
            error["error"]["message"],
            "Error: Invalid Conflict - Attribute invented not found on entity User",
        )

    def test_log_conflict_validates_and_writes_conflict(self):
        result = json.loads(
            self.service.log_conflict(
                json.dumps(
                    {
                        "conflict_id": "Conflict_2",
                        "entity_type": "User",
                        "agent_id": "AgentA",
                        "proposed_states": {
                            "AgentA": {"name": "Alice"},
                            "AgentB": {"name": "Alicia"},
                        },
                    }
                )
            )
        )

        self.assertTrue(result["ok"])
        ledger = LedgerManager(str(resolve_ledger_path(self.test_dir)))
        try:
            conflicts = ledger.get_pending_conflicts()
        finally:
            ledger.close()

        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0]["conflict_id"], "Conflict_2")

    def test_file_ledger_uses_wal_and_busy_timeout(self):
        ledger_path = resolve_ledger_path(self.test_dir)
        ledger = LedgerManager(str(ledger_path))
        conn = ledger._get_connection()
        try:
            journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
            busy_timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
        finally:
            conn.close()
            ledger.close()

        self.assertEqual(journal_mode.lower(), "wal")
        self.assertEqual(busy_timeout, 10000)
