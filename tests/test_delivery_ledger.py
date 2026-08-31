"""
Tests for the durable delivery ledger:
- delivery_evidence (commissioner pass persistence)
- delivery_artifacts (source-control-neutral artifact anchors)
- improvement_proposals (lifecycle tracking)
- delivery_snapshots (drift detection anchor)
"""
import unittest
from entigram.sqlite_ledger.manager import LedgerManager


class TestDeliveryEvidence(unittest.TestCase):
    def setUp(self):
        self.ledger = LedgerManager(":memory:")

    def tearDown(self):
        self.ledger.close()

    def test_record_and_retrieve_delivery_evidence(self):
        row_id = self.ledger.record_delivery_evidence(
            evidence_type="commission_pass",
            artifact_ref="commissioner_checklist",
            expectation_name="Stable Jump Arc",
            command="python -m pytest tests/test_gameplay.py",
            result_summary="3 passed in 0.1s",
            passed=True,
            agent_id="Antigravity",
        )
        self.assertIsNotNone(row_id)
        self.assertGreater(row_id, 0)

        records = self.ledger.get_delivery_evidence()
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record["evidence_type"], "commission_pass")
        self.assertEqual(record["expectation_name"], "Stable Jump Arc")
        self.assertTrue(record["passed"])
        self.assertEqual(record["agent_id"], "Antigravity")

    def test_filter_by_expectation_name(self):
        self.ledger.record_delivery_evidence(
            evidence_type="commission_pass",
            artifact_ref="commissioner_checklist",
            expectation_name="Loop A",
            passed=True,
        )
        self.ledger.record_delivery_evidence(
            evidence_type="test_run",
            artifact_ref="tests/test_b.py",
            expectation_name="Loop B",
            passed=True,
        )
        results = self.ledger.get_delivery_evidence(expectation_name="Loop A")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["expectation_name"], "Loop A")

    def test_filter_passed_only(self):
        self.ledger.record_delivery_evidence(
            evidence_type="test_run", artifact_ref="tests/a.py", passed=True
        )
        self.ledger.record_delivery_evidence(
            evidence_type="test_run", artifact_ref="tests/b.py", passed=False
        )
        results = self.ledger.get_delivery_evidence(passed_only=True)
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0]["passed"])


class TestImprovementProposals(unittest.TestCase):
    def setUp(self):
        self.ledger = LedgerManager(":memory:")

    def tearDown(self):
        self.ledger.close()

    def test_record_and_retrieve_proposal(self):
        row_id = self.ledger.record_improvement_proposal(
            title="Add Blocked expectation state",
            affected_model="Entigram_Expectation",
            proposed_change={"add_field": "lifecycle_status=Blocked"},
            rationale="Distinguish infra failures from proof gaps",
            expected_benefit="Clearer root cause in commissioner output",
            created_by="Antigravity",
        )
        self.assertIsNotNone(row_id)

        proposals = self.ledger.get_improvement_proposals()
        self.assertEqual(len(proposals), 1)
        p = proposals[0]
        self.assertEqual(p["title"], "Add Blocked expectation state")
        self.assertEqual(p["lifecycle_status"], "Proposed")
        self.assertEqual(p["created_by"], "Antigravity")
        self.assertIn("add_field", p["proposed_change"])

    def test_filter_by_lifecycle_status(self):
        self.ledger.record_improvement_proposal(
            title="Proposal A",
            affected_model="Model",
            proposed_change={},
            rationale="reason A",
            lifecycle_status="Proposed",
        )
        self.ledger.record_improvement_proposal(
            title="Proposal B",
            affected_model="Model",
            proposed_change={},
            rationale="reason B",
            lifecycle_status="Reviewed",
        )
        proposed = self.ledger.get_improvement_proposals(lifecycle_status="Proposed")
        self.assertEqual(len(proposed), 1)
        self.assertEqual(proposed[0]["title"], "Proposal A")

    def test_multiple_proposals_returned(self):
        for i in range(3):
            self.ledger.record_improvement_proposal(
                title=f"Proposal {i}",
                affected_model="Core",
                proposed_change={"index": i},
                rationale=f"reason {i}",
            )
        proposals = self.ledger.get_improvement_proposals()
        self.assertEqual(len(proposals), 3)


