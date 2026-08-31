"""Shared project identity, recovery, and provenance timeline tests."""

import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import yaml

from entigram.broker import EntigramBroker
from entigram.cli_runner.etg_cli import main
from entigram.cli_runner.etg_cli import get_hydration_vector
from entigram.governance.action_admission import (
    ActionAdmissionEngine,
    agent_attestation_claims,
    action_approval_claims,
    evidence_attestation_claims,
    authority_grant_claims,
)
from entigram.governance.trust import (
    AgentIdentity,
    PersonalIdentity,
    ProjectTrustRegistry,
    TrustRegistryError,
)
from entigram.governance.warden import Warden


NOW = datetime.now(timezone.utc).replace(microsecond=0)
ATTESTATION_EXPIRY = (NOW + timedelta(minutes=5)).isoformat().replace("+00:00", "Z")


def action_contract():
    return {
        "format": "entigram.action-contract.v1",
        "actions": {
            "publish_release": {
                "version": 1,
                "assurance": "mediated",
                "reads": ["release.status"],
                "writes": ["release.status"],
                "authority": {"scopes": ["release.publish"]},
                "preconditions": [{"path": "release.status", "equals": "staged"}],
                "evidence": [{
                    "id": "tests",
                    "verified": True,
                    "freshness_seconds": 300,
                    "issuer_roles": ["evidence_issuer"],
                    "source_kinds": ["ci"],
                }],
                "policy": {"id": "release_policy", "decision": "approval_required"},
                "approval": {"required": True, "roles": ["release_manager"]},
                "postcondition": {"path": "release.status", "equals": "published"},
                "compensation": {"action": "rollback_release"},
            }
        },
    }


def action_request():
    return {
        "request_id": "publish-001",
        "principal": "user:founder",
        "agent_id": "agent:codex",
        "target": {"release_id": "2026.08.16"},
        "context": {"release": {"status": "staged"}},
        "evidence": [{
            "id": "tests",
            "sha256": "a" * 64,
            "verified": True,
            "observed_at": NOW.isoformat().replace("+00:00", "Z"),
        }],
    }


