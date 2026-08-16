"""Contract, authority, evidence, policy, approval, and ledger tests for v2 action admission."""

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
    LocalActionAuthority,
)
from entigram.governance.warden import Warden


NOW = datetime.now(timezone.utc).replace(microsecond=0)


def contract(policy_decision="allow"):
    return {
        "format": "entigram.action-contract.v1",
        "actions": {
            "publish_release": {
                "version": 1,
                "assurance": "mediated",
                "reads": ["release.status", "release.tests"],
                "writes": ["release.status"],
                "authority": {
                    "scopes": ["release.publish"],
                    "principals": ["user:founder"],
                    "agents": ["agent:codex"],
                },
                "preconditions": [{"path": "release.status", "equals": "staged"}],
                "evidence": [
                    {
                        "id": "release_tests",
                        "verified": True,
                        "freshness_seconds": 300,
                    }
                ],
                "policy": {"id": "release_policy", "decision": policy_decision},
                "approval": {"required": True, "roles": ["release_manager"]},
                "postcondition": {
                    "path": "release.status",
                    "equals": "published",
                    "observation_deadline_seconds": 600,
                },
                "compensation": {"action": "rollback_release"},
            }
        },
    }


def request(observed_at=None, sha256="a" * 64):
    observed_at = observed_at or NOW.isoformat().replace("+00:00", "Z")
    return {
        "request_id": "release-publish-001",
        "principal": "user:founder",
        "agent_id": "agent:codex",
        "target": {"release_id": "release-2026-08-16"},
        "context": {"release": {"status": "staged", "tests": "passed"}},
        "evidence": [
            {
                "id": "release_tests",
                "sha256": sha256,
                "observed_at": observed_at,
                "verified": True,
            }
        ],
    }


