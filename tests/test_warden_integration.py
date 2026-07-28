import unittest
import os
import shutil
import json
import sqlite3
import sys
from io import StringIO
from pathlib import Path
from unittest.mock import patch
from entigram.broker import EntigramBroker
from entigram.cli_runner.etg_cli import main
from entigram.governance.warden import Warden
from entigram.injector import inject_entigram_manifest
from tests.workspace_helpers import declare_schema_paths

class TestWardenIntegration(unittest.TestCase):
    def setUp(self):
        self.test_dir = Path("test_warden_integration_workspace")
        if self.test_dir.exists():
            shutil.rmtree(self.test_dir)
        self.test_dir.mkdir()

        # Initialize workspace
        inject_entigram_manifest(str(self.test_dir), ["Entigram Schemas"], "Antigravity")
        
        # Create a schema.lds
        self.schema_content = """
        ENTITY Supplier {
            id UUID PK
            name String
        }
        """
        (self.test_dir / "schema.lds").write_text(self.schema_content)
        
        self.broker = EntigramBroker(str(self.test_dir))

    def tearDown(self):
        if self.test_dir.exists():
            shutil.rmtree(self.test_dir)

    def run_cli(self, argv):
        with patch.object(sys, "argv", ["etg"] + argv):
            output = StringIO()
            with patch("sys.stdout", output):
                try:
                    main()
                    code = 0
                except SystemExit as exc:
                    code = int(exc.code or 0)
            return code, output.getvalue()

    def test_broker_rejects_invalid_payload(self):
        # 1. Lock the laws
        self.broker.warden.lock_fingerprint()
        
        # 2. Propose a resolution with an invalid attribute 'rating' (not in Schema)
        invalid_state = json.dumps({"name": "Global Fasteners", "rating": 5})
        success = self.broker.propose_resolution("CONFLICT-1", "Supplier", invalid_state, "Hallucinated rating")
        
        self.assertFalse(success, "Broker should have rejected payload with unknown attribute 'rating'")

    def test_broker_rejects_on_integrity_violation(self):
        # 1. Lock the laws
        self.broker.warden.lock_fingerprint()
        
        # 2. Tamper with the laws (change Schema)
        (self.test_dir / "schema.lds").write_text(self.schema_content + "\nENTITY Tampered { id UUID PK }")
        
        # 3. Propose a valid resolution (should fail due to integrity violation)
        valid_state = json.dumps({"name": "Global Fasteners"})
        success = self.broker.propose_resolution("CONFLICT-1", "Supplier", valid_state, "Valid but tainted")
        
        self.assertFalse(success, "Broker should have rejected proposal due to integrity violation (TAINTED)")

    def test_broker_allows_valid_payload_when_protected(self):
        # 1. Lock the laws
        self.broker.warden.lock_fingerprint()
        
        # 2. Propose a valid resolution
        valid_state = json.dumps({"name": "Global Fasteners"})
        success = self.broker.propose_resolution("CONFLICT-1", "Supplier", valid_state, "Valid and protected")
        
        self.assertTrue(success, "Broker should have allowed valid payload when protected")

    def test_package_schema_tampering_blocks_alignment_authorization(self):
        package_paths = []
        for domain in ("DomainA", "DomainB"):
            schema_path = (
                self.test_dir / ".etg" / "packages" / domain / "schema.lds"
            )
            schema_path.parent.mkdir(parents=True)
            schema_path.write_text("ENTITY Original { id UUID PK }")
            package_paths.append(schema_path)
        declare_schema_paths(self.test_dir, package_paths)

        for schema_path in package_paths:
            schema_path.write_text("ENTITY Injected { id UUID PK }")

        self.assertFalse(self.broker.warden.verify_integrity(emit_human=False))
        self.assertFalse(
            self.broker.authorize_alignment(
                "DomainA",
                "DomainB",
                "Injected",
                "Injected",
                1.0,
                "Tampered package schema",
                validate_schema=True,
            )
        )

    def test_warden_records_structured_halt_event_for_invalid_payload(self):
        self.broker.warden.lock_fingerprint()

        valid = self.broker.warden.validate_payload(
            "Supplier",
            {"name": "Global Fasteners", "rating": 5},
            emit_human=False,
        )

        self.assertFalse(valid)
        event = self.broker.warden.last_halt_event.to_dict()
        self.assertEqual(event["halt_code"], "UNKNOWN_ATTRIBUTE")
        self.assertIn("allowed_attributes", event["expected_schema"])
        self.assertEqual(event["actual_payload"]["rating"], 5)
        self.assertIn("suggested_fix", event)

    def test_warden_check_json_outputs_halt_event(self):
        Warden(str(self.test_dir)).lock_fingerprint()
        (self.test_dir / "schema.lds").write_text(
            self.schema_content + "\nENTITY Tampered { id UUID PK }"
        )

        code, output = self.run_cli([
            "warden",
            "--dir",
            str(self.test_dir),
            "check",
            "--json",
        ])

        payload = json.loads(output)
        self.assertEqual(code, 1)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["halt_event"]["halt_code"], "SCHEMA_INTEGRITY_VIOLATION")
        self.assertIn("expected_checksum", payload["halt_event"]["expected_schema"])

    def test_broker_decide_json_outputs_halt_event(self):
        self.broker.warden.lock_fingerprint()

        code, output = self.run_cli([
            "broker",
            "--dir",
            str(self.test_dir),
            "decide",
            "--id",
            "CONFLICT-1",
            "--type",
            "Supplier",
            "--state",
            json.dumps({"name": "Global Fasteners", "rating": 5}),
            "--rationale",
            "Hallucinated rating",
            "--json",
        ])

        payload = json.loads(output)
        self.assertEqual(code, 1)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["halt_event"]["halt_code"], "UNKNOWN_ATTRIBUTE")
        self.assertEqual(payload["halt_event"]["actual_payload"]["rating"], 5)

if __name__ == "__main__":
    unittest.main()