class TestDeliverySnapshots(unittest.TestCase):
    def setUp(self):
        self.ledger = LedgerManager(":memory:")

    def tearDown(self):
        self.ledger.close()

    def test_record_and_retrieve_snapshot(self):
        success = self.ledger.record_delivery_snapshot(
            snapshot_id="delivery-20260604T120000-Antigravity",
            expectation_count=2,
            missing_proof_count=0,
            schema_hash="abc123def456ab12",
            agent_id="Antigravity",
            warden_status="intact",
            evidence_ids=[1, 2],
            artifact_ids=[3, 4],
            metadata={"proofs_provided": 2},
        )
        self.assertTrue(success)

        snap = self.ledger.get_latest_snapshot()
        self.assertIsNotNone(snap)
        self.assertEqual(snap["snapshot_id"], "delivery-20260604T120000-Antigravity")
        self.assertEqual(snap["expectation_count"], 2)
        self.assertEqual(snap["missing_proof_count"], 0)
        self.assertEqual(snap["schema_hash"], "abc123def456ab12")
        self.assertEqual(snap["warden_status"], "intact")
        self.assertEqual(snap["evidence_ids"], [1, 2])
        self.assertEqual(snap["artifact_ids"], [3, 4])
        self.assertEqual(snap["metadata"]["proofs_provided"], 2)

    def test_get_latest_snapshot_returns_most_recent(self):
        self.ledger.record_delivery_snapshot(
            snapshot_id="snap-001", expectation_count=2, missing_proof_count=0
        )
        self.ledger.record_delivery_snapshot(
            snapshot_id="snap-002", expectation_count=3, missing_proof_count=0
        )
        snap = self.ledger.get_latest_snapshot()
        self.assertEqual(snap["snapshot_id"], "snap-002")

    def test_no_snapshot_returns_none(self):
        snap = self.ledger.get_latest_snapshot()
        self.assertIsNone(snap)


class TestDeliveryArtifacts(unittest.TestCase):
    def setUp(self):
        self.ledger = LedgerManager(":memory:")

    def tearDown(self):
        self.ledger.close()

    def test_record_and_retrieve_delivery_artifact(self):
        artifact_id = self.ledger.record_delivery_artifact(
            path="schema.lds",
            artifact_role="schema_contract",
            sha256="abc123",
            size_bytes=42,
            content_type="text/plain",
            source_ref="schema.lds",
        )

        self.assertIsNotNone(artifact_id)
        artifacts = self.ledger.get_delivery_artifacts()
        self.assertEqual(len(artifacts), 1)
        self.assertEqual(artifacts[0]["path"], "schema.lds")
        self.assertEqual(artifacts[0]["artifact_role"], "schema_contract")
        self.assertEqual(artifacts[0]["sha256"], "abc123")
        self.assertEqual(artifacts[0]["size_bytes"], 42)

    def test_get_delivery_artifacts_by_ids_preserves_snapshot_order(self):
        first = self.ledger.record_delivery_artifact(
            path="schema.lds",
            artifact_role="schema_contract",
            sha256="abc123",
        )
        second = self.ledger.record_delivery_artifact(
            path="local-result.txt",
            artifact_role="delivery_artifact",
            sha256="def456",
        )

        artifacts = self.ledger.get_delivery_artifacts_by_ids([second, first])
        self.assertEqual([artifact["id"] for artifact in artifacts], [second, first])