class ActionAdmissionTestCase(unittest.TestCase):
    def setUp(self):
        self.workspace = Path(tempfile.mkdtemp())
        (self.workspace / ".etg").mkdir()
        (self.workspace / ".etg" / "entigram.yaml").write_text(
            yaml.safe_dump({"workspace_schema_version": 1, "state_ledger": ".etg/state.db"})
        )
        self.write_contract()
        Warden(str(self.workspace)).lock_fingerprint()
        self.authority = LocalActionAuthority(self.workspace)
        self.authority.initialize()

    def tearDown(self):
        shutil.rmtree(self.workspace)

    def write_contract(self, policy_decision="allow"):
        (self.workspace / "actions.yaml").write_text(yaml.safe_dump(contract(policy_decision), sort_keys=False))

    def lock_contract(self):
        Warden(str(self.workspace)).lock_fingerprint()

    def sign_grant(self, scopes=None):
        return self.authority.issue_grant(
            principal="user:founder",
            agent_id="agent:codex",
            scopes=scopes or ["release.publish"],
            expires_at="2099-08-16T12:00:00Z",
        )

    def approve(self, engine, action_request):
        digests = engine.action_digests("publish_release", action_request)
        return self.authority.issue_approval(
            action_name="publish_release",
            request_id=action_request["request_id"],
            approver_id="user:founder",
            role="release_manager",
            action_digest=digests["request_digest"],
            evidence_digest=digests["evidence_digest"],
            expires_at="2099-08-16T12:00:00Z",
        )

    def test_admits_current_authorized_evidenced_and_approved_action(self):
        engine = ActionAdmissionEngine(self.workspace, now=NOW)
        action_request = request()
        action_request["authority"] = self.sign_grant()
        action_request["approvals"] = [self.approve(engine, action_request)]

        decision = engine.validate("publish_release", action_request)

        self.assertTrue(decision["ok"])
        self.assertEqual(decision["status"], "admitted")
        self.assertEqual(decision["assurance"], "mediated")
        self.assertEqual(decision["authority"]["code"], "authority_valid")
        self.assertEqual(decision["preconditions"][0]["result"], "satisfied")
        self.assertEqual(decision["evidence"][0]["result"], "satisfied")
        self.assertTrue(decision["approval"]["satisfied"])
        self.assertTrue(decision["contract_digest"])
        self.assertTrue(decision["request_digest"])
        self.assertTrue(decision["evidence_digest"])

    def test_denies_an_action_contract_not_covered_by_a_warden_lock(self):
        manifest_path = self.workspace / ".etg" / "entigram.yaml"
        manifest = yaml.safe_load(manifest_path.read_text())
        manifest.pop("integrity_fingerprint")
        manifest_path.write_text(yaml.safe_dump(manifest))

        engine = ActionAdmissionEngine(self.workspace, now=NOW)
        action_request = request()
        action_request["authority"] = self.sign_grant()
        action_request["approvals"] = [self.approve(engine, action_request)]
        decision = engine.validate("publish_release", action_request)

        self.assertFalse(decision["ok"])
        self.assertEqual(decision["status"], "preflight_denied")
        self.assertEqual(decision["reasons"][0]["code"], "model_integrity_failed")
        self.assertEqual(
            decision["reasons"][0]["details"]["halt_code"],
            "ACTION_CONTRACT_INTEGRITY_COVERAGE_MISSING",
        )

    def test_denies_stale_evidence_without_treating_it_as_success(self):
        engine = ActionAdmissionEngine(self.workspace, now=NOW)
        action_request = request(
            observed_at=(NOW - timedelta(hours=1)).isoformat().replace("+00:00", "Z")
        )
        action_request["authority"] = self.sign_grant()
        action_request["approvals"] = [self.approve(engine, action_request)]

        decision = engine.validate("publish_release", action_request)

        self.assertFalse(decision["ok"])
        self.assertEqual(decision["status"], "preflight_denied")
        self.assertIn("evidence_stale", [reason["code"] for reason in decision["reasons"]])

    def test_denies_future_dated_evidence(self):
        engine = ActionAdmissionEngine(self.workspace, now=NOW)
        action_request = request(
            observed_at=(NOW + timedelta(hours=1)).isoformat().replace("+00:00", "Z")
        )
        action_request["authority"] = self.sign_grant()
        action_request["approvals"] = [self.approve(engine, action_request)]

        decision = engine.validate("publish_release", action_request)

        self.assertFalse(decision["ok"])
        self.assertEqual(decision["status"], "preflight_denied")
        self.assertIn("evidence_observed_in_future", [reason["code"] for reason in decision["reasons"]])

    def test_denies_signed_grant_without_a_stable_grant_id(self):
        engine = ActionAdmissionEngine(self.workspace, now=NOW)
        action_request = request()
        grant = self.sign_grant()
        grant["claims"].pop("grant_id")
        action_request["authority"] = self.authority.sign("authority_grant", grant["claims"])
        action_request["approvals"] = [self.approve(engine, action_request)]

        decision = engine.validate("publish_release", action_request)

        self.assertFalse(decision["ok"])
        self.assertEqual(decision["status"], "preflight_denied")
        self.assertEqual(decision["authority"]["code"], "authority_grant_id_invalid")

    def test_returns_conflicted_when_policy_conflict_is_declared(self):
        self.write_contract(policy_decision="conflicted")
        self.lock_contract()
        engine = ActionAdmissionEngine(self.workspace, now=NOW)
        action_request = request()
        action_request["authority"] = self.sign_grant()
        action_request["approvals"] = [self.approve(engine, action_request)]

        decision = engine.validate("publish_release", action_request)

        self.assertFalse(decision["ok"])
        self.assertEqual(decision["status"], "conflicted")
        self.assertIn("policy_conflicted", [reason["code"] for reason in decision["reasons"]])

    def test_policy_required_approval_admits_after_a_qualified_approval(self):
        self.write_contract(policy_decision="approval_required")
        self.lock_contract()
        engine = ActionAdmissionEngine(self.workspace, now=NOW)
        action_request = request()
        action_request["authority"] = self.sign_grant()
        action_request["approvals"] = [self.approve(engine, action_request)]

        decision = engine.validate("publish_release", action_request)

        self.assertTrue(decision["ok"])
        self.assertEqual(decision["status"], "admitted")

    def test_rejects_approval_if_bound_evidence_changes(self):
        engine = ActionAdmissionEngine(self.workspace, now=NOW)
        action_request = request()
        action_request["authority"] = self.sign_grant()
        approval = self.approve(engine, action_request)
        action_request["evidence"][0]["sha256"] = "b" * 64
        action_request["approvals"] = [approval]

        decision = engine.validate("publish_release", action_request)

        self.assertFalse(decision["ok"])
        self.assertEqual(decision["status"], "approval_required")
        approval_codes = [check["code"] for check in decision["approval"]["checks"]]
        self.assertIn("approval_digest_mismatch", approval_codes)

    def test_denies_signed_grant_that_lacks_required_scope(self):
        engine = ActionAdmissionEngine(self.workspace, now=NOW)
        action_request = request()
        action_request["authority"] = self.sign_grant(scopes=["release.read"])
        action_request["approvals"] = [self.approve(engine, action_request)]

        decision = engine.validate("publish_release", action_request)

        self.assertFalse(decision["ok"])
        self.assertEqual(decision["status"], "preflight_denied")
        self.assertEqual(decision["authority"]["code"], "authority_scope_missing")

    def test_broker_appends_each_decision_to_the_ledger(self):
        engine = ActionAdmissionEngine(self.workspace, now=NOW)
        action_request = request()
        action_request["authority"] = self.sign_grant()
        action_request["approvals"] = [self.approve(engine, action_request)]

        with EntigramBroker(str(self.workspace), seed_synonyms=False) as broker:
            first = broker.validate_action("publish_release", action_request)
            second = broker.validate_action("publish_release", action_request)
            records = broker.ledger.get_action_decisions(request_id="release-publish-001")

        self.assertNotEqual(first["decision_id"], second["decision_id"])
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]["decision"]["status"], "admitted")

    def test_broker_rejects_malformed_resolution_without_raising(self):
        with EntigramBroker(str(self.workspace), seed_synonyms=False) as broker:
            with patch.object(broker.warden, "verify_integrity", return_value=True), patch.object(
                broker.warden, "validate_payload", return_value=True
            ):
                self.assertFalse(
                    broker.propose_resolution(
                        "conflict-1", "Entigram_Project", "{malformed", "invalid payload"
                    )
                )

    def test_broker_denies_a_durably_revoked_grant(self):
        engine = ActionAdmissionEngine(self.workspace, now=NOW)
        action_request = request()
        action_request["authority"] = self.sign_grant()
        action_request["approvals"] = [self.approve(engine, action_request)]
        grant_id = action_request["authority"]["claims"]["grant_id"]

        with EntigramBroker(str(self.workspace), seed_synonyms=False) as broker:
            self.assertTrue(broker.ledger.revoke_action_grant(
                grant_id,
                revoked_by="user:founder",
                rationale="Release window closed",
            ))
            decision = broker.validate_action("publish_release", action_request)

        self.assertFalse(decision["ok"])
        self.assertEqual(decision["status"], "preflight_denied")
        self.assertEqual(decision["authority"]["code"], "authority_revoked")

    def test_warden_fingerprints_an_existing_action_contract(self):
        warden = Warden(str(self.workspace))
        warden.lock_fingerprint()
        with (self.workspace / "actions.yaml").open("a") as actions_file:
            actions_file.write("\n# changed after lock\n")

        self.assertFalse(warden.verify_integrity(emit_human=False))
        self.assertEqual(warden.last_halt_event.halt_code, "SCHEMA_INTEGRITY_VIOLATION")

    def test_hydration_loads_action_contract_summary(self):
        vector = get_hydration_vector(self.workspace, compact=True)
        payload = json.loads(
            vector.split("--- ENTIGRAM HYDRATION SEQUENCE ---\n", 1)[1]
            .split("\n--- SEQUENCE COMPLETE ---", 1)[0]
        )
        summary = payload["ENTIGRAM_BOOT_SUMMARY"]

        self.assertEqual(summary["action_admission"]["status"], "valid")
        self.assertEqual(summary["action_admission"]["actions"][0]["name"], "publish_release")
        self.assertIn("etg action validate", " ".join(summary["next_commands"]))