class ProjectTrustTestCase(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.anchor_patch = patch.dict(
            os.environ, {"ENTIGRAM_TRUST_ANCHOR_DIR": str(self.root / "trust-anchors")}
        )
        self.anchor_patch.start()
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        (self.workspace / ".etg").mkdir()
        (self.workspace / ".etg" / "entigram.yaml").write_text(
            yaml.safe_dump({"workspace_schema_version": 1, "state_ledger": ".etg/state.db"})
        )
        (self.workspace / "actions.yaml").write_text(yaml.safe_dump(action_contract(), sort_keys=False))
        self.alice = PersonalIdentity("user:alice", self.root / "keys" / "alice.pem")
        self.bob = PersonalIdentity("user:bob", self.root / "keys" / "bob.pem")
        self.codex = AgentIdentity("agent:codex", "codex", self.root / "agent-keys" / "codex.pem")
        self.alice.create()
        self.bob.create()
        self.codex.create()
        self.registry = ProjectTrustRegistry(self.workspace)
        self.registry.initialize(
            project_id="shared-demo",
            owner_public_key=self.alice.public_record(),
            owner_roles=["trust_admin", "recovery_admin", "authority_issuer", "evidence_issuer"],
            owner_identity=self.alice,
            recovery_quorum=1,
        )
        change = self.registry.make_change(
            operation="add_signer",
            signer_id="user:bob",
            public_key=self.bob.public_record(),
            roles=["release_manager", "recovery_admin", "authority_issuer"],
        )
        self.trust_event = self.registry.apply_change(
            change, [self.registry.approve_change(change, self.alice)]
        )
        agent_change = self.registry.make_change(
            operation="enroll_agent",
            agent_id="agent:codex",
            owner_id="user:alice",
            runtime="codex",
            version="2026.08.16",
            public_key=self.codex.public_record(),
        )
        self.agent_trust_event = self.registry.apply_change(
            agent_change, [self.registry.approve_change(agent_change, self.alice)]
        )
        Warden(str(self.workspace)).lock_fingerprint()

    def tearDown(self):
        self.anchor_patch.stop()
        shutil.rmtree(self.root)

    def _signed_request(self):
        request = action_request()
        engine = ActionAdmissionEngine(self.workspace, now=NOW)
        request["evidence"][0]["attestation"] = self.alice.sign(
            "evidence_attestation",
            evidence_attestation_claims(
                evidence_id="tests",
                sha256=request["evidence"][0]["sha256"],
                observed_at=request["evidence"][0]["observed_at"],
                source={"kind": "ci", "uri": "https://ci.example.test/runs/1"},
            ),
        )
        request["authority"] = self.alice.sign(
            "authority_grant",
            authority_grant_claims(
                principal=request["principal"],
                agent_id=request["agent_id"],
                scopes=["release.publish"],
                expires_at=ATTESTATION_EXPIRY,
                issuer_id="user:alice",
            ),
        )
        digests = engine.action_digests("publish_release", request)
        request["approvals"] = [self.bob.sign(
            "action_approval",
            action_approval_claims(
                action_name="publish_release",
                request_id=request["request_id"],
                approver_id="user:bob",
                role="release_manager",
                action_digest=digests["request_digest"],
                evidence_digest=digests["evidence_digest"],
                expires_at=ATTESTATION_EXPIRY,
            ),
        )]
        request["agent_attestation"] = self.codex.sign(
            "action_request",
            agent_attestation_claims(
                action_name="publish_release",
                request_id=request["request_id"],
                request_digest=engine.action_digests("publish_release", request)["request_digest"],
                agent_id="agent:codex",
                runtime="codex",
                version="2026.08.16",
                issued_at=NOW.isoformat().replace("+00:00", "Z"),
                expires_at=ATTESTATION_EXPIRY,
            ),
        )
        return request

    def test_two_people_sign_without_sharing_a_private_key(self):
        request = self._signed_request()
        with EntigramBroker(self.workspace, seed_synonyms=False) as broker:
            decision = broker.validate_action("publish_release", request)

        self.assertTrue(decision["ok"])
        self.assertEqual(decision["authority"]["mode"], "project_trust")
        self.assertEqual(decision["authority"]["signer_id"], "user:alice")
        self.assertTrue(decision["agent_identity"]["verified"])
        self.assertEqual(decision["agent_identity"]["runtime"], "codex")
        self.assertTrue(decision["approval"]["satisfied"])

    def test_agent_version_requires_explicit_human_enrollment(self):
        request = self._signed_request()
        request["agent_attestation"] = self.codex.sign(
            "action_request",
            agent_attestation_claims(
                action_name="publish_release",
                request_id=request["request_id"],
                request_digest=ActionAdmissionEngine(self.workspace, now=NOW).action_digests(
                    "publish_release", request
                )["request_digest"],
                agent_id="agent:codex",
                runtime="codex",
                version="2026.08.17",
                issued_at=NOW.isoformat().replace("+00:00", "Z"),
                expires_at=ATTESTATION_EXPIRY,
            ),
        )
        denied = ActionAdmissionEngine(self.workspace, now=NOW).validate("publish_release", request)
        self.assertFalse(denied["ok"])
        self.assertEqual(denied["agent_identity"]["code"], "agent_version_unenrolled")

        Warden(str(self.workspace)).unlock()
        change = self.registry.make_change(
            operation="add_agent_version", agent_id="agent:codex", version="2026.08.17"
        )
        self.registry.apply_change(change, [self.registry.approve_change(change, self.alice)])
        Warden(str(self.workspace)).lock_fingerprint()
        admitted = ActionAdmissionEngine(self.workspace, now=NOW).validate("publish_release", request)
        self.assertTrue(admitted["ok"])

        Warden(str(self.workspace)).unlock()
        removal = self.registry.make_change(
            operation="remove_agent_version", agent_id="agent:codex", version="2026.08.16"
        )
        self.registry.apply_change(removal, [self.registry.approve_change(removal, self.alice)])
        Warden(str(self.workspace)).lock_fingerprint()
        request["agent_attestation"] = self.codex.sign(
            "action_request",
            agent_attestation_claims(
                action_name="publish_release",
                request_id=request["request_id"],
                request_digest=ActionAdmissionEngine(self.workspace, now=NOW).action_digests(
                    "publish_release", request
                )["request_digest"],
                agent_id="agent:codex",
                runtime="codex",
                version="2026.08.16",
                issued_at=NOW.isoformat().replace("+00:00", "Z"),
                expires_at=ATTESTATION_EXPIRY,
            ),
        )
        retired = ActionAdmissionEngine(self.workspace, now=NOW).validate("publish_release", request)
        self.assertFalse(retired["ok"])
        self.assertEqual(retired["agent_identity"]["code"], "agent_version_unenrolled")

    def test_shared_trust_rejects_an_unattested_agent_name(self):
        request = self._signed_request()
        request.pop("agent_attestation")

        decision = ActionAdmissionEngine(self.workspace, now=NOW).validate("publish_release", request)

        self.assertFalse(decision["ok"])
        self.assertEqual(decision["agent_identity"]["code"], "agent_attestation_missing")

    def test_shared_trust_rejects_unattested_evidence(self):
        request = self._signed_request()
        request["evidence"][0].pop("attestation")

        decision = ActionAdmissionEngine(self.workspace, now=NOW).validate("publish_release", request)

        self.assertFalse(decision["ok"])
        self.assertTrue(
            any(item.get("code") == "evidence_issuer_unverified" for item in decision["evidence"])
        )

    def test_shared_trust_rejects_evidence_from_an_unapproved_source(self):
        request = self._signed_request()
        request["evidence"][0]["attestation"] = self.alice.sign(
            "evidence_attestation",
            evidence_attestation_claims(
                evidence_id="tests",
                sha256=request["evidence"][0]["sha256"],
                observed_at=request["evidence"][0]["observed_at"],
                source={"kind": "untrusted_export"},
            ),
        )

        decision = ActionAdmissionEngine(self.workspace, now=NOW).validate("publish_release", request)

        self.assertFalse(decision["ok"])
        self.assertTrue(any(item.get("code") == "evidence_source_untrusted" for item in decision["evidence"]))

    def test_broker_consumes_a_shared_agent_attestation_once(self):
        request = self._signed_request()
        with EntigramBroker(self.workspace, seed_synonyms=False) as broker:
            first = broker.validate_action("publish_release", request)
            second = broker.validate_action("publish_release", request)

        self.assertTrue(first["ok"])
        self.assertFalse(second["ok"])
        self.assertEqual(second["agent_identity"]["code"], "agent_attestation_replayed")

    def test_shared_trust_rejects_an_agent_attestation_longer_than_fifteen_minutes(self):
        request = self._signed_request()
        request["agent_attestation"] = self.codex.sign(
            "action_request",
            agent_attestation_claims(
                action_name="publish_release",
                request_id=request["request_id"],
                request_digest=ActionAdmissionEngine(self.workspace, now=NOW).action_digests(
                    "publish_release", request
                )["request_digest"],
                agent_id="agent:codex",
                runtime="codex",
                version="2026.08.16",
                issued_at=NOW.isoformat().replace("+00:00", "Z"),
                expires_at=(NOW + timedelta(minutes=16)).isoformat().replace("+00:00", "Z"),
            ),
        )

        decision = ActionAdmissionEngine(self.workspace, now=NOW).validate("publish_release", request)

        self.assertFalse(decision["ok"])
        self.assertEqual(decision["agent_identity"]["code"], "agent_attestation_ttl_exceeded")

    def test_project_trust_revocation_denies_the_grant_in_every_validator(self):
        request = self._signed_request()
        Warden(str(self.workspace)).unlock()
        change = self.registry.make_change(
            operation="revoke_grant",
            signer_id="user:alice",
            grant_id=request["authority"]["claims"]["grant_id"],
            grant=request["authority"],
        )
        self.registry.apply_change(change, [self.registry.approve_change(change, self.alice)])
        Warden(str(self.workspace)).lock_fingerprint()

        decision = ActionAdmissionEngine(self.workspace, now=NOW).validate("publish_release", request)

        self.assertFalse(decision["ok"])
        self.assertEqual(decision["authority"]["code"], "authority_revoked")

    def test_another_authority_issuer_cannot_revoke_someone_elses_grant(self):
        request = self._signed_request()
        change = self.registry.make_change(
            operation="revoke_grant",
            signer_id="user:bob",
            grant_id=request["authority"]["claims"]["grant_id"],
            grant=request["authority"],
        )

        with self.assertRaisesRegex(TrustRegistryError, "does not match the grant issuer"):
            self.registry.apply_change(change, [self.registry.approve_change(change, self.bob)])

    def test_new_collaborator_must_compare_then_pin_the_signed_root(self):
        clone = self.root / "clone"
        shutil.copytree(self.workspace, clone)
        clone_registry = ProjectTrustRegistry(clone, anchor_path=self.root / "clone-anchor.json")
        root = clone_registry.root_summary()

        with self.assertRaises(TrustRegistryError):
            clone_registry.pin_root(expected_root_digest="0" * 64)

        clone_registry.pin_root(expected_root_digest=root["root_digest"])
        self.assertEqual(clone_registry.load()["project_id"], "shared-demo")

    def test_initialization_refuses_to_overwrite_an_existing_project_anchor(self):
        other_workspace = self.root / "other-workspace"
        (other_workspace / ".etg").mkdir(parents=True)
        other_identity = PersonalIdentity("user:other", self.root / "keys" / "other.pem")
        other_identity.create()
        other_registry = ProjectTrustRegistry(other_workspace)

        with self.assertRaisesRegex(TrustRegistryError, "refuse to overwrite"):
            other_registry.initialize(
                project_id="shared-demo",
                owner_public_key=other_identity.public_record(),
                owner_roles=["trust_admin", "recovery_admin"],
                owner_identity=other_identity,
            )
        self.assertFalse(other_registry.path.exists())

    def test_external_anchor_and_signed_history_reject_registry_key_replacement(self):
        attacker = AgentIdentity("agent:codex", "codex", self.root / "agent-keys" / "attacker.pem")
        attacker.create()
        document = self.registry.load()
        public_key = attacker.public_record()
        document["agents"][0]["keys"] = [{
            "key_id": public_key["key_id"],
            "public_key": public_key["public_key"],
            "state": "active",
            "registered_at": NOW.isoformat().replace("+00:00", "Z"),
        }]
        self.registry.path.write_text(yaml.safe_dump(document, sort_keys=False))
        Warden(str(self.workspace)).unlock()
        Warden(str(self.workspace)).lock_fingerprint()

        with self.assertRaises(TrustRegistryError):
            self.registry.load()

    def test_recovery_replaces_lost_key_after_qualified_approval(self):
        replacement = PersonalIdentity("user:alice", self.root / "keys" / "alice-replacement.pem")
        replacement.create()
        change = self.registry.make_change(
            operation="recover_key",
            signer_id="user:alice",
            public_key=replacement.public_record(),
        )
        event = self.registry.apply_change(change, [self.registry.approve_change(change, self.bob)])

        document = self.registry.load()
        alice = next(item for item in document["signers"] if item["signer_id"] == "user:alice")
        self.assertEqual(sum(key["state"] == "active" for key in alice["keys"]), 1)
        self.assertEqual(event["event_type"], "recover_key")
        self.assertFalse(self.registry.verify(self.alice.sign("test", {}), "test")[0])
        self.assertTrue(self.registry.verify(replacement.sign("test", {}), "test")[0])

    def test_history_and_provenance_show_decisions_and_trust_events(self):
        request = self._signed_request()
        with EntigramBroker(self.workspace, seed_synonyms=False) as broker:
            broker.ledger.record_trust_registry_event(
                self.trust_event,
                registry_digest=self.registry.registry_digest(),
            )
            decision = broker.validate_action("publish_release", request)
            history = broker.ledger.get_workspace_history(limit=20)
            detail = broker.ledger.get_provenance_event(f"action:{decision['decision_id']}")

        kinds = {event["kind"] for event in history}
        self.assertIn("trust", kinds)
        self.assertIn("action", kinds)
        self.assertEqual(detail["outcome"], "admitted")
        self.assertEqual(detail["actor"], "user:alice")
        self.assertEqual(detail["details"]["request"]["agent_id"], "agent:codex")
        self.assertEqual(
            detail["details"]["provenance"]["approval_assertions"][0]["signer_id"],
            "user:bob",
        )
        self.assertEqual(
            detail["details"]["provenance"]["agent_attestation"]["agent_id"],
            "agent:codex",
        )

    def test_warden_requires_the_shared_trust_registry_in_its_lock(self):
        manifest_path = self.workspace / ".etg" / "entigram.yaml"
        manifest = yaml.safe_load(manifest_path.read_text())
        manifest["integrity_fingerprint"].pop("trust_registry_checksum")
        manifest_path.write_text(yaml.safe_dump(manifest))

        warden = Warden(str(self.workspace))
        self.assertFalse(warden.verify_integrity(emit_human=False))
        self.assertEqual(
            warden.last_halt_event.halt_code,
            "TRUST_REGISTRY_INTEGRITY_COVERAGE_MISSING",
        )

    def test_hydration_exposes_only_public_shared_trust_context(self):
        vector = get_hydration_vector(self.workspace, compact=True)
        payload = json.loads(
            vector.split("--- ENTIGRAM HYDRATION SEQUENCE ---\n", 1)[1]
            .split("\n--- SEQUENCE COMPLETE ---", 1)[0]
        )
        summary = payload["ENTIGRAM_BOOT_SUMMARY"]

        self.assertEqual(summary["project_trust"]["project_id"], "shared-demo")
        self.assertEqual(summary["project_trust"]["signers"][1]["signer_id"], "user:bob")
        self.assertEqual(summary["project_trust"]["agents"][0]["allowed_versions"], ["2026.08.16"])
        self.assertIn("etg trust show --dir .", summary["next_commands"])
        self.assertNotIn("private_key", json.dumps(summary))


class ProjectTrustCLITestCase(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.anchor_patch = patch.dict(
            os.environ, {"ENTIGRAM_TRUST_ANCHOR_DIR": str(self.root / "trust-anchors")}
        )
        self.anchor_patch.start()
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        (self.workspace / ".etg").mkdir()
        (self.workspace / ".etg" / "entigram.yaml").write_text(
            yaml.safe_dump({"workspace_schema_version": 1, "state_ledger": ".etg/state.db"})
        )
        (self.workspace / "actions.yaml").write_text(yaml.safe_dump(action_contract(), sort_keys=False))
        (self.workspace / "request.json").write_text(json.dumps(action_request(), indent=2))
        self.stdout = sys.stdout

    def tearDown(self):
        sys.stdout = self.stdout
        self.anchor_patch.stop()
        shutil.rmtree(self.root)

    def run_cli(self, arguments):
        sys.stdout = StringIO()
        with patch.object(sys, "argv", ["etg"] + arguments):
            try:
                main()
                return True, sys.stdout.getvalue()
            except SystemExit as exc:
                return exc.code == 0, sys.stdout.getvalue()

    def test_cli_establishes_shared_trust_and_renders_history(self):
        alice_key = self.root / "keys" / "alice.pem"
        bob_key = self.root / "keys" / "bob.pem"
        codex_key = self.root / "agent-keys" / "codex.pem"
        success, _ = self.run_cli(["identity", "create", "--signer", "user:alice", "--key", str(alice_key)])
        self.assertTrue(success)
        success, _ = self.run_cli(["identity", "create", "--signer", "user:bob", "--key", str(bob_key)])
        self.assertTrue(success)
        success, _ = self.run_cli([
            "identity", "export", "--signer", "user:alice", "--key", str(alice_key),
            "--out", str(self.workspace / "alice-public.json"),
        ])
        self.assertTrue(success)
        success, _ = self.run_cli([
            "identity", "export", "--signer", "user:bob", "--key", str(bob_key),
            "--out", str(self.workspace / "bob-public.json"),
        ])
        self.assertTrue(success)
        success, _ = self.run_cli([
            "identity", "create-agent", "--agent", "agent:codex", "--runtime", "codex", "--key", str(codex_key),
        ])
        self.assertTrue(success)
        success, _ = self.run_cli([
            "identity", "export-agent", "--agent", "agent:codex", "--runtime", "codex", "--key", str(codex_key),
            "--out", str(self.workspace / "codex-public.json"),
        ])
        self.assertTrue(success)
        common = ["--dir", str(self.workspace)]
        success, _ = self.run_cli([
            "trust", *common, "init", "--project", "shared-demo", "--owner", "user:alice",
            "--identity-key", str(alice_key), "--role", "evidence_issuer",
        ])
        self.assertTrue(success)
        success, output = self.run_cli(["trust", *common, "root", "--json"])
        self.assertTrue(success)
        root_digest = json.loads(output)["root"]["root_digest"]
        success, _ = self.run_cli([
            "trust", *common, "pin-root", "--root-digest", root_digest,
        ])
        self.assertTrue(success)
        success, _ = self.run_cli([
            "trust", *common, "add-signer", "--signer", "user:bob", "--public-key", "bob-public.json",
            "--role", "release_manager", "--role", "recovery_admin", "--authorized-by", "user:alice",
            "--identity-key", str(alice_key),
        ])
        self.assertTrue(success)
        success, _ = self.run_cli([
            "trust", *common, "enroll-agent", "--agent", "agent:codex", "--owner", "user:alice",
            "--runtime", "codex", "--version", "2026.08.16", "--public-key", "codex-public.json",
            "--authorized-by", "user:alice", "--identity-key", str(alice_key),
        ])
        self.assertTrue(success)
        Warden(str(self.workspace)).lock_fingerprint()
        success, _ = self.run_cli([
            "action", *common, "grant", "--principal", "user:founder", "--agent", "agent:codex",
            "--scope", "release.publish", "--issuer", "user:alice", "--identity-key", str(alice_key),
            "--expires-at", "2099-01-01T00:00:00Z", "--out", "grant.json",
        ])
        self.assertTrue(success)
        success, _ = self.run_cli([
            "action", *common, "approve", "--name", "publish_release", "--request", "request.json",
            "--approver", "user:bob", "--identity-key", str(bob_key), "--role", "release_manager",
            "--expires-at", "2099-01-01T00:00:00Z", "--out", "approval.json",
        ])
        self.assertTrue(success)
        success, _ = self.run_cli([
            "action", *common, "attest-evidence", "--request", "request.json", "--evidence", "tests",
            "--issuer", "user:alice", "--identity-key", str(alice_key), "--source-kind", "ci",
            "--out", "evidence-attestation.json",
        ])
        self.assertTrue(success)
        success, _ = self.run_cli([
            "action", *common, "attest", "--name", "publish_release", "--request", "request.json",
            "--agent", "agent:codex", "--runtime", "codex", "--version", "2026.08.16",
            "--identity-key", str(codex_key), "--expires-at", ATTESTATION_EXPIRY, "--out", "agent-attestation.json",
        ])
        self.assertTrue(success)
        success, output = self.run_cli([
            "action", *common, "validate", "--name", "publish_release", "--request", "request.json",
            "--grant", "grant.json", "--approval", "approval.json",
            "--agent-attestation", "agent-attestation.json",
            "--evidence-attestation", "evidence-attestation.json", "--json",
        ])
        self.assertTrue(success)
        decision = json.loads(output)
        self.assertEqual(decision["status"], "admitted")
        success, output = self.run_cli(["history", "--dir", str(self.workspace), "--json"])
        self.assertTrue(success)
        events = json.loads(output)
        self.assertTrue(any(event["kind"] == "action" for event in events))
        action_event = next(event for event in events if event["kind"] == "action")
        success, output = self.run_cli([
            "provenance", "--dir", str(self.workspace), "--event", action_event["event_id"], "--json",
        ])
        self.assertTrue(success)
        self.assertEqual(json.loads(output)["event"]["outcome"], "admitted")


if __name__ == "__main__":
    unittest.main()