class TestCommissionerWritesEvidence(unittest.TestCase):
    """End-to-end: Commissioner pass should write to delivery_evidence in ledger."""

    SCHEMA = """
EXPECTATION: Stable Loop {
  developer_expectation: The loop must be stable.
  implementation_rule: Tests must not break the loop.
  validation_check: tests/test_loop.py
  proof: tests/test_loop.py
}
"""

    def test_commissioner_pass_writes_evidence_to_ledger(self):
        from entigram.governance.commissioner import Commissioner

        ledger = LedgerManager(":memory:")
        try:
            commissioner = Commissioner(self.SCHEMA, ledger=ledger)
            checklist = commissioner.build_checklist(
                proofs=["tests/test_loop.py passed 3 assertions"],
                agent_id="TestAgent",
            )

            self.assertTrue(checklist["valid"])

            # Verify evidence was written
            evidence = ledger.get_delivery_evidence(expectation_name="Stable Loop")
            self.assertEqual(len(evidence), 1)
            self.assertTrue(evidence[0]["passed"])
            self.assertEqual(evidence[0]["agent_id"], "TestAgent")
            self.assertEqual(evidence[0]["evidence_type"], "commission_pass")
        finally:
            ledger.close()

    def test_commissioner_fail_does_not_write_evidence(self):
        from entigram.governance.commissioner import Commissioner

        ledger = LedgerManager(":memory:")
        try:
            commissioner = Commissioner(self.SCHEMA, ledger=ledger)
            checklist = commissioner.build_checklist(proofs=[], agent_id="TestAgent")

            self.assertFalse(checklist["valid"])
            evidence = ledger.get_delivery_evidence()
            self.assertEqual(len(evidence), 0)
        finally:
            ledger.close()

    def test_commissioner_accepts_prior_passed_evidence_as_proof(self):
        from entigram.governance.commissioner import Commissioner

        ledger = LedgerManager(":memory:")
        try:
            evidence_id = ledger.record_delivery_evidence(
                evidence_type="test_run",
                artifact_ref="tests/test_loop.py",
                expectation_name="Stable Loop",
                command="tests/test_loop.py",
                result_summary="test passed",
                passed=True,
                agent_id="Resolver",
            )

            commissioner = Commissioner(self.SCHEMA, ledger=ledger)
            checklist = commissioner.build_checklist(agent_id="Deliverer")

            self.assertTrue(checklist["valid"])
            self.assertIn(evidence_id, checklist["evidence_ids"])
        finally:
            ledger.close()