class TestActionAdmissionCLI(unittest.TestCase):
    def setUp(self):
        self.workspace = Path(tempfile.mkdtemp())
        (self.workspace / ".etg").mkdir()
        (self.workspace / ".etg" / "entigram.yaml").write_text(
            yaml.safe_dump({"workspace_schema_version": 1, "state_ledger": ".etg/state.db"})
        )
        (self.workspace / "actions.yaml").write_text(yaml.safe_dump(contract(), sort_keys=False))
        Warden(str(self.workspace)).lock_fingerprint()
        (self.workspace / "request.json").write_text(json.dumps(request(), indent=2))
        self.old_stdout = sys.stdout

    def tearDown(self):
        sys.stdout = self.old_stdout
        shutil.rmtree(self.workspace)

    def run_cli(self, arguments):
        sys.stdout = StringIO()
        with patch.object(sys, "argv", ["etg"] + arguments):
            try:
                main()
                return True, sys.stdout.getvalue()
            except SystemExit as exc:
                return exc.code == 0, sys.stdout.getvalue()

    def test_cli_issues_bound_artifacts_validates_and_lists_ledger_history(self):
        common = ["--dir", str(self.workspace)]
        success, _ = self.run_cli(["action"] + common + ["init-authority"])
        self.assertTrue(success)
        success, _ = self.run_cli(
            [
                "action",
                *common,
                "grant",
                "--principal",
                "user:founder",
                "--agent",
                "agent:codex",
                "--scope",
                "release.publish",
                "--expires-at",
                "2099-08-16T12:00:00Z",
                "--out",
                "grant.json",
            ]
        )
        self.assertTrue(success)
        success, _ = self.run_cli(
            [
                "action",
                *common,
                "approve",
                "--name",
                "publish_release",
                "--request",
                "request.json",
                "--approver",
                "user:founder",
                "--role",
                "release_manager",
                "--expires-at",
                "2099-08-16T12:00:00Z",
                "--out",
                "approval.json",
            ]
        )
        self.assertTrue(success)
        success, output = self.run_cli(
            [
                "action",
                *common,
                "validate",
                "--name",
                "publish_release",
                "--request",
                "request.json",
                "--grant",
                "grant.json",
                "--approval",
                "approval.json",
                "--json",
            ]
        )
        self.assertTrue(success)
        result = json.loads(output)
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "admitted")
        grant = json.loads((self.workspace / "grant.json").read_text())
        success, output = self.run_cli(
            [
                "action",
                *common,
                "revoke",
                "--grant-id",
                grant["claims"]["grant_id"],
                "--by",
                "user:founder",
                "--reason",
                "Release window closed",
                "--json",
            ]
        )
        self.assertTrue(success)
        self.assertTrue(json.loads(output)["created"])
        success, output = self.run_cli(
            [
                "action",
                *common,
                "validate",
                "--name",
                "publish_release",
                "--request",
                "request.json",
                "--grant",
                "grant.json",
                "--approval",
                "approval.json",
                "--json",
            ]
        )
        self.assertFalse(success)
        self.assertEqual(json.loads(output)["authority"]["code"], "authority_revoked")
        success, output = self.run_cli(["action", *common, "decisions", "--json"])
        self.assertTrue(success)
        records = json.loads(output)
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]["status"], "preflight_denied")


if __name__ == "__main__":
    unittest.main()
