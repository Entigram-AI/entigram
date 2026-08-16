import os
import json
import sqlite3
import hashlib
import logging
import mimetypes
import platform
import base64
import math
import re
from itertools import combinations
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from .sqlite_ledger.manager import LedgerManager
from .sqlite_ledger.paths import resolve_ledger_path
from datetime import datetime, timezone
from .governance.warden import Warden
from .workspace_contract import (
    authoritative_schema_paths,
    governed_artifact_paths,
    workspace_relative_path,
)

logger = logging.getLogger(__name__)


class EntigramBroker:
    """
    The semantic governance broker that validates edge-agent state,
    records conflicts, and manages verified cross-domain alignments.
    """
    def __init__(self, target_dir: str, ledger: LedgerManager = None, seed_synonyms: bool = True):
        self.target_dir = Path(target_dir).expanduser().resolve()
        self.etg_dir = self.target_dir / ".etg"
        self.ledger_path = resolve_ledger_path(str(self.target_dir))
        self.ledger = ledger if ledger is not None else LedgerManager(str(self.ledger_path))
        self._owns_ledger = ledger is None
        self.warden = Warden(str(self.target_dir))
        self._packages_cache = None
        
        # Seed initial synonyms if table is empty (Phase 3 Scalability)
        if seed_synonyms:
            self._seed_synonyms()

    def close(self):
        if self._owns_ledger and self.ledger is not None:
            self.ledger.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

    def active_agent_adapter_status(self) -> Dict[str, Any]:
        """Return the workspace-agent enforcement state."""
        from .workspace_lifecycle import active_agent_adapter_status

        return active_agent_adapter_status(self.target_dir)

    def _adapter_enforcement_blocked_delivery(
        self, status: Dict[str, Any]
    ) -> Dict[str, Any]:
        return {
            "valid": False,
            "error": (
                "Delivery refused: workspace-agent lifecycle enforcement is required. "
                + status.get("next_action", "Install the configured agent adapter.")
            ),
            "adapter_enforcement": status,
            "expectation_count": 0,
            "items": [],
            "missing_proof_count": 0,
            "blocked_count": 0,
            "evidence_ids": [],
            "trust_score": {
                "score": 0.0,
                "grade": "F",
                "breakdown": {"active_agent_adapter": 0.0},
            },
        }

    def _with_adapter_enforcement(self, status: Dict[str, Any]) -> Dict[str, Any]:
        enforcement = self.active_agent_adapter_status()
        status["adapter_enforcement"] = enforcement
        if enforcement["ok"]:
            return status

        status["valid"] = False
        status["needs_recommission"] = True
        status["status"] = "degraded"
        recommendations = status.setdefault("recommendations", [])
        requirement = (
            "Install each missing workspace-agent adapter before handoff. "
            + enforcement.get("next_action", "")
        )
        if requirement not in recommendations:
            recommendations.insert(0, requirement)
        return status

    def _seed_synonyms(self):
        """Seeds the ledger with default synonyms if empty."""
        try:
            current_syns = self.ledger.get_synonyms()
            if not current_syns:
                from .governance.negotiator import _SYNONYMS
                for term, syns in _SYNONYMS.items():
                    for s in syns:
                        self.ledger.record_synonym(term, s, 0.95)
        except Exception:
            logger.warning("Failed to seed default synonyms", exc_info=True)

    def check_decision(self, conflict_id: str) -> Optional[Dict[str, Any]]:
        """Checks if a decision has already been recorded for this conflict."""
        return self.ledger.get_resolution(conflict_id)

    def report_conflict(self, conflict_id: str, entity_type: str, proposed_states: Dict[str, Any], agent_id: str):
        """
        Reports a contradiction between agent states.
        Hardened with a Policy Engine to auto-arbitrate low-risk conflicts.
        """
        # Verify Integrity: Prevent reporting if schema contracts have been tampered with
        if not self.warden.verify_integrity():
            print(f"🚨 [SCHEMA_GUARD_HALT] Broker: Refusing to report conflict. Model integrity is compromised.")
            return None

        # Validate payloads against Schema
        for agent, state in proposed_states.items():
            if not self.warden.validate_payload(entity_type, state):
                print(f"🚨 [SCHEMA_GUARD_HALT] Broker: Agent '{agent}' proposed an invalid payload for '{entity_type}'.")
                return None

        from .governance.policy_engine import PolicyEngine
        policy = PolicyEngine()
        
        # 1. Attempt Auto-Arbitration (Tiered Oversight)
        resolution = policy.evaluate_conflict(conflict_id, entity_type, proposed_states)
        if resolution:
            print(f"[ENTIGRAM POLICY] Auto-resolving conflict {conflict_id}: {resolution['rationale']}")
            return self.ledger.record_resolution(
                conflict_id=conflict_id,
                entity_type=entity_type,
                state=json.dumps(resolution['resolved_state']),
                rationale=resolution['rationale']
            )

        # 2. Escalate to Human Ledger (Standard HITL)
        return self.ledger.record_conflict(
            conflict_id=conflict_id,
            entity_type=entity_type,
            proposed_states=json.dumps(proposed_states),
            source_agents=json.dumps([agent_id])
        )

    def propose_resolution(self, conflict_id: str, entity_type: str, proposed_state: str, rationale: str) -> bool:
        """
        Registers a proposed resolution. 
        Note: In Phase 2, this is appended to the ledger as a new version.
        """
        # Verify Integrity
        if not self.warden.verify_integrity():
            return False

        # Validate payload
        try:
            payload = json.loads(proposed_state)
            if not self.warden.validate_payload(entity_type, payload):
                return False
        except json.JSONDecodeError:
            logger.warning("propose_resolution: proposed_state is not valid JSON — rejecting unvalidated payload")
            return False

        return self.ledger.record_resolution(conflict_id, entity_type, proposed_state, f"[AGENT] {rationale}")

    def get_active_packages(self) -> List[str]:
        """Reads the manifest to see what capabilities are active. Result is cached until add_package invalidates it."""
        if self._packages_cache is not None:
            return self._packages_cache
        import yaml
        manifest_path = self.etg_dir / "entigram.yaml"
        if not manifest_path.exists():
            return []
        try:
            with open(manifest_path, 'r') as f:
                manifest = yaml.safe_load(f) or {}
            self._packages_cache = manifest.get('packages', [])
            return self._packages_cache
        except Exception as e:
            print(f"Warning: Could not read manifest: {e}")
            return []

    def add_package(self, package_name: str) -> bool:
        """
        Adds a package to the project's entigram.yaml manifest.
        """
        import yaml
        manifest_path = self.etg_dir / "entigram.yaml"
        if not manifest_path.exists():
            return False

        try:
            with open(manifest_path, 'r') as f:
                manifest = yaml.safe_load(f) or {}

            packages = manifest.get('packages', [])
            
            # Handle both list and dict formats for backwards compatibility
            if isinstance(packages, list):
                if package_name not in packages:
                    packages.append(package_name)
                    manifest['packages'] = packages
                    manifest['last_updated'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    with open(manifest_path, 'w') as f:
                        yaml.dump(manifest, f, default_flow_style=False)
            elif isinstance(packages, dict):
                if package_name not in packages:
                    packages[package_name] = "latest"
                    manifest['packages'] = packages
                    manifest['last_updated'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    with open(manifest_path, 'w') as f:
                        yaml.dump(manifest, f, default_flow_style=False)

            self._packages_cache = None  # invalidate cache
            return True
        except Exception as e:
            print(f"Error adding package: {e}")
            return False

    def validate_model(self) -> Dict[str, Any]:
        """
        Performs a 'Semantic Governance' check on the logical models using the SchemaLinter.
        Also runs MVD (Minimum Viable Domain) semantic validation and
        expectation-to-test mapping: warns when validation_check paths cannot be found.
        """
        from .schema_compiler.linter import SchemaLinter
        from .governance.viability import MVDValidator
        from .governance.commissioner import Commissioner
        import os

        schema_path = self.target_dir / "schema.lds"
        if not schema_path.exists():
            schema_path = self.target_dir / "draft_schema.lds"

        if not schema_path.exists():
            return {"valid": False, "error": "Missing schema.lds or draft_schema.lds"}

        try:
            schema_text = schema_path.read_text()

            # 1. Structural Linting
            linter = SchemaLinter(schema_text)
            errors = linter.lint()

            # 2. Semantic MVD Validation
            mvd_validator = MVDValidator(schema_text)
            mvd_issues = mvd_validator.validate()

            all_issues = errors + mvd_issues

            # Filter for hard errors that block compilation
            critical_errors = [i for i in all_issues if i.get('severity') == 'ERROR' or 'code' not in i]

            if critical_errors:
                return {
                    "valid": False,
                    "error": critical_errors[0].get('message', str(critical_errors[0])),
                    "all_errors": all_issues
                }

            # 3. Expectation-to-Test Mapping: warn on unreachable validation_check paths
            commissioner = Commissioner.from_workspace(str(self.target_dir))
            expectation_warnings = []
            for exp in commissioner.expectations:
                check = (exp.validation_check or "").strip()
                if not check:
                    expectation_warnings.append({
                        "severity": "WARN",
                        "code": "E2T_NO_CHECK",
                        "expectation": exp.name,
                        "message": f"Expectation '{exp.name}' has no validation_check — handoff proof cannot be automated.",
                    })
                    continue
                # Detect file-based checks (path::test or path only, not shell commands)
                check_file = check.split("::")[0].strip()
                is_likely_file = (
                    check_file.endswith(".py")
                    or check_file.startswith("tests/")
                    or check_file.startswith("test_")
                )
                if is_likely_file:
                    abs_check = self.target_dir / check_file
                    if not abs_check.exists():
                        expectation_warnings.append({
                            "severity": "WARN",
                            "code": "E2T_MISSING_FILE",
                            "expectation": exp.name,
                            "validation_check": check,
                            "message": (
                                f"Expectation '{exp.name}': validation_check path "
                                f"'{check_file}' does not exist — run will always fail."
                            ),
                        })

            # If no critical errors, model is 'valid' but might have warnings
            entities, rels = linter.parser.parse()
            all_warnings = [i for i in mvd_issues if i.get('severity') != 'ERROR'] + expectation_warnings
            return {
                "valid": True,
                "entity_count": len(entities),
                "relationship_count": len(rels),
                "warnings": all_warnings,
                "expectation_warnings": expectation_warnings,
            }
        except Exception as e:
            import traceback
            print(f"validate_model raised unexpectedly:\n{traceback.format_exc()}")
            return {"valid": False, "error": str(e)}

    def commission(
        self,
        proofs: List[str] = None,
        blocked_checks: List[str] = None,
        agent_id: Optional[str] = None,
        expectation_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Runs the Commissioner pre-handoff checklist for modeled expectations.
        Automatically writes delivery evidence to the ledger when all pass.
        """
        from .governance.commissioner import Commissioner

        commissioner = Commissioner.from_workspace(
            str(self.target_dir), ledger=self.ledger
        )
        return commissioner.build_checklist(
            proofs=proofs,
            blocked_checks=blocked_checks,
            agent_id=agent_id,
            expectation_name=expectation_name,
        )

    def format_commission(self, checklist: Dict[str, Any]) -> str:
        from .governance.commissioner import Commissioner

        return Commissioner("").format_checklist(checklist)

    def expectation_guard(
        self,
        proofs: List[str] = None,
        blocked_checks: List[str] = None,
        agent_id: Optional[str] = None,
        expectation_name: Optional[str] = None,
        run_validation_checks: bool = True,
        timeout: int = 120,
    ) -> Dict[str, Any]:
        """
        Runs the out-of-the-box expectation guard for agent pre-handoff
        verification. Missing proofs are resolved by executing validation_check
        commands and recording durable evidence.
        """
        from .governance.expectation_guard import ExpectationGuard

        guard = ExpectationGuard(str(self.target_dir), ledger=self.ledger)
        return guard.verify(
            proofs=proofs,
            blocked_checks=blocked_checks,
            agent_id=agent_id,
            expectation_name=expectation_name,
            run_validation_checks=run_validation_checks,
            timeout=timeout,
        )

    def format_expectation_guard(self, result: Dict[str, Any]) -> str:
        from .governance.expectation_guard import ExpectationGuard

        return ExpectationGuard(str(self.target_dir), ledger=self.ledger).format_result(result)

    def validate_action(self, action_name: str, request: Dict[str, Any]) -> Dict[str, Any]:
        """Evaluate and append an evidence-backed action-admission decision.

        This method has no external side effect. It records the exact contract,
        evidence, authority, and policy decision that a future mediated action
        executor must verify again immediately before operating on a target.
        """
        from .governance.action_admission import ActionAdmissionEngine
        from .sqlite_ledger.manager import ActionAttestationReplayError

        decision = ActionAdmissionEngine(
            self.target_dir,
            revocation_checker=self.ledger.is_action_grant_revoked,
            attestation_consumed_checker=self.ledger.is_action_attestation_consumed,
        ).validate(action_name, request)
        try:
            decision["ledger_id"] = self.ledger.record_action_decision(decision)
        except ActionAttestationReplayError:
            decision["ok"] = False
            decision["status"] = "preflight_denied"
            decision.setdefault("reasons", []).append(
                {
                    "code": "agent_attestation_replayed",
                    "message": "This agent attestation was consumed by another admission attempt.",
                }
            )
            decision.setdefault("remediation", []).append(
                "Create a fresh, short-lived agent attestation before retrying."
            )
            decision["ledger_id"] = self.ledger.record_action_decision(
                decision, consume_attestation=False
            )
        return decision

    @staticmethod
    def format_action_decision(decision: Dict[str, Any]) -> str:
        lines = [
            f"Action admission: {decision.get('action_name', 'unknown')}",
            f"Request: {decision.get('request_id', 'unknown')}",
            f"Status: {decision.get('status', 'unknown')}",
            f"Assurance: {decision.get('assurance', 'unknown')}",
        ]
        authority = decision.get("authority") or {}
        if authority:
            lines.append(f"Authority: {authority.get('code', 'unknown')} ({authority.get('message', '')})")
        reasons = decision.get("reasons") or []
        if reasons:
            lines.append("Reasons:")
            lines.extend(f"  - [{reason.get('code', 'unknown')}] {reason.get('message', '')}" for reason in reasons)
        remediation = decision.get("remediation") or []
        if remediation:
            lines.append("Next steps:")
            lines.extend(f"  - {item}" for item in remediation)
        return "\n".join(lines)

    def _artifact_path_for_storage(self, path: Path) -> str:
        try:
            return path.resolve().relative_to(self.target_dir).as_posix()
        except ValueError:
            return str(path.resolve())

    def _capture_artifact(self, artifact_path: str, role: str) -> Dict[str, Any]:
        path = Path(artifact_path).expanduser()
        if not path.is_absolute():
            path = self.target_dir / path
        if not path.exists() or not path.is_file():
            return {"path": artifact_path, "artifact_role": role, "missing": True}

        data = path.read_bytes()
        content_type = mimetypes.guess_type(path.name)[0]
        return {
            "path": self._artifact_path_for_storage(path),
            "artifact_role": role,
            "sha256": hashlib.sha256(data).hexdigest(),
            "size_bytes": len(data),
            "content_type": content_type,
            "source_ref": self._artifact_path_for_storage(path),
        }

    def _default_delivery_artifacts(self) -> List[Tuple[str, str]]:
        artifacts = {
            ("schema.lds", "schema_contract"),
            ("schema.ttl", "ontology_contract"),
            ("draft_schema.lds", "draft_schema_contract"),
            ("draft_schema.ttl", "draft_ontology_contract"),
            ("actions.yaml", "action_contract"),
            ("ontology/schema.ttl", "ontology_contract"),
            (".etg/entigram.yaml", "workspace_manifest"),
        }
        roles_by_path = dict(artifacts)
        for path in authoritative_schema_paths(
            self.target_dir,
            require_existing=False,
        ):
            roles_by_path[workspace_relative_path(self.target_dir, path)] = "schema_contract"
        for path in governed_artifact_paths(self.target_dir):
            roles_by_path.setdefault(
                workspace_relative_path(self.target_dir, path),
                "governed_source",
            )
        return sorted(roles_by_path.items())

    def _record_delivery_artifacts(
        self,
        artifact_paths: Optional[List[str]] = None,
        artifact_role: str = "delivery_artifact",
    ) -> Tuple[List[int], List[str]]:
        artifact_ids: List[int] = []
        missing_artifacts: List[str] = []
        seen = set()

        candidates: List[Tuple[str, str, bool]] = [
            (path, role, False) for path, role in self._default_delivery_artifacts()
        ]
        candidates.extend((path, artifact_role, True) for path in (artifact_paths or []))

        for artifact_path, role, required in candidates:
            captured = self._capture_artifact(artifact_path, role)
            if captured.get("missing"):
                if required:
                    missing_artifacts.append(artifact_path)
                continue

            key = (captured["path"], captured["artifact_role"], captured["sha256"])
            if key in seen:
                continue
            seen.add(key)

            row_id = self.ledger.record_delivery_artifact(**captured)
            if row_id is not None:
                artifact_ids.append(row_id)

        return artifact_ids, missing_artifacts

    def _compute_trust_score(
        self,
        checklist: Dict[str, Any],
        warden_ok: bool,
        schema_hash: Optional[str],
        proofs: Optional[List[str]],
    ) -> Dict[str, Any]:
        """
        Computes a confidence score [0.0–1.0] for a delivery based on:
        - All expectations passed (40 pts)
        - Warden integrity (20 pts)
        - Schema hash matches last snapshot (20 pts)
        - Evidence exists in ledger for each passing expectation (20 pts)
        """
        score = 0.0
        breakdown = {}

        # 1. All commissioner expectations satisfied
        if checklist.get("valid") and checklist.get("missing_proof_count", 1) == 0:
            score += 0.40
            breakdown["expectations_passed"] = 0.40
        else:
            breakdown["expectations_passed"] = 0.0

        # 2. Warden integrity
        if warden_ok:
            score += 0.20
            breakdown["warden_intact"] = 0.20
        else:
            breakdown["warden_intact"] = 0.0

        # 3. Schema continuity (hash matches last snapshot or this delivery
        # intentionally establishes a new baseline)
        last_snap = self.ledger.get_latest_snapshot()
        if last_snap and schema_hash and last_snap.get("schema_hash") == schema_hash:
            score += 0.20
            breakdown["schema_stable"] = 0.20
            breakdown["schema_change"] = "unchanged"
        elif not last_snap:
            # First delivery — no prior baseline; give benefit of the doubt
            score += 0.20
            breakdown["schema_stable"] = 0.20
            breakdown["schema_change"] = "initial_baseline"
        else:
            score += 0.10
            breakdown["schema_stable"] = 0.10
            breakdown["schema_change"] = "changed_and_anchored"

        # 4. Ledger evidence for each passing expectation
        passing_items = [i for i in checklist.get("items", []) if i["status"] == "passed"]
        if passing_items:
            covered = sum(
                1 for item in passing_items
                if self.ledger.get_delivery_evidence(
                    expectation_name=item["name"], passed_only=True, limit=1
                )
            )
            evidence_ratio = covered / len(passing_items)
            evidence_score = round(evidence_ratio * 0.20, 4)
            score += evidence_score
            breakdown["ledger_evidence"] = evidence_score
        else:
            breakdown["ledger_evidence"] = 0.0

        return {
            "score": round(min(score, 1.0), 4),
            "grade": (
                "A" if score >= 0.90 else
                "B" if score >= 0.75 else
                "C" if score >= 0.60 else
                "D" if score >= 0.40 else "F"
            ),
            "breakdown": breakdown,
        }

    def _load_or_create_audit_signing_key(self, signing_key_path: Optional[str] = None) -> Tuple[Ed25519PrivateKey, Path]:
        key_path = Path(signing_key_path).expanduser() if signing_key_path else self.etg_dir / "audit_ed25519_private.pem"
        if not key_path.is_absolute():
            key_path = self.target_dir / key_path

        if key_path.exists():
            loaded_key = serialization.load_pem_private_key(
                key_path.read_bytes(),
                password=None,
            )
            if not isinstance(loaded_key, Ed25519PrivateKey):
                raise ValueError(f"Audit signing key is not an Ed25519 private key: {key_path}")
            return loaded_key, key_path

        key_path.parent.mkdir(parents=True, exist_ok=True)
        private_key = Ed25519PrivateKey.generate()
        key_path.write_bytes(
            private_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption(),
            )
        )
        try:
            key_path.chmod(0o600)
        except OSError:
            pass
        return private_key, key_path

    def export_audit_bundle(
        self,
        out_path: Optional[str] = None,
        signing_key_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Exports a deterministic, content-addressed audit bundle for the current
        workspace. The SHA-256 digest is computed over canonical JSON and can
        be used to detect tampering after export.
        """
        manifest_path = self.target_dir / ".etg" / "entigram.yaml"
        manifest = {}
        if manifest_path.exists():
            try:
                import yaml
                manifest = yaml.safe_load(manifest_path.read_text()) or {}
            except Exception as exc:
                manifest = {"error": f"failed to read manifest: {exc}"}

        snapshot = self.ledger.get_latest_snapshot()
        artifact_ids = snapshot.get("artifact_ids", []) if snapshot else []
        payload = {
            "bundle_type": "entigram.audit_bundle.v1",
            "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "project_dir": str(self.target_dir),
            "environment": {
                "python": platform.python_version(),
                "platform": platform.platform(),
            },
            "manifest": manifest,
            "delivery_status": self.delivery_status(),
            "latest_snapshot": snapshot,
            "delivery_evidence": self.ledger.get_delivery_evidence(limit=500),
            "delivery_artifacts": self.ledger.get_delivery_artifacts_by_ids(artifact_ids),
            "semantic_alignments": self.ledger.get_alignments(trusted_only=False),
            "pending_conflicts": self.ledger.get_pending_conflicts(),
            "resolutions": self.ledger.get_all_resolutions(),
            "action_decisions": self.ledger.get_action_decisions(limit=500),
            "trust_registry_events": self.ledger.get_trust_registry_events(limit=500),
        }
        canonical_payload = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        digest = hashlib.sha256(canonical_payload).hexdigest()
        private_key, key_path = self._load_or_create_audit_signing_key(signing_key_path)
        signature = private_key.sign(canonical_payload)
        public_key_bytes = private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        key_id = hashlib.sha256(public_key_bytes).hexdigest()
        bundle = {
            "ok": True,
            "sha256": digest,
            "signature": {
                "type": "ed25519",
                "key_id": key_id,
                "public_key": base64.b64encode(public_key_bytes).decode("utf-8"),
                "value": base64.b64encode(signature).decode("utf-8"),
            },
            "signing_key_path": str(key_path),
            "payload": payload,
        }

        if out_path:
            destination = Path(out_path).expanduser()
            if not destination.is_absolute():
                destination = self.target_dir / destination
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(json.dumps(bundle, indent=2, sort_keys=True))
            bundle["path"] = str(destination)

        return bundle

    def commission_and_record(
        self,
        proofs: List[str] = None,
        blocked_checks: List[str] = None,
        agent_id: Optional[str] = None,
        expectation_name: Optional[str] = None,
        artifact_paths: Optional[List[str]] = None,
        artifact_role: str = "delivery_artifact",
    ) -> Dict[str, Any]:
        """
        Runs commission and, if all pass, writes a delivery snapshot anchoring
        the current schema state. This is the 'last known good' baseline.
        Attaches a trust score to every delivery.
        """
        adapter_status = self.active_agent_adapter_status()
        if not adapter_status["ok"]:
            return self._adapter_enforcement_blocked_delivery(adapter_status)

        checklist = self.commission(
            proofs=proofs,
            blocked_checks=blocked_checks,
            agent_id=agent_id,
            expectation_name=expectation_name,
        )

        # Compute trust score even on failure so caller can see the gap
        warden_ok = self.warden.verify_integrity()
        pending_contract_change = self.warden.has_pending_contract_change()
        warden_status = "intact" if warden_ok else "tampered"
        schema_hash = None
        schema_path = self.target_dir / "schema.lds"
        if schema_path.exists():
            schema_hash = hashlib.sha256(schema_path.read_bytes()).hexdigest()[:16]

        checklist["trust_score"] = self._compute_trust_score(
            checklist, warden_ok, schema_hash, proofs
        )

        if not warden_ok or pending_contract_change:
            checklist["valid"] = False
            if pending_contract_change:
                checklist["warden_error"] = {
                    "message": (
                        "an unlocked contract change is pending; use broker handoff "
                        "--accept-contract-change after reviewing the contract diff"
                    )
                }
            else:
                checklist["warden_error"] = (
                    self.warden.last_halt_event.to_dict()
                    if self.warden.last_halt_event is not None
                    else {"message": "schema integrity check failed"}
                )
            return checklist

        if checklist["valid"]:
            artifact_ids, missing_artifacts = self._record_delivery_artifacts(
                artifact_paths=artifact_paths,
                artifact_role=artifact_role,
            )
            if missing_artifacts:
                checklist["valid"] = False
                checklist["artifact_errors"] = [
                    f"Missing artifact: {path}" for path in missing_artifacts
                ]
                return checklist

            snapshot_id = f"delivery-{datetime.now().strftime('%Y%m%dT%H%M%S')}"
            if agent_id:
                snapshot_id += f"-{agent_id}"

            self.ledger.record_delivery_snapshot(
                snapshot_id=snapshot_id,
                expectation_count=checklist["expectation_count"],
                missing_proof_count=0,
                schema_hash=schema_hash,
                agent_id=agent_id,
                warden_status=warden_status,
                evidence_ids=checklist.get("evidence_ids", []),
                artifact_ids=artifact_ids,
                metadata={
                    "proofs_provided": len(proofs) if proofs else 0,
                    "blocked_checks": blocked_checks or [],
                    "expectation_name": expectation_name,
                    "artifact_count": len(artifact_ids),
                    "trust_score": checklist["trust_score"]["score"],
                },
            )
            checklist["snapshot_id"] = snapshot_id
            checklist["artifact_ids"] = artifact_ids
            try:
                from .workspace_lifecycle import establish_active_change_baseline

                checklist["active_change_baseline"] = establish_active_change_baseline(
                    self.target_dir,
                    reason="broker_handoff",
                    snapshot_id=snapshot_id,
                )
            except Exception as exc:
                checklist["valid"] = False
                checklist["active_change_baseline"] = {"ok": False, "error": str(exc)}

        return checklist

    def delivery_status(
        self,
        artifact_paths: Optional[List[str]] = None,
        artifact_role: str = "delivery_artifact",
    ) -> Dict[str, Any]:
        """
        Compares the current workspace against the latest delivery snapshot.
        This is source-control-neutral drift detection over modeled expectations,
        Warden integrity, and anchored local artifacts.
        """
        snapshot = self.ledger.get_latest_snapshot()
        if not snapshot:
            return self._with_adapter_enforcement({
                "valid": False,
                "needs_recommission": True,
                "status": "no_snapshot",
                "snapshot": None,
                "artifact_changes": [],
                "unanchored_artifacts": [],
                "recommendations": [
                    "Run `etg broker deliver --proof ...` after proof is available.",
                ],
            })

        from .governance.commissioner import Commissioner

        commissioner = Commissioner.from_workspace(
            str(self.target_dir), ledger=self.ledger
        )
        checklist = commissioner.build_checklist(persist_evidence=False)
        warden_ok = self.warden.verify_integrity()
        warden_status = "intact" if warden_ok else "tampered"
        current_schema_hash = None
        schema_path = self.target_dir / "schema.lds"
        if schema_path.exists():
            current_schema_hash = hashlib.sha256(schema_path.read_bytes()).hexdigest()[:16]

        artifact_changes = []
        anchored_artifacts = self.ledger.get_delivery_artifacts_by_ids(
            snapshot.get("artifact_ids", [])
        )
        anchored_keys = set()

        for artifact in anchored_artifacts:
            path = artifact.get("path")
            role = artifact.get("artifact_role") or "delivery_artifact"
            anchored_keys.add((path, role))
            current = self._capture_artifact(path, role)
            if current.get("missing"):
                artifact_changes.append({
                    "path": path,
                    "artifact_role": role,
                    "status": "missing",
                    "previous_sha256": artifact.get("sha256"),
                    "current_sha256": None,
                })
            elif current.get("sha256") != artifact.get("sha256"):
                artifact_changes.append({
                    "path": path,
                    "artifact_role": role,
                    "status": "changed",
                    "previous_sha256": artifact.get("sha256"),
                    "current_sha256": current.get("sha256"),
                })

        missing_artifact_records = [
            artifact_id for artifact_id in snapshot.get("artifact_ids", [])
            if artifact_id not in {artifact["id"] for artifact in anchored_artifacts}
        ]
        for artifact_id in missing_artifact_records:
            artifact_changes.append({
                "path": None,
                "artifact_role": None,
                "status": "missing_record",
                "artifact_id": artifact_id,
            })

        unanchored_artifacts = []
        current_candidates = list(self._default_delivery_artifacts())
        current_candidates.extend(
            (path, artifact_role) for path in (artifact_paths or [])
        )
        seen_current = set()
        for artifact_path, role in current_candidates:
            current = self._capture_artifact(artifact_path, role)
            if current.get("missing"):
                if artifact_path in (artifact_paths or []):
                    unanchored_artifacts.append({
                        "path": artifact_path,
                        "artifact_role": role,
                        "status": "missing",
                    })
                continue
            key = (current["path"], current["artifact_role"])
            if key in seen_current:
                continue
            seen_current.add(key)
            if key not in anchored_keys:
                unanchored_artifacts.append({
                    "path": current["path"],
                    "artifact_role": current["artifact_role"],
                    "status": "unanchored",
                    "sha256": current["sha256"],
                })

        expectation_count_changed = (
            checklist.get("expectation_count") != snapshot.get("expectation_count")
        )
        schema_changed = (
            bool(snapshot.get("schema_hash"))
            and current_schema_hash != snapshot.get("schema_hash")
        )
        needs_recommission = any([
            not checklist.get("valid", False),
            warden_status != snapshot.get("warden_status"),
            warden_status != "intact",
            expectation_count_changed,
            schema_changed,
            bool(artifact_changes),
            bool(unanchored_artifacts),
        ])

        recommendations = []
        if not checklist.get("valid", False):
            recommendations.append("Resolve missing or blocked expectation proof.")
        if warden_status != "intact":
            recommendations.append("Inspect schema contract integrity before handoff.")
        if expectation_count_changed:
            recommendations.append("Recommission because modeled expectations changed.")
        if schema_changed:
            recommendations.append("Recommission because the schema contract hash changed.")
        if artifact_changes:
            recommendations.append("Recommission because anchored artifacts drifted.")
        if unanchored_artifacts:
            recommendations.append("Include new local artifacts with `etg broker deliver --artifact PATH`.")
        if not recommendations:
            recommendations.append("No recommission needed; latest delivery snapshot still matches.")

        return self._with_adapter_enforcement({
            "valid": not needs_recommission,
            "needs_recommission": needs_recommission,
            "status": "needs_recommission" if needs_recommission else "current",
            "snapshot": snapshot,
            "warden_status": warden_status,
            "current_schema_hash": current_schema_hash,
            "expectation_count": checklist.get("expectation_count", 0),
            "missing_proof_count": checklist.get("missing_proof_count", 0),
            "blocked_count": checklist.get("blocked_count", 0),
            "artifact_count": len(anchored_artifacts),
            "artifact_changes": artifact_changes,
            "unanchored_artifacts": unanchored_artifacts,
            "recommendations": recommendations,
        })

    def format_delivery_status(self, status: Dict[str, Any]) -> str:
        if status.get("status") == "no_snapshot":
            return "\n".join([
                "Delivery status: no delivery snapshot found.",
                "Recommendation: run `etg broker deliver --proof ...` after proof is available.",
            ])

        snapshot = status.get("snapshot") or {}
        lines = [
            (
                "Delivery status: current"
                if status.get("valid")
                else (
                    "Delivery status: governance enforcement required"
                    if status.get("status") == "degraded"
                    else "Delivery status: recommission required"
                )
            ),
            f"Snapshot: {snapshot.get('snapshot_id', 'unknown')}",
            f"Warden: {status.get('warden_status', 'unknown')}",
            (
                "Expectations: "
                f"{status.get('expectation_count', 0)} modeled, "
                f"{status.get('missing_proof_count', 0)} missing proof, "
                f"{status.get('blocked_count', 0)} blocked"
            ),
            (
                "Artifacts: "
                f"{status.get('artifact_count', 0)} anchored, "
                f"{len(status.get('artifact_changes', []))} drifted, "
                f"{len(status.get('unanchored_artifacts', []))} unanchored"
            ),
        ]

        enforcement = status.get("adapter_enforcement") or {}
        if enforcement:
            lines.append(
                "Workspace agents: "
                f"{', '.join(enforcement.get('active_agents') or ['unconfigured'])} "
                f"({enforcement.get('status', 'unknown')})"
            )
            lines.append(
                "Operating agent: "
                f"{enforcement.get('operating_agent') or 'unresolved'} "
                f"({enforcement.get('operating_agent_source') or 'unresolved'})"
            )

        for change in status.get("artifact_changes", []):
            path = change.get("path") or f"artifact_id={change.get('artifact_id')}"
            lines.append(f"  {change.get('status')}: {path}")
        for artifact in status.get("unanchored_artifacts", []):
            lines.append(f"  {artifact.get('status')}: {artifact.get('path')}")

        lines.append("Recommendations:")
        lines.extend(f"  - {item}" for item in status.get("recommendations", []))
        return "\n".join(lines)

    def record_improvement_proposal(
        self,
        title: str,
        affected_model: str,
        proposed_change: Dict[str, Any],
        rationale: str,
        *,
        expected_benefit: Optional[str] = None,
        created_by: Optional[str] = None,
        lifecycle_status: str = "Proposed",
    ) -> Optional[int]:
        """
        Records an agent-discovered improvement proposal to the durable ledger.
        Proposals move through: Proposed -> Reviewed -> Implemented -> Verified.
        """
        return self.ledger.record_improvement_proposal(
            title=title,
            affected_model=affected_model,
            proposed_change=proposed_change,
            rationale=rationale,
            expected_benefit=expected_benefit,
            created_by=created_by,
            lifecycle_status=lifecycle_status,
        )

    def record_lesson(
        self,
        lesson: str,
        *,
        source_task: Optional[str] = None,
        reusable_rule: Optional[str] = None,
        confidence: float = 1.0,
        agent_id: Optional[str] = None,
    ) -> Optional[int]:
        """
        Persists a reusable lesson derived from a delivery session.
        Lessons accumulate as institutional memory across all agents.
        """
        return self.ledger.record_lesson(
            lesson=lesson,
            source_task=source_task,
            reusable_rule=reusable_rule,
            confidence=confidence,
            agent_id=agent_id,
        )

    def authorize_alignment(
        self,
        source_domain: str,
        target_domain: str,
        source_concept: str,
        target_concept: str,
        confidence: float,
        rationale: str,
        _defer_sync: bool = False,
        *,
        verified_by: str = "EntigramBroker",
        evidence_type: str = "human_review",
        source_artifact: Optional[str] = None,
        validate_schema: bool = True,
    ):
        """
        Authorizes a semantic alignment between two isolated domains.
        Pass _defer_sync=True when calling in a batch loop; call sync_all_ontologies() once after the loop.
        """
        error = self._validate_alignment_authorization(
            source_domain,
            target_domain,
            source_concept,
            target_concept,
            confidence,
            rationale,
            validate_schema=validate_schema,
        )
        if error:
            print(f"Alignment authorization rejected: {error}")
            return False
        result = self.ledger.record_alignment(
            source_domain,
            target_domain,
            source_concept,
            target_concept,
            confidence,
            rationale,
            evidence_type=evidence_type,
            source_artifact=source_artifact,
            lifecycle_status="verified",
            verified=True,
            verified_by=verified_by,
            human_review_confidence=confidence,
        )
        if result and not _defer_sync:
            self.sync_all_ontologies()
        return result

    def _validate_alignment_authorization(
        self,
        source_domain: str,
        target_domain: str,
        source_concept: str,
        target_concept: str,
        confidence: float,
        rationale: str,
        *,
        validate_schema: bool,
    ) -> Optional[str]:
        values = (source_domain, target_domain, source_concept, target_concept, rationale)
        if any(not isinstance(value, str) or not value.strip() for value in values):
            return "domains, concepts, and rationale must be non-empty strings"
        if source_domain == target_domain:
            return "source and target domains must differ"
        if not isinstance(confidence, (int, float)) or not math.isfinite(confidence) or not 0.8 <= confidence <= 1.0:
            return "confidence must be a finite value between 0.8 and 1.0"
        if not self.warden.verify_integrity():
            return "schema integrity check failed"
        if validate_schema:
            for domain, concept in ((source_domain, source_concept), (target_domain, target_concept)):
                if not self._domain_declares_concept(domain, concept):
                    return f"{domain} does not declare concept '{concept}'"
        return None

    def _domain_declares_concept(self, domain: str, concept: str) -> bool:
        from .schema_compiler.parser import SchemaParser

        authoritative = set(authoritative_schema_paths(self.target_dir))
        candidates = [
            (self.etg_dir / "packages" / domain / "schema.lds").resolve(),
            (self.target_dir / "packages" / domain / "schema.lds").resolve(),
        ]
        if domain in {".", "root", "workspace"}:
            candidates.append((self.target_dir / "schema.lds").resolve())
        for schema_path in candidates:
            if schema_path not in authoritative:
                continue
            entities, _ = SchemaParser(schema_path.read_text()).parse()
            if concept in entities:
                return True
            if "." in concept:
                entity_name, attribute_name = concept.split(".", 1)
                entity = entities.get(entity_name)
                if entity and any(attr["name"] == attribute_name for attr in entity.attributes):
                    return True
        return False

    def propose_alignment(self, source_domain: str, target_domain: str, source_concept: str, target_concept: str, confidence: float, rationale: str, source_artifact: str = None):
        """
        Records a non-operational alignment hypothesis. Proposed alignments are not used
        for closed-world routing until promoted through authorize_alignment().
        """
        return self.ledger.record_alignment_proposal(
            source_domain,
            target_domain,
            source_concept,
            target_concept,
            confidence,
            rationale,
            source_artifact=source_artifact,
        )

    def compile_ontology(self, schema_path: str):
        """Compiles a Schema file into a TTL ontology and saves it in the same directory."""
        from .schema_compiler import compile_schema_file
        try:
            ttl_content = compile_schema_file(schema_path, output_format="ttl")
            ttl_path = Path(schema_path).with_suffix(".ttl")
            ttl_path.write_text(ttl_content)
        except Exception as e:
            print(f"Warning: Failed to auto-sync ontology for {schema_path}: {e}")

    def sync_all_ontologies(self):
        """Synchronizes ontologies for the root domain and all active packages."""
        # 1. Root Domain
        for schema_name in ["schema.lds", "draft_schema.lds"]:
            root_schema = self.target_dir / schema_name
            if root_schema.exists():
                self.compile_ontology(str(root_schema))
        
        # 2. Active Packages
        packages = self.get_active_packages()
        for pkg in packages:
            # Check local packages folder
            pkg_schema = self.etg_dir / "packages" / pkg / "schema.lds"
            if pkg_schema.exists():
                self.compile_ontology(str(pkg_schema))
            else:
                # Fallback to root packages dir (legacy)
                pkg_schema = self.target_dir / "packages" / pkg / "schema.lds"
                if pkg_schema.exists():
                    self.compile_ontology(str(pkg_schema))

    def detect_cross_domain_conflict(self, 
                                     source_domain: str, 
                                     target_domain: str, 
                                     source_state: Dict[str, Any], 
                                     target_state: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Uses approved semantic alignments to sense contradictions between two domain states.
        """
        alignments = self.ledger.get_alignments(source_domain=source_domain, trusted_only=True)
        conflicts = []

        for alignment in alignments:
            if alignment['target_domain'] != target_domain:
                continue
            
            s_concept = alignment['source_concept']
            t_concept = alignment['target_concept']

            # Check if both states have the concept
            if s_concept in source_state and t_concept in target_state:
                s_val = source_state[s_concept]
                t_val = target_state[t_concept]

                if s_val != t_val:
                    conflict_id = f"CONFLICT-{source_domain}-{target_domain}-{s_concept}"
                    conflict = {
                        "id": conflict_id,
                        "entity_type": s_concept,
                        "proposed_states": {
                            source_domain: s_val,
                            target_domain: t_val
                        },
                        "rationale": f"Alignment mismatch: {s_concept} ({source_domain}) vs {t_concept} ({target_domain})"
                    }
                    conflicts.append(conflict)

        return conflicts

    def load_domain_state(self, domain_name: str) -> Dict[str, Any]:
        """
        Loads the current state of a domain from .etg/states/[domain].db
        For sensing purposes, we extract a flattened representation of key attributes.
        """
        db_file = self.etg_dir / "states" / f"{domain_name}.db"
        if not db_file.exists():
            return {}
        
        state = {}
        conn = None
        try:
            conn = sqlite3.connect(db_file)
            cursor = conn.cursor()

            # 1. Get all tables
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
            tables = [r[0] for r in cursor.fetchall()]

            for table in tables:
                # 2. Get table columns
                cursor.execute(f"PRAGMA table_info({table})")
                cols = [r[1] for r in cursor.fetchall()]

                # 3. Read the latest record (simplistic extraction for conflict sensing)
                cursor.execute(f"SELECT * FROM {table} ORDER BY rowid DESC LIMIT 1")
                row = cursor.fetchone()

                if row:
                    for i, col in enumerate(cols):
                        state[f"{table}.{col}"] = row[i]
                        if col not in state:
                            state[col] = row[i]

            return state
        except Exception as e:
            print(f"Error loading state from {db_file}: {e}")
            return {}
        finally:
            if conn:
                conn.close()

    def update_domain_state(self, domain_name: str, updates: Dict[str, Any]) -> int:
        """
        Updates the domain state with the provided values in the SQLite database.
        """
        db_file = self.etg_dir / "states" / f"{domain_name}.db"
        if not db_file.exists():
            print(f"Cannot update state: {db_file} does not exist.")
            return 0

        conn = None
        updated = 0
        try:
            conn = sqlite3.connect(db_file)
            cursor = conn.cursor()

            # Get all tables and their columns for whitelist validation
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
            tables = [r[0] for r in cursor.fetchall()]

            # Build column whitelist per table to prevent SQL injection via concept names
            table_cols: Dict[str, set] = {}
            for table in tables:
                cursor.execute(f"PRAGMA table_info({table})")
                table_cols[table] = {r[1] for r in cursor.fetchall()}

            with conn:
                for key, value in updates.items():
                    if "." not in key:
                        print(f"Warning: Skipping unqualified update key '{key}'.")
                        continue
                    table, col = key.split(".", 1)
                    if table not in table_cols or col not in table_cols[table]:
                        print(f"Warning: Skipping unsafe update key '{key}' — not a valid table.column")
                        continue
                    quoted_table = '"' + table.replace('"', '""') + '"'
                    quoted_column = '"' + col.replace('"', '""') + '"'
                    # Conflict sensing reads one current row per table. Update
                    # only that row; never broadcast a resolution table-wide.
                    cursor.execute(
                        f"UPDATE {quoted_table} SET {quoted_column} = ? "
                        f"WHERE rowid = (SELECT rowid FROM {quoted_table} ORDER BY rowid DESC LIMIT 1)",
                        (value,),
                    )
                    updated += cursor.rowcount
        except Exception as e:
            print(f"Error updating state in {db_file}: {e}")
        finally:
            if conn:
                conn.close()
        return updated

    def sync_resolutions(self):
        """
        Propagates human-approved resolutions back to the individual domain states.
        This closes the loop in the Federated Architecture.
        """
        resolutions = self.ledger.get_all_resolutions()
        alignments = self.ledger.get_alignments(trusted_only=True)
        
        for res in resolutions:
            resolved_val = res['state']

            # Restrict a resolution to the exact alignment that created its
            # deterministic conflict id. Ambiguous legacy decisions remain in
            # the ledger but are never broadcast as table-wide writes.
            matched = [
                aln for aln in alignments
                if res["conflict_id"] == f"CONFLICT-{aln['source_domain']}-{aln['target_domain']}-{aln['source_concept']}"
            ]
            if len(matched) != 1:
                print(f"Warning: Skipping unscoped resolution {res['conflict_id']}.")
                continue
            alignment = matched[0]
            for domain, concept in (
                (alignment["source_domain"], alignment["source_concept"]),
                (alignment["target_domain"], alignment["target_concept"]),
            ):
                qualified = self._qualified_state_key(domain, concept)
                if not qualified:
                    print(f"Warning: Skipping ambiguous state target {domain}.{concept}.")
                    continue
                print(f"🔄 Syncing {domain}: setting {qualified} = {resolved_val}")
                self.update_domain_state(domain, {qualified: resolved_val})

    def _qualified_state_key(self, domain_name: str, concept: str) -> Optional[str]:
        if "." in concept:
            return concept
        db_file = self.etg_dir / "states" / f"{domain_name}.db"
        if not db_file.exists():
            return None
        conn = sqlite3.connect(db_file)
        try:
            tables = [row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )]
            matches = [
                table for table in tables
                if concept in {
                    row[0]
                    for row in conn.execute(
                        "SELECT name FROM pragma_table_info(?)",
                        (table,),
                    )
                }
            ]
            return f"{matches[0]}.{concept}" if len(matches) == 1 else None
        finally:
            conn.close()

    def negotiate_alignments(self, source_schema_path: str, target_schema_path: str, threshold: float = 0.6) -> List[Dict[str, Any]]:
        """
        Automatically proposes alignments between two Schema files.
        """
        from .governance.negotiator import AlignmentNegotiator

        source_path = self._confined_workspace_input(source_schema_path, suffix=".lds")
        target_path = self._confined_workspace_input(target_schema_path, suffix=".lds")
        with open(source_path, 'r') as f:
            source_schema = f.read()
        with open(target_path, 'r') as f:
            target_schema = f.read()
            
        negotiator = AlignmentNegotiator(threshold=threshold, ledger_path=str(self.ledger_path))
        return negotiator.negotiate(source_schema, target_schema)

    def export_alignments(self, source_domain: Optional[str] = None) -> str:
        """
        Exports all approved alignments in EXMO Align API (RDF/XML) format.
        """
        from .governance.alignment import AlignmentProtocol
        
        # We'll group alignments by pair of domains
        all_alignments = self.ledger.get_alignments(source_domain=source_domain, trusted_only=True)
        if not all_alignments:
            return ""

        # For the purpose of EXMO Align API, we usually export one file per domain pair.
        # Here we'll just aggregate them into a single protocol object for demonstration,
        # or we could return multiple. Let's simplify and aggregate.
        protocol = AlignmentProtocol("Entigram-Source-Collective", "Entigram-Target-Collective")
        for aln in all_alignments:
            protocol.mappings.append({
                "source": f"{aln['source_domain']}:{aln['source_concept']}",
                "target": f"{aln['target_domain']}:{aln['target_concept']}",
                "relation": aln['relation'],
                "confidence": aln['confidence']
            })
            
        return protocol.export_alignment_api()

    def import_alignments(self, xml_file_path: str) -> int:
        """
        Imports alignment proposals from an EXMO Align API XML file.
        Imported mappings require explicit authorization before routing.
        """
        from .governance.alignment import AlignmentProtocol
        
        xml_path = self._confined_workspace_input(xml_file_path, suffix=".xml")
        with open(xml_path, 'r') as f:
            content = f.read()
            
        protocol = AlignmentProtocol("", "")
        protocol.import_alignment_api(content)
        
        count = 0
        for mapping in protocol.mappings:
            # Parse domain:concept from the resource strings
            # Expected format "Domain:Concept" or just "Concept"
            src = mapping['source']
            tgt = mapping['target']

            src_dom, src_con = src.split(":", 1) if ":" in src else ("External", src)
            tgt_dom, tgt_con = tgt.split(":", 1) if ":" in tgt else ("Internal", tgt)

            if self.propose_alignment(
                src_dom,
                tgt_dom,
                src_con,
                tgt_con,
                mapping['confidence'],
                f"Imported from {xml_file_path} ({mapping['relation']})",
                source_artifact=xml_file_path,
            ):
                count += 1
        return count

    def _confined_workspace_input(self, value: str, *, suffix: str) -> Path:
        """Resolve a user-selected input without permitting workspace escape."""
        if not isinstance(value, str):
            raise ValueError(
                "input path must stay within the workspace and use a safe relative path"
            )
        workspace_prefix = f"{self.target_dir}{os.sep}"
        if value.startswith(workspace_prefix):
            value = value[len(workspace_prefix):]
        if not re.fullmatch(
            r"[A-Za-z0-9_.@+-]+(?:/[A-Za-z0-9_.@+-]+)*",
            value,
        ):
            raise ValueError(
                "input path must stay within the workspace and use a safe relative path"
            )
        parts = value.split("/")
        if any(part in {".", ".."} for part in parts):
            raise ValueError(
                "input path must stay within the workspace and use a safe relative path"
            )
        candidate = self.target_dir.joinpath(*parts).resolve()
        if candidate != self.target_dir and self.target_dir not in candidate.parents:
            raise ValueError("input path must stay within the Entigram workspace")
        if candidate.suffix.lower() != suffix or not candidate.is_file():
            raise ValueError(f"input must be an existing {suffix} file")
        return candidate

    def sense_all(self) -> List[Dict[str, Any]]:
        """
        Orchestrates cross-domain sensing across all active packages.
        """
        packages = self.get_active_packages()
        all_conflicts = []

        # Load states for all packages
        states = {pkg: self.load_domain_state(pkg) for pkg in packages}

        # Compare each unordered pair once (N*(N-1)/2 instead of N*(N-1))
        for src_pkg, tgt_pkg in combinations(packages, 2):
            conflicts = self.detect_cross_domain_conflict(
                src_pkg, tgt_pkg, states[src_pkg], states[tgt_pkg]
            )
            for conflict in conflicts:
                all_conflicts.append(conflict)
                self.report_conflict(conflict['id'], conflict['entity_type'], conflict['proposed_states'], "EntigramBroker-Sensor")

        return all_conflicts
    def analyze_impact(self, changed_file: str) -> dict:
        """Analyzes the impact of a changed file on expectations, entities, and relationships."""
        from .governance.commissioner import Commissioner
        impact = {
            "expectations": [],
            "entities": [],
            "relationships": []
        }

        changed_refs = self._impact_file_refs(changed_file)
        changed_terms = self._impact_file_terms(changed_file)
        schema_path = self.target_dir / "schema.lds"
        if schema_path.exists():
            with open(schema_path, "r", encoding="utf-8") as f:
                schema_content = f.read()
            commissioner = Commissioner(schema_content)

            for exp in commissioner.expectations:
                val_check = getattr(exp, 'validation_check', '') or ''
                dev_exp = getattr(exp, 'developer_expectation', '') or ''
                impl_rule = getattr(exp, 'implementation_rule', '') or ''
                name = getattr(exp, 'name', 'Unknown')
                searchable = " ".join([val_check, dev_exp, impl_rule])
                searchable_lower = searchable.lower()
                term_match = (
                    len(changed_terms) >= 2
                    and all(term in searchable_lower for term in changed_terms)
                )
                if any(ref and ref in searchable for ref in changed_refs) or term_match:
                    impact['expectations'].append(name)

        if changed_file.endswith(".lds") or changed_file.endswith(".ttl"):
            impact['entities'].append("All Entities (Schema change)")
            impact['relationships'].append("All Relationships (Schema change)")

        return impact

    def _impact_file_refs(self, changed_file: str) -> set:
        """Return common path/module spellings for matching modeled validation checks."""
        raw = (changed_file or "").strip()
        refs = {raw}
        without_selector = raw.split("::", 1)[0]
        refs.add(without_selector)

        normalized = without_selector.replace("\\", "/").lstrip("./")
        refs.add(normalized)
        if normalized.endswith(".py"):
            module = normalized[:-3].replace("/", ".")
            refs.add(module)
            refs.add(module.replace(".", "/") + ".py")
        else:
            refs.add(normalized.replace("/", "."))
            refs.add(normalized.replace(".", "/") + ".py")

        try:
            candidate = Path(normalized)
            if not candidate.is_absolute():
                candidate = self.target_dir / candidate
            rel = candidate.resolve().relative_to(self.target_dir)
            refs.add(rel.as_posix())
            if rel.suffix == ".py":
                refs.add(rel.with_suffix("").as_posix().replace("/", "."))
        except (OSError, ValueError):
            pass

        return {ref for ref in refs if ref}

    def _impact_file_terms(self, changed_file: str) -> set:
        normalized = (changed_file or "").split("::", 1)[0].replace("\\", "/").lstrip("./")
        stem = Path(normalized).with_suffix("").as_posix()
        terms = set()
        for part in stem.split("/"):
            for token in part.replace("-", "_").split("_"):
                token = token.lower()
                if len(token) > 2 and token not in {"entigram", "tests", "test", "src", "lib"}:
                    terms.add(token)
        return terms