class TestBrokerDeliverySnapshots(unittest.TestCase):
    SCHEMA = TestCommissionerWritesEvidence.SCHEMA

    def test_delivery_snapshot_anchors_evidence_ids(self):
        import tempfile
        import shutil
        from pathlib import Path

        from entigram.broker import EntigramBroker
        from entigram.injector import inject_entigram_manifest

        test_dir = tempfile.mkdtemp()
        ledger = None
        try:
            inject_entigram_manifest(test_dir, ["Entigram Schemas"], "Codex")
            Path(test_dir, "schema.lds").write_text(self.SCHEMA)
            ledger = LedgerManager(":memory:")
            broker = EntigramBroker(test_dir, ledger=ledger)

            checklist = broker.commission_and_record(
                proofs=["tests/test_loop.py passed"],
                agent_id="TestAgent",
            )

            self.assertTrue(checklist["valid"])
            self.assertTrue(checklist["evidence_ids"])
            self.assertTrue(checklist["artifact_ids"])
            snapshot = ledger.get_latest_snapshot()
            self.assertIsNotNone(snapshot)
            self.assertEqual(snapshot["evidence_ids"], checklist["evidence_ids"])
            self.assertEqual(snapshot["artifact_ids"], checklist["artifact_ids"])
            artifacts = ledger.get_delivery_artifacts()
            self.assertTrue(any(a["path"] == "schema.lds" for a in artifacts))
            from entigram.workspace_lifecycle import active_change_status

            self.assertEqual(
                active_change_status(Path(test_dir))["budget"]["changed_files"], 0
            )
        finally:
            if ledger is not None:
                ledger.close()
            shutil.rmtree(test_dir)

    def test_delivery_status_detects_runtime_source_drift(self):
        import tempfile
        import shutil
        from pathlib import Path

        from entigram.broker import EntigramBroker
        from entigram.injector import inject_entigram_manifest

        test_dir = tempfile.mkdtemp()
        ledger = None
        try:
            inject_entigram_manifest(test_dir, ["Entigram Schemas"], "Codex")
            Path(test_dir, "schema.lds").write_text(self.SCHEMA)
            runtime_file = Path(test_dir, "entigram", "runtime.py")
            runtime_file.parent.mkdir()
            runtime_file.write_text("VALUE = 1\n")
            ledger = LedgerManager(":memory:")
            broker = EntigramBroker(test_dir, ledger=ledger)

            checklist = broker.commission_and_record(
                proofs=["tests/test_loop.py passed"],
                agent_id="TestAgent",
            )
            self.assertTrue(checklist["valid"])

            runtime_file.write_text("VALUE = 2\n")
            status = broker.delivery_status()
            self.assertFalse(status["valid"])
            self.assertTrue(any(change["path"] == "entigram/runtime.py" for change in status["artifact_changes"]))
        finally:
            if ledger is not None:
                ledger.close()
            shutil.rmtree(test_dir)

    def test_delivery_status_detects_new_governed_source_file(self):
        import tempfile
        import shutil
        from pathlib import Path

        from entigram.broker import EntigramBroker
        from entigram.injector import inject_entigram_manifest

        test_dir = tempfile.mkdtemp()
        ledger = None
        try:
            inject_entigram_manifest(test_dir, ["Entigram Schemas"], "Codex")
            Path(test_dir, "schema.lds").write_text(self.SCHEMA)
            source_dir = Path(test_dir, "src")
            source_dir.mkdir()
            Path(source_dir, "existing.ts").write_text("export const value = 1;\n")
            ledger = LedgerManager(":memory:")
            broker = EntigramBroker(test_dir, ledger=ledger)
            checklist = broker.commission_and_record(
                proofs=["tests/test_loop.py passed"],
                agent_id="TestAgent",
            )
            self.assertTrue(checklist["valid"])

            Path(source_dir, "new-module.ts").write_text("export const added = true;\n")
            status = broker.delivery_status()

            self.assertFalse(status["valid"])
            self.assertTrue(any(
                artifact["path"] == "src/new-module.ts"
                and artifact["status"] == "unanchored"
                for artifact in status["unanchored_artifacts"]
            ))
        finally:
            if ledger is not None:
                ledger.close()
            shutil.rmtree(test_dir)

    def test_delivery_status_ignores_nonstandard_virtualenv(self):
        import tempfile
        import shutil
        from pathlib import Path

        from entigram.broker import EntigramBroker
        from entigram.injector import inject_entigram_manifest
        from entigram.workspace_contract import governed_artifact_paths

        test_dir = tempfile.mkdtemp()
        ledger = None
        try:
            inject_entigram_manifest(test_dir, ["Entigram Schemas"], "Codex")
            Path(test_dir, "schema.lds").write_text(self.SCHEMA)
            virtualenv_file = Path(
                test_dir,
                ".poet-venv",
                "lib",
                "python3.13",
                "site-packages",
                "dependency.py",
            )
            virtualenv_file.parent.mkdir(parents=True)
            virtualenv_file.write_text("dependency = True\n")
            ledger = LedgerManager(":memory:")
            broker = EntigramBroker(test_dir, ledger=ledger)

            checklist = broker.commission_and_record(
                proofs=["tests/test_loop.py passed"],
                agent_id="TestAgent",
            )
            status = broker.delivery_status()

            self.assertTrue(checklist["valid"])
            self.assertTrue(status["valid"])
            self.assertNotIn(virtualenv_file.resolve(), governed_artifact_paths(test_dir))
        finally:
            if ledger is not None:
                ledger.close()
            shutil.rmtree(test_dir)

    def test_governed_artifacts_use_git_inventory_and_ignore_rules(self):
        import shutil
        import subprocess
        import tempfile
        from pathlib import Path

        from entigram.injector import inject_entigram_manifest
        from entigram.workspace_contract import governed_artifact_paths

        if shutil.which("git") is None:
            self.skipTest("git is not installed")

        test_dir = tempfile.mkdtemp()
        try:
            inject_entigram_manifest(test_dir, ["Entigram Schemas"], "Codex")
            subprocess.run(
                ["git", "init", "--quiet"],
                cwd=test_dir,
                check=True,
            )
            Path(test_dir, ".gitignore").write_text("generated/\n")
            source = Path(test_dir, "src", "workflow.agent")
            source.parent.mkdir()
            source.write_text("governed\n")
            ignored = Path(test_dir, "generated", "cache.agent")
            ignored.parent.mkdir()
            ignored.write_text("ignored\n")

            paths = set(governed_artifact_paths(test_dir))

            self.assertIn(source.resolve(), paths)
            self.assertNotIn(ignored.resolve(), paths)
        finally:
            shutil.rmtree(test_dir)

    def test_governed_artifact_globs_override_git_inventory(self):
        import shutil
        import subprocess
        import tempfile
        from pathlib import Path

        import yaml

        from entigram.injector import inject_entigram_manifest
        from entigram.workspace_contract import governed_artifact_paths

        if shutil.which("git") is None:
            self.skipTest("git is not installed")

        test_dir = tempfile.mkdtemp()
        try:
            inject_entigram_manifest(test_dir, ["Entigram Schemas"], "Codex")
            subprocess.run(
                ["git", "init", "--quiet"],
                cwd=test_dir,
                check=True,
            )
            manifest_path = Path(test_dir, ".etg", "entigram.yaml")
            manifest = yaml.safe_load(manifest_path.read_text())
            manifest["governed_artifact_globs"] = ["src/*.py"]
            manifest_path.write_text(yaml.dump(manifest, default_flow_style=False))
            included = Path(test_dir, "src", "app.py")
            included.parent.mkdir()
            included.write_text("included = True\n")
            excluded = Path(test_dir, "src", "workflow.agent")
            excluded.write_text("excluded\n")

            paths = set(governed_artifact_paths(test_dir))

            self.assertIn(included.resolve(), paths)
            self.assertNotIn(excluded.resolve(), paths)
        finally:
            shutil.rmtree(test_dir)

    def test_export_audit_bundle_has_ed25519_signature(self):
        import base64
        import json
        import tempfile
        import shutil
        from pathlib import Path

        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        from entigram.broker import EntigramBroker
        from entigram.injector import inject_entigram_manifest

        test_dir = tempfile.mkdtemp()
        ledger = None
        try:
            inject_entigram_manifest(test_dir, ["Entigram Schemas"], "Codex")
            Path(test_dir, "schema.lds").write_text(self.SCHEMA)
            ledger = LedgerManager(":memory:")
            broker = EntigramBroker(test_dir, ledger=ledger)

            broker.commission_and_record(
                proofs=["tests/test_loop.py passed"],
                agent_id="TestAgent",
            )
            bundle = broker.export_audit_bundle("audit.json")

            self.assertTrue(bundle["ok"])
            self.assertEqual(bundle["signature"]["type"], "ed25519")
            self.assertEqual(bundle["payload"]["bundle_type"], "entigram.audit_bundle.v1")
            self.assertEqual(bundle["payload"]["delivery_status"]["status"], "current")
            self.assertTrue(Path(bundle["signing_key_path"]).exists())
            self.assertEqual(Path(bundle["signing_key_path"]).name, "audit_ed25519_private.pem")
            self.assertTrue(Path(test_dir, "audit.json").exists())

            canonical_payload = json.dumps(
                bundle["payload"],
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            public_key = Ed25519PublicKey.from_public_bytes(
                base64.b64decode(bundle["signature"]["public_key"])
            )
            public_key.verify(
                base64.b64decode(bundle["signature"]["value"]),
                canonical_payload,
            )
        finally:
            if ledger is not None:
                ledger.close()
            shutil.rmtree(test_dir)

    def test_trust_score_labels_intentional_schema_change(self):
        import tempfile
        import shutil
        from pathlib import Path

        from entigram.broker import EntigramBroker
        from entigram.injector import inject_entigram_manifest

        test_dir = tempfile.mkdtemp()
        ledger = None
        try:
            inject_entigram_manifest(test_dir, ["Entigram Schemas"], "Codex")
            Path(test_dir, "schema.lds").write_text(self.SCHEMA)
            ledger = LedgerManager(":memory:")
            broker = EntigramBroker(test_dir, ledger=ledger)

            first = broker.commission_and_record(proofs=["tests/test_loop.py passed"])
            self.assertEqual(
                first["trust_score"]["breakdown"]["schema_change"],
                "initial_baseline",
            )

            Path(test_dir, "schema.lds").write_text(self.SCHEMA + "\nENTITY: Extra\n")
            second = broker.commission_and_record(proofs=["tests/test_loop.py passed"])
            self.assertEqual(
                second["trust_score"]["breakdown"]["schema_change"],
                "changed_and_anchored",
            )
            self.assertGreaterEqual(second["trust_score"]["score"], 0.9)
        finally:
            if ledger is not None:
                ledger.close()
            shutil.rmtree(test_dir)

    def test_delivery_status_reports_current_snapshot(self):
        import tempfile
        import shutil
        from pathlib import Path

        from entigram.broker import EntigramBroker
        from entigram.injector import inject_entigram_manifest

        test_dir = tempfile.mkdtemp()
        ledger = None
        try:
            inject_entigram_manifest(test_dir, ["Entigram Schemas"], "Codex")
            Path(test_dir, "schema.lds").write_text(self.SCHEMA)
            ledger = LedgerManager(":memory:")
            broker = EntigramBroker(test_dir, ledger=ledger)

            broker.commission_and_record(
                proofs=["tests/test_loop.py passed"],
                agent_id="TestAgent",
            )

            status = broker.delivery_status()
            self.assertTrue(status["valid"])
            self.assertFalse(status["needs_recommission"])
            self.assertEqual(status["artifact_changes"], [])
        finally:
            if ledger is not None:
                ledger.close()
            shutil.rmtree(test_dir)

    def test_delivery_status_reports_artifact_drift(self):
        import tempfile
        import shutil
        from pathlib import Path

        from entigram.broker import EntigramBroker
        from entigram.injector import inject_entigram_manifest

        test_dir = tempfile.mkdtemp()
        ledger = None
        try:
            inject_entigram_manifest(test_dir, ["Entigram Schemas"], "Codex")
            Path(test_dir, "schema.lds").write_text(self.SCHEMA)
            ledger = LedgerManager(":memory:")
            broker = EntigramBroker(test_dir, ledger=ledger)

            broker.commission_and_record(
                proofs=["tests/test_loop.py passed"],
                agent_id="TestAgent",
            )
            Path(test_dir, "schema.lds").write_text(self.SCHEMA + "\nENTITY: Extra\n")

            status = broker.delivery_status()
            self.assertFalse(status["valid"])
            self.assertTrue(status["needs_recommission"])
            self.assertTrue(any(
                change["path"] == "schema.lds" and change["status"] == "changed"
                for change in status["artifact_changes"]
            ))
        finally:
            if ledger is not None:
                ledger.close()
            shutil.rmtree(test_dir)

    def test_delivery_status_ignores_generated_manifest_metadata_drift(self):
        import tempfile
        import shutil
        from pathlib import Path

        from entigram.broker import EntigramBroker
        from entigram.injector import inject_entigram_manifest

        test_dir = tempfile.mkdtemp()
        ledger = None
        try:
            inject_entigram_manifest(test_dir, ["Entigram Schemas"], "Codex")
            Path(test_dir, "schema.lds").write_text(self.SCHEMA)
            ledger = LedgerManager(":memory:")
            broker = EntigramBroker(test_dir, ledger=ledger)
            broker.commission_and_record(
                proofs=["tests/test_loop.py passed"], agent_id="TestAgent"
            )

            manifest_path = Path(test_dir, ".etg", "entigram.yaml")
            manifest_path.write_text(manifest_path.read_text() + "\nlast_locked: generated\n")

            status = broker.delivery_status()
            self.assertTrue(status["valid"])
            self.assertEqual(status["governed_artifact_changes"], [])
            self.assertEqual(status["generated_metadata_changes"][0]["path"], ".etg/entigram.yaml")
        finally:
            if ledger is not None:
                ledger.close()
            shutil.rmtree(test_dir)

    def test_delivery_status_treats_governed_manifest_drift_as_blocking(self):
        import tempfile
        import shutil
        from pathlib import Path
        import yaml

        from entigram.broker import EntigramBroker
        from entigram.injector import inject_entigram_manifest

        test_dir = tempfile.mkdtemp()
        ledger = None
        try:
            inject_entigram_manifest(test_dir, ["Entigram Schemas"], "Codex")
            Path(test_dir, "schema.lds").write_text(self.SCHEMA)
            ledger = LedgerManager(":memory:")
            broker = EntigramBroker(test_dir, ledger=ledger)
            broker.commission_and_record(
                proofs=["tests/test_loop.py passed"], agent_id="TestAgent"
            )

            manifest_path = Path(test_dir, ".etg", "entigram.yaml")
            manifest = yaml.safe_load(manifest_path.read_text())
            manifest["lifecycle"]["state"] = "paused"
            manifest_path.write_text(yaml.safe_dump(manifest))

            status = broker.delivery_status()
            self.assertFalse(status["valid"])
            self.assertEqual(status["governed_artifact_changes"][0]["path"], ".etg/entigram.yaml")
            self.assertEqual(status["generated_metadata_changes"], [])
        finally:
            if ledger is not None:
                ledger.close()
            shutil.rmtree(test_dir)

    def test_delivery_status_returns_structured_error_for_invalid_schema_paths(self):
        import tempfile
        import shutil
        from pathlib import Path
        import yaml

        from entigram.broker import EntigramBroker
        from entigram.injector import inject_entigram_manifest

        test_dir = tempfile.mkdtemp()
        ledger = None
        try:
            inject_entigram_manifest(test_dir, ["Entigram Schemas"], "Codex")
            Path(test_dir, "schema.lds").write_text(self.SCHEMA)
            ledger = LedgerManager(":memory:")
            broker = EntigramBroker(test_dir, ledger=ledger)
            broker.commission_and_record(
                proofs=["tests/test_loop.py passed"], agent_id="TestAgent"
            )

            manifest_path = Path(test_dir, ".etg", "entigram.yaml")
            manifest = yaml.safe_load(manifest_path.read_text())
            manifest["schema_paths"] = ["../outside.lds"]
            manifest_path.write_text(yaml.safe_dump(manifest))

            status = broker.delivery_status()
            self.assertFalse(status["valid"])
            self.assertTrue(status["integrity_error"])
            self.assertEqual(status["warden_status"], "tampered")
        finally:
            if ledger is not None:
                ledger.close()
            shutil.rmtree(test_dir)

    def test_delivery_status_reports_unanchored_requested_artifact(self):
        import tempfile
        import shutil
        from pathlib import Path

        from entigram.broker import EntigramBroker
        from entigram.injector import inject_entigram_manifest

        test_dir = tempfile.mkdtemp()
        ledger = None
        try:
            inject_entigram_manifest(test_dir, ["Entigram Schemas"], "Codex")
            Path(test_dir, "schema.lds").write_text(self.SCHEMA)
            Path(test_dir, "local-result.txt").write_text("new output")
            ledger = LedgerManager(":memory:")
            broker = EntigramBroker(test_dir, ledger=ledger)

            broker.commission_and_record(
                proofs=["tests/test_loop.py passed"],
                agent_id="TestAgent",
            )

            status = broker.delivery_status(artifact_paths=["local-result.txt"])
            self.assertFalse(status["valid"])
            self.assertEqual(status["unanchored_artifacts"][0]["path"], "local-result.txt")
            self.assertEqual(status["unanchored_artifacts"][0]["status"], "unanchored")
        finally:
            if ledger is not None:
                ledger.close()
            shutil.rmtree(test_dir)


if __name__ == "__main__":
    unittest.main()
