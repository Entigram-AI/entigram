"""Versioned, evidence-backed admission for consequential agent actions.

This module deliberately validates and records an action without executing its
side effect.  An executor can claim prevention only when it owns the scoped
credential and mediates the target operation; that boundary is represented by
the contract's ``assurance`` value and enforced by a future action runner.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

import yaml
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.exceptions import InvalidSignature

from .warden import Warden
from .trust import ProjectTrustRegistry


ACTION_CONTRACT_FILE = "actions.yaml"
ACTION_CONTRACT_VERSION = "entigram.action-contract.v1"
LOCAL_SIGNATURE_TYPE = "entigram.local.ed25519.v1"
ASSURANCE_LEVELS = {"advisory", "mediated", "enforced"}
POLICY_DECISIONS = {"allow", "deny", "approval_required", "conflicted"}
_IDENTIFIER = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]*$")
_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:+-]*$")
_SHA256 = re.compile(r"^[a-fA-F0-9]{64}$")
MAX_EVIDENCE_CLOCK_SKEW_SECONDS = 30


class ActionContractError(ValueError):
    """Raised when an action-contract document is malformed."""


def canonical_json(value: Any) -> bytes:
    """Canonical bytes used for all action, evidence, and signature digests."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def isoformat(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_timestamp(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be an RFC 3339 timestamp")
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(f"{field} must be an RFC 3339 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _require_identifier(value: Any, field: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise ActionContractError(
            f"{field} must use letters, digits, '.', '_', ':', or '-' and begin with a letter"
        )
    return value


def _require_version(value: Any, field: str) -> str:
    if not isinstance(value, str) or not _VERSION.fullmatch(value):
        raise ActionContractError(
            f"{field} must use letters, digits, '.', '_', ':', '+', or '-' and begin with a letter or digit"
        )
    return value


def _require_string_list(value: Any, field: str, *, allow_empty: bool = True) -> List[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
        raise ActionContractError(f"{field} must be a list of non-empty strings")
    if not allow_empty and not value:
        raise ActionContractError(f"{field} must not be empty")
    return [item.strip() for item in value]


def _path_lookup(context: Dict[str, Any], path: str) -> Tuple[bool, Any]:
    """Return whether a dotted path is present without treating absence as false."""
    current: Any = context
    for segment in path.split("."):
        if not isinstance(current, dict) or segment not in current:
            return False, None
        current = current[segment]
    return True, current


def authority_grant_claims(
    *,
    principal: str,
    agent_id: str,
    scopes: Iterable[str],
    expires_at: str,
    grant_id: Optional[str] = None,
    audience: Optional[str] = None,
    issuer_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Build portable grant claims for either a local or personal signer."""
    claims: Dict[str, Any] = {
        "grant_id": _require_identifier(grant_id, "grant_id") if grant_id else f"grant-{uuid.uuid4()}",
        "principal": _require_identifier(principal, "principal"),
        "agent_id": _require_identifier(agent_id, "agent_id"),
        "scopes": _require_string_list(list(scopes), "scopes", allow_empty=False),
        "issued_at": isoformat(utc_now()),
        "expires_at": isoformat(parse_timestamp(expires_at, "expires_at")),
        "revoked": False,
    }
    if audience:
        claims["audience"] = _require_identifier(audience, "audience")
    if issuer_id:
        claims["issuer_id"] = _require_identifier(issuer_id, "issuer_id")
    return claims


def action_approval_claims(
    *,
    action_name: str,
    request_id: str,
    approver_id: str,
    role: str,
    action_digest: str,
    evidence_digest: str,
    expires_at: str,
) -> Dict[str, Any]:
    return {
        "approval_id": f"approval-{uuid.uuid4()}",
        "action_name": _require_identifier(action_name, "action_name"),
        "request_id": _require_identifier(request_id, "request_id"),
        "approver_id": _require_identifier(approver_id, "approver_id"),
        "role": _require_identifier(role, "role"),
        "action_digest": action_digest,
        "evidence_digest": evidence_digest,
        "issued_at": isoformat(utc_now()),
        "expires_at": isoformat(parse_timestamp(expires_at, "expires_at")),
    }


def evidence_attestation_claims(
    *,
    evidence_id: str,
    sha256: str,
    observed_at: str,
    source: Dict[str, Any],
) -> Dict[str, Any]:
    """Build claims a trusted evidence issuer signs for one evidence record."""
    if not _SHA256.fullmatch(str(sha256)):
        raise ActionContractError("evidence sha256 must be a SHA-256 hex digest")
    if not isinstance(source, dict) or not isinstance(source.get("kind"), str) or not source["kind"].strip():
        raise ActionContractError("evidence source must be an object with a non-empty kind")
    return {
        "evidence_id": _require_identifier(evidence_id, "evidence_id"),
        "sha256": str(sha256).lower(),
        "observed_at": isoformat(parse_timestamp(observed_at, "observed_at")),
        "source": source,
        "attested_at": isoformat(utc_now()),
    }


def agent_attestation_claims(
    *,
    action_name: str,
    request_id: str,
    request_digest: str,
    agent_id: str,
    runtime: str,
    version: str,
    expires_at: str,
    nonce: Optional[str] = None,
) -> Dict[str, Any]:
    """Build a short-lived assertion that binds an enrolled agent to a request.

    Human authority grants delegate scope. This separate workload assertion
    proves that the enrolled host key, not just arbitrary request JSON, named
    the particular agent/runtime/version for this exact action payload.
    """
    return {
        "attestation_id": f"agent-attestation-{uuid.uuid4()}",
        "action_name": _require_identifier(action_name, "action_name"),
        "request_id": _require_identifier(request_id, "request_id"),
        "request_digest": str(request_digest),
        "agent_id": _require_identifier(agent_id, "agent_id"),
        "runtime": _require_identifier(runtime, "runtime"),
        "version": _require_version(version, "version"),
        "issued_at": isoformat(utc_now()),
        "expires_at": isoformat(parse_timestamp(expires_at, "expires_at")),
        "nonce": _require_identifier(nonce, "nonce") if nonce else f"agent-nonce-{uuid.uuid4()}",
    }


class LocalActionAuthority:
    """A deliberately local Ed25519 authority adapter for development use.

    It is only as strong as access to ``.etg/action_authority_ed25519_private.pem``.
    Production integrations should replace it with an IAM-backed authority
    adapter and retain the normalized claims and decision record.
    """

    def __init__(self, target_dir: str | Path):
        self.target_dir = Path(target_dir).expanduser().resolve()
        self.etg_dir = self.target_dir / ".etg"
        self.private_key_path = self.etg_dir / "action_authority_ed25519_private.pem"
        self.public_key_path = self.etg_dir / "action_authority_ed25519_public.pem"

    def initialized(self) -> bool:
        return self.private_key_path.is_file() and self.public_key_path.is_file()

    def initialize(self) -> Dict[str, str]:
        if self.private_key_path.exists() or self.public_key_path.exists():
            raise ValueError("local action authority already exists; refuse to overwrite key material")
        self.etg_dir.mkdir(parents=True, exist_ok=True)
        private_key = Ed25519PrivateKey.generate()
        private_bytes = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        public_bytes = private_key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        self.private_key_path.write_bytes(private_bytes)
        self.public_key_path.write_bytes(public_bytes)
        try:
            self.private_key_path.chmod(0o600)
            self.public_key_path.chmod(0o644)
        except OSError:
            pass
        return {
            "key_id": self.key_id(),
            "private_key_path": str(self.private_key_path),
            "public_key_path": str(self.public_key_path),
        }

    def key_id(self) -> str:
        return hashlib.sha256(self._public_key_bytes()).hexdigest()

    def _public_key_bytes(self) -> bytes:
        if not self.public_key_path.is_file():
            raise ValueError("local action authority is not initialized")
        return self.public_key_path.read_bytes()

    def _private_key(self) -> Ed25519PrivateKey:
        if not self.private_key_path.is_file():
            raise ValueError("local action authority is not initialized")
        loaded = serialization.load_pem_private_key(self.private_key_path.read_bytes(), password=None)
        if not isinstance(loaded, Ed25519PrivateKey):
            raise ValueError("local action authority private key is not Ed25519")
        return loaded

    def _public_key(self) -> Ed25519PublicKey:
        loaded = serialization.load_pem_public_key(self._public_key_bytes())
        if not isinstance(loaded, Ed25519PublicKey):
            raise ValueError("local action authority public key is not Ed25519")
        return loaded

    def sign(self, kind: str, claims: Dict[str, Any]) -> Dict[str, Any]:
        payload = {"type": LOCAL_SIGNATURE_TYPE, "kind": kind, "claims": claims}
        signature = self._private_key().sign(canonical_json(payload))
        return {
            "type": LOCAL_SIGNATURE_TYPE,
            "kind": kind,
            "key_id": self.key_id(),
            "claims": claims,
            "signature": base64.b64encode(signature).decode("ascii"),
        }

    def verify(self, artifact: Any, kind: str) -> Tuple[bool, Optional[Dict[str, Any]], str]:
        if not isinstance(artifact, dict):
            return False, None, "missing signed assertion"
        if artifact.get("type") != LOCAL_SIGNATURE_TYPE or artifact.get("kind") != kind:
            return False, None, "assertion type or kind is not supported"
        try:
            if artifact.get("key_id") != self.key_id():
                return False, None, "assertion was not issued by this workspace authority"
            claims = artifact.get("claims")
            if not isinstance(claims, dict):
                return False, None, "assertion claims must be an object"
            signature = base64.b64decode(artifact.get("signature", ""), validate=True)
            payload = {"type": LOCAL_SIGNATURE_TYPE, "kind": kind, "claims": claims}
            self._public_key().verify(signature, canonical_json(payload))
            return True, claims, "verified"
        except (ValueError, TypeError, InvalidSignature) as exc:
            return False, None, f"invalid assertion signature: {exc}"

    def issue_grant(
        self,
        *,
        principal: str,
        agent_id: str,
        scopes: Iterable[str],
        expires_at: str,
        grant_id: Optional[str] = None,
        audience: Optional[str] = None,
    ) -> Dict[str, Any]:
        return self.sign(
            "authority_grant",
            authority_grant_claims(
                principal=principal,
                agent_id=agent_id,
                scopes=scopes,
                expires_at=expires_at,
                grant_id=grant_id,
                audience=audience,
            ),
        )

    def issue_approval(
        self,
        *,
        action_name: str,
        request_id: str,
        approver_id: str,
        role: str,
        action_digest: str,
        evidence_digest: str,
        expires_at: str,
    ) -> Dict[str, Any]:
        return self.sign(
            "action_approval",
            action_approval_claims(
                action_name=action_name,
                request_id=request_id,
                approver_id=approver_id,
                role=role,
                action_digest=action_digest,
                evidence_digest=evidence_digest,
                expires_at=expires_at,
            ),
        )


class ActionAdmissionEngine:
    """Loads ``actions.yaml`` and renders a deterministic admission decision."""

    def __init__(
        self,
        target_dir: str | Path,
        *,
        now: Optional[datetime] = None,
        revocation_checker: Optional[Callable[[str], bool]] = None,
        attestation_consumed_checker: Optional[Callable[[str, str, str], bool]] = None,
    ):
        self.target_dir = Path(target_dir).expanduser().resolve()
        self.actions_path = self.target_dir / ACTION_CONTRACT_FILE
        self.warden = Warden(str(self.target_dir))
        self.authority = LocalActionAuthority(self.target_dir)
        self.project_trust = ProjectTrustRegistry(self.target_dir)
        self.now = (now or utc_now()).astimezone(timezone.utc)
        self.revocation_checker = revocation_checker
        self.attestation_consumed_checker = attestation_consumed_checker

    def _verify_assertion(self, artifact: Any, kind: str) -> Tuple[bool, Optional[Dict[str, Any]], str]:
        """Prefer project identities when a shared trust registry is configured.

        Fails closed if the workspace manifest declares project trust but the
        registry file is missing (prevents downgrade attacks via registry deletion).
        """
        if self.project_trust.exists():
            return self.project_trust.verify(artifact, kind)
        # Fail closed if manifest expects project trust but registry is absent
        manifest_path = self.target_dir / ".etg" / "entigram.yaml"
        if manifest_path.is_file():
            try:
                import yaml
                manifest = yaml.safe_load(manifest_path.read_text()) or {}
                if manifest.get("trust_mode") == "project":
                    return False, None, "trust registry missing but trust_mode=project declared in manifest"
            except Exception:
                pass
        return self.authority.verify(artifact, kind)

    def load_contracts(self) -> Dict[str, Dict[str, Any]]:
        if not self.actions_path.is_file():
            raise ActionContractError(f"missing {ACTION_CONTRACT_FILE} in workspace root")
        try:
            document = yaml.safe_load(self.actions_path.read_text()) or {}
        except yaml.YAMLError as exc:
            raise ActionContractError(f"could not parse {ACTION_CONTRACT_FILE}: {exc}") from exc
        if not isinstance(document, dict):
            raise ActionContractError(f"{ACTION_CONTRACT_FILE} must be a YAML object")
        if document.get("format") not in {None, ACTION_CONTRACT_VERSION}:
            raise ActionContractError(
                f"format must be {ACTION_CONTRACT_VERSION!r} when declared"
            )
        actions = document.get("actions")
        if not isinstance(actions, dict) or not actions:
            raise ActionContractError("actions must be a non-empty object")
        contracts: Dict[str, Dict[str, Any]] = {}
        for name, contract in actions.items():
            _require_identifier(name, "action name")
            contracts[name] = self._validate_contract(name, contract)
        return contracts

    def get_contract(self, action_name: str) -> Dict[str, Any]:
        contracts = self.load_contracts()
        if action_name not in contracts:
            raise ActionContractError(f"unknown action contract: {action_name}")
        return contracts[action_name]

    def _validate_contract(self, name: str, contract: Any) -> Dict[str, Any]:
        if not isinstance(contract, dict):
            raise ActionContractError(f"actions.{name} must be an object")
        required = {"version", "assurance", "reads", "writes", "authority", "policy", "postcondition", "compensation"}
        missing = sorted(required - set(contract))
        if missing:
            raise ActionContractError(f"actions.{name} is missing required fields: {', '.join(missing)}")
        version = contract.get("version")
        if not isinstance(version, (str, int)) or not str(version).strip():
            raise ActionContractError(f"actions.{name}.version must be a non-empty string or integer")
        assurance = contract.get("assurance")
        if assurance not in ASSURANCE_LEVELS:
            raise ActionContractError(
                f"actions.{name}.assurance must be one of {', '.join(sorted(ASSURANCE_LEVELS))}"
            )
        contract["reads"] = _require_string_list(contract.get("reads"), f"actions.{name}.reads")
        contract["writes"] = _require_string_list(contract.get("writes"), f"actions.{name}.writes")
        authority = contract.get("authority")
        if not isinstance(authority, dict):
            raise ActionContractError(f"actions.{name}.authority must be an object")
        authority["scopes"] = _require_string_list(authority.get("scopes"), f"actions.{name}.authority.scopes", allow_empty=False)
        for field in ("principals", "agents"):
            if field in authority:
                authority[field] = _require_string_list(authority[field], f"actions.{name}.authority.{field}")
        if "audience" in authority:
            _require_identifier(authority["audience"], f"actions.{name}.authority.audience")

        preconditions = contract.get("preconditions", [])
        if not isinstance(preconditions, list):
            raise ActionContractError(f"actions.{name}.preconditions must be a list")
        for index, predicate in enumerate(preconditions):
            self._validate_predicate(predicate, f"actions.{name}.preconditions[{index}]")

        evidence = contract.get("evidence", [])
        if not isinstance(evidence, list):
            raise ActionContractError(f"actions.{name}.evidence must be a list")
        for index, requirement in enumerate(evidence):
            if not isinstance(requirement, dict):
                raise ActionContractError(f"actions.{name}.evidence[{index}] must be an object")
            _require_identifier(requirement.get("id"), f"actions.{name}.evidence[{index}].id")
            if "freshness_seconds" in requirement and (
                not isinstance(requirement["freshness_seconds"], int)
                or isinstance(requirement["freshness_seconds"], bool)
                or requirement["freshness_seconds"] < 0
            ):
                raise ActionContractError(
                    f"actions.{name}.evidence[{index}].freshness_seconds must be a non-negative integer"
                )
            if "verified" in requirement and not isinstance(requirement["verified"], bool):
                raise ActionContractError(f"actions.{name}.evidence[{index}].verified must be boolean")
            if "issuer_roles" in requirement:
                _require_string_list(
                    requirement["issuer_roles"],
                    f"actions.{name}.evidence[{index}].issuer_roles",
                    allow_empty=False,
                )
            if "source_kinds" in requirement:
                _require_string_list(
                    requirement["source_kinds"],
                    f"actions.{name}.evidence[{index}].source_kinds",
                    allow_empty=False,
                )

        policy = contract.get("policy")
        if not isinstance(policy, dict) or policy.get("decision") not in POLICY_DECISIONS:
            raise ActionContractError(
                f"actions.{name}.policy.decision must be one of {', '.join(sorted(POLICY_DECISIONS))}"
            )
        if "id" in policy:
            _require_identifier(policy["id"], f"actions.{name}.policy.id")

        approval = contract.get("approval", {"required": False})
        if not isinstance(approval, dict) or not isinstance(approval.get("required", False), bool):
            raise ActionContractError(f"actions.{name}.approval must be an object with boolean required")
        if approval.get("required"):
            _require_string_list(approval.get("roles"), f"actions.{name}.approval.roles", allow_empty=False)
        if policy["decision"] == "approval_required":
            if approval.get("required") is not True:
                raise ActionContractError(
                    f"actions.{name}.approval.required must be true when policy requires approval"
                )
            _require_string_list(approval.get("roles"), f"actions.{name}.approval.roles", allow_empty=False)

        postcondition = contract.get("postcondition")
        self._validate_predicate(postcondition, f"actions.{name}.postcondition")
        if "observation_deadline_seconds" in postcondition and (
            not isinstance(postcondition["observation_deadline_seconds"], int)
            or isinstance(postcondition["observation_deadline_seconds"], bool)
            or postcondition["observation_deadline_seconds"] < 0
        ):
            raise ActionContractError(
                f"actions.{name}.postcondition.observation_deadline_seconds must be a non-negative integer"
            )

        compensation = contract.get("compensation")
        if not isinstance(compensation, dict):
            raise ActionContractError(f"actions.{name}.compensation must be an object")
        has_compensator = isinstance(compensation.get("action"), str) and bool(compensation["action"].strip())
        not_reversible = compensation.get("not_reversible") is True
        if has_compensator == not_reversible:
            raise ActionContractError(
                f"actions.{name}.compensation must declare exactly one of action or not_reversible: true"
            )
        if has_compensator:
            _require_identifier(compensation["action"], f"actions.{name}.compensation.action")
        return contract

    @staticmethod
    def _validate_predicate(predicate: Any, field: str) -> None:
        if not isinstance(predicate, dict):
            raise ActionContractError(f"{field} must be an object")
        if not isinstance(predicate.get("path"), str) or not predicate["path"].strip():
            raise ActionContractError(f"{field}.path must be a non-empty dotted path")
        if "equals" not in predicate and "one_of" not in predicate and predicate.get("exists") is not True:
            raise ActionContractError(f"{field} must declare equals, one_of, or exists: true")
        if "one_of" in predicate and not isinstance(predicate["one_of"], list):
            raise ActionContractError(f"{field}.one_of must be a list")

    def action_digests(self, action_name: str, request: Dict[str, Any]) -> Dict[str, str]:
        contract = self.get_contract(action_name)
        self._validate_request(request)
        evidence = [
            {key: value for key, value in item.items() if key != "attestation"}
            for item in (request.get("evidence") or [])
        ]
        evidence_digest = digest(sorted(evidence, key=lambda item: str(item.get("id", ""))))
        request_payload = {
            key: (
                evidence
                if key == "evidence"
                else value
            )
            for key, value in request.items()
            if key not in {"authority", "approvals", "agent_attestation"}
        }
        contract_digest = digest({"name": action_name, "contract": contract})
        request_digest = digest(
            {
                "action_name": action_name,
                "contract_digest": contract_digest,
                "request": request_payload,
            }
        )
        return {
            "contract_digest": contract_digest,
            "evidence_digest": evidence_digest,
            "request_digest": request_digest,
        }

    def validate(self, action_name: str, request: Dict[str, Any]) -> Dict[str, Any]:
        observed_at = isoformat(self.now)
        decision_id = f"action-decision-{uuid.uuid4()}"
        base: Dict[str, Any] = {
            "ok": False,
            "decision_id": decision_id,
            "action_name": action_name,
            "request_id": (
                request.get("request_id")
                if isinstance(request, dict) and isinstance(request.get("request_id"), str) and request.get("request_id")
                else f"invalid-{decision_id}"
            ),
            "status": "preflight_denied",
            "observed_at": observed_at,
            "reasons": [],
            "remediation": [],
            "preconditions": [],
            "evidence": [],
            "approval": {"required": False, "satisfied": True, "checks": []},
            "agent_identity": {"required": False, "verified": False},
        }
        try:
            contract = self.get_contract(action_name)
            self._validate_request(request)
            digests = self.action_digests(action_name, request)
        except (ActionContractError, ValueError) as exc:
            base["reasons"].append({"code": "contract_or_request_invalid", "message": str(exc)})
            base["remediation"].append("Correct the action contract or request and validate again.")
            return base

        base.update(digests)
        base["request_id"] = request["request_id"]
        base["request"] = {
            "principal": request["principal"],
            "agent_id": request["agent_id"],
            "target": request["target"],
        }
        # These are public signed assertions and evidence references, never
        # private-key material. Persisting them makes a later admission result
        # independently reviewable instead of merely reporting a boolean.
        base["provenance"] = {
            "authority_assertion": request.get("authority"),
            "approval_assertions": request.get("approvals", []),
            "agent_attestation": request.get("agent_attestation"),
            "evidence": request.get("evidence", []),
        }
        base["assurance"] = contract["assurance"]
        base["contract_version"] = str(contract["version"])
        base["model"] = {
            "warden_fingerprint": self.warden.generate_fingerprint(),
            "actions_path": ACTION_CONTRACT_FILE,
            "actions_sha256": hashlib.sha256(self.actions_path.read_bytes()).hexdigest(),
        }

        if not self.warden.verify_integrity(emit_human=False):
            base["reasons"].append(
                {
                    "code": "model_integrity_failed",
                    "message": "Warden integrity verification failed before action admission.",
                    "details": self.warden.last_halt_event.to_dict() if self.warden.last_halt_event else {},
                }
            )
            base["remediation"].append("Restore or explicitly review and lock the governed schema before retrying.")
            return base

        authority_ok, authority_result = self._validate_authority(contract, request)
        base["authority"] = authority_result
        if not authority_ok:
            base["reasons"].append(authority_result)
            base["remediation"].append("Obtain a current scoped authority grant from the configured authority adapter.")

        agent_ok, agent_result = self._validate_agent_identity(action_name, request, digests)
        base["agent_identity"] = agent_result
        if not agent_ok:
            base["reasons"].append(agent_result)
            base["remediation"].append(
                "Use a currently enrolled agent key and an allowed runtime version to attest this exact action request."
            )

        predicate_ok = self._validate_preconditions(contract, request, base)
        evidence_ok = self._validate_evidence(contract, request, base)
        policy_decision = contract["policy"]["decision"]
        base["policy"] = {
            "decision": policy_decision,
            "policy_id": contract["policy"].get("id"),
            "policy_version": str(contract["version"]),
            "considered": [contract["policy"]],
        }

        approval_ok = self._validate_approvals(contract, request, base, digests)
        if policy_decision == "conflicted":
            base["status"] = "conflicted"
            base["reasons"].append(
                {"code": "policy_conflicted", "message": "The action contract declares a conflicted policy result."}
            )
            base["remediation"].append("Resolve the policy conflict and re-admit the action.")
            return base
        if policy_decision == "deny":
            base["reasons"].append(
                {"code": "policy_denied", "message": "The action contract policy denies this action."}
            )
            base["remediation"].append("Change the request only if a separate policy permits it; do not bypass the mediator.")
            return base
        if not authority_ok or not agent_ok or not predicate_ok or not evidence_ok:
            return base
        if not approval_ok:
            base["status"] = "approval_required"
            if policy_decision == "approval_required":
                base["reasons"].append(
                    {"code": "policy_requires_approval", "message": "Policy requires a bound human approval."}
                )
            base["remediation"].append("Obtain a current signed approval bound to this action and evidence digest.")
            return base

        base["ok"] = True
        base["status"] = "admitted"
        base["remediation"].append(
            "This action is admitted for validation only. Execute it only through an adapter that matches its assurance level."
        )
        return base

    def _validate_agent_identity(
        self,
        action_name: str,
        request: Dict[str, Any],
        digests: Dict[str, str],
    ) -> Tuple[bool, Dict[str, Any]]:
        """Require an enrolled workload signature only in shared-trust mode."""
        if not self.project_trust.exists():
            return True, {
                "required": False,
                "verified": False,
                "mode": "local_development",
                "code": "agent_attribution_unverified",
                "message": (
                    "No project trust registry is configured; agent_id is attribution only and does not prevent impersonation."
                ),
            }
        verified, claims, code = self.project_trust.verify_agent_attestation(
            request.get("agent_attestation"),
            action_name=action_name,
            request_id=request["request_id"],
            request_digest=digests["request_digest"],
            now=self.now,
        )
        result: Dict[str, Any] = {
            "required": True,
            "verified": verified,
            "mode": "project_trust",
            "code": code,
            "message": code.replace("_", " "),
        }
        if not verified or claims is None:
            return False, result
        if claims.get("_agent_id") != request["agent_id"]:
            result.update(
                {
                    "verified": False,
                    "code": "agent_attestation_agent_mismatch",
                    "message": "The signed agent identity does not match the request agent_id.",
                }
            )
            return False, result
        if self.attestation_consumed_checker and self.attestation_consumed_checker(
            claims["_agent_id"], claims["attestation_id"], claims["nonce"]
        ):
            result.update(
                {
                    "verified": False,
                    "code": "agent_attestation_replayed",
                    "message": "This agent attestation or nonce has already been consumed.",
                }
            )
            return False, result
        result.update(
            {
                "agent_id": claims["_agent_id"],
                "owner_id": claims["_owner_id"],
                "runtime": claims["_runtime"],
                "version": claims["version"],
                "key_id": claims["_key_id"],
                "attestation_id": claims["attestation_id"],
            }
        )
        return True, result

    @staticmethod
    def _validate_request(request: Any) -> None:
        if not isinstance(request, dict):
            raise ValueError("action request must be a JSON object")
        _require_identifier(request.get("request_id"), "request_id")
        _require_identifier(request.get("principal"), "principal")
        _require_identifier(request.get("agent_id"), "agent_id")
        if "target" not in request:
            raise ValueError("action request must declare target")
        if not isinstance(request.get("context", {}), dict):
            raise ValueError("action request context must be an object")
        evidence = request.get("evidence", [])
        if not isinstance(evidence, list):
            raise ValueError("action request evidence must be a list")
        for index, item in enumerate(evidence):
            if not isinstance(item, dict):
                raise ValueError(f"evidence[{index}] must be an object")
            _require_identifier(item.get("id"), f"evidence[{index}].id")
            if not _SHA256.fullmatch(str(item.get("sha256", ""))):
                raise ValueError(f"evidence[{index}].sha256 must be a SHA-256 hex digest")
            parse_timestamp(item.get("observed_at"), f"evidence[{index}].observed_at")
            if "verified" in item and not isinstance(item["verified"], bool):
                raise ValueError(f"evidence[{index}].verified must be boolean")

    def _validate_authority(self, contract: Dict[str, Any], request: Dict[str, Any]) -> Tuple[bool, Dict[str, Any]]:
        verified, claims, message = self._verify_assertion(request.get("authority"), "authority_grant")
        result: Dict[str, Any] = {"verified": verified, "message": message}
        if not verified or claims is None:
            result["code"] = "authority_unverified"
            return False, result
        try:
            grant_id = _require_identifier(claims.get("grant_id"), "grant.grant_id")
        except ActionContractError as exc:
            result.update({"code": "authority_grant_id_invalid", "message": str(exc)})
            return False, result
        result["grant_id"] = grant_id
        try:
            if self.project_trust.exists():
                signer_id = claims.get("_signer_id")
                result.update({"signer_id": signer_id, "key_id": claims.get("_key_id"), "mode": "project_trust"})
                if claims.get("issuer_id") != signer_id:
                    result.update({"code": "authority_issuer_mismatch", "message": "Grant issuer does not match its signing identity."})
                    return False, result
                if not self.project_trust.signer_has_role(str(signer_id), "authority_issuer"):
                    result.update({"code": "authority_issuer_unqualified", "message": "Grant signer lacks the authority_issuer role."})
                    return False, result
            else:
                result["mode"] = "local_development"
            if claims.get("principal") != request["principal"] or claims.get("agent_id") != request["agent_id"]:
                result.update({"code": "authority_subject_mismatch", "message": "Grant principal or agent does not match request."})
                return False, result
            scopes = set(_require_string_list(claims.get("scopes"), "grant.scopes", allow_empty=False))
            required_scopes = set(contract["authority"]["scopes"])
            if not required_scopes.issubset(scopes):
                result.update({"code": "authority_scope_missing", "message": "Grant does not contain every required action scope.", "required_scopes": sorted(required_scopes)})
                return False, result
            if claims.get("revoked") is True:
                result.update({"code": "authority_revoked", "message": "Grant is marked revoked."})
                return False, result
            if self.project_trust.exists():
                revocation = self.project_trust.get_grant_revocation(grant_id)
                if revocation is not None:
                    result.update({"code": "authority_revoked", "message": "Grant was revoked in project trust history."})
                    return False, result
            if (not self.project_trust.exists()) and self.revocation_checker and self.revocation_checker(
                grant_id
            ):
                result.update({"code": "authority_revoked", "message": "Grant was revoked in the local action ledger."})
                return False, result
            if parse_timestamp(claims.get("expires_at"), "grant.expires_at") <= self.now:
                result.update({"code": "authority_expired", "message": "Grant has expired."})
                return False, result
            for field, request_key in (("principals", "principal"), ("agents", "agent_id")):
                allowed = contract["authority"].get(field)
                if allowed and request[request_key] not in allowed:
                    result.update({"code": "authority_not_allowed", "message": f"{request_key} is outside the contract authority allowlist."})
                    return False, result
            expected_audience = contract["authority"].get("audience")
            if expected_audience and claims.get("audience") != expected_audience:
                result.update({"code": "authority_audience_mismatch", "message": "Grant audience does not match the action contract."})
                return False, result
        except (ActionContractError, ValueError) as exc:
            result.update({"code": "authority_invalid", "message": str(exc)})
            return False, result
        result["code"] = "authority_valid"
        return True, result

    def _validate_preconditions(self, contract: Dict[str, Any], request: Dict[str, Any], decision: Dict[str, Any]) -> bool:
        all_passed = True
        context = request.get("context", {})
        for predicate in contract.get("preconditions", []):
            present, actual = _path_lookup(context, predicate["path"])
            check = {"path": predicate["path"], "actual": actual if present else None}
            if not present:
                check.update({"result": "unknown", "code": "precondition_unknown"})
                all_passed = False
            elif "equals" in predicate and actual != predicate["equals"]:
                check.update({"result": "failed", "code": "precondition_failed", "expected": predicate["equals"]})
                all_passed = False
            elif "one_of" in predicate and actual not in predicate["one_of"]:
                check.update({"result": "failed", "code": "precondition_failed", "expected_one_of": predicate["one_of"]})
                all_passed = False
            else:
                check.update({"result": "satisfied", "code": "precondition_satisfied"})
            decision["preconditions"].append(check)
            if check["result"] != "satisfied":
                decision["reasons"].append(
                    {"code": check["code"], "message": f"Precondition {predicate['path']} is {check['result']}."}
                )
                decision["remediation"].append(f"Refresh or correct context for {predicate['path']}.")
        return all_passed

    def _validate_evidence(self, contract: Dict[str, Any], request: Dict[str, Any], decision: Dict[str, Any]) -> bool:
        supplied: Dict[str, Dict[str, Any]] = {}
        for item in request.get("evidence", []):
            if item["id"] in supplied:
                duplicate = {"id": item["id"], "result": "failed", "code": "evidence_duplicate"}
                decision["evidence"].append(duplicate)
                decision["reasons"].append(
                    {
                        "code": "evidence_duplicate",
                        "message": f"Evidence {item['id']} was supplied more than once.",
                    }
                )
                decision["remediation"].append(
                    f"Supply exactly one current evidence record for {item['id']}."
                )
            supplied[item["id"]] = item
        all_passed = not any(check.get("code") == "evidence_duplicate" for check in decision["evidence"])
        for requirement in contract.get("evidence", []):
            evidence_id = requirement["id"]
            item = supplied.get(evidence_id)
            check: Dict[str, Any] = {"id": evidence_id}
            if item is None:
                check.update({"result": "unknown", "code": "evidence_missing"})
                all_passed = False
            else:
                observed_at = parse_timestamp(item["observed_at"], f"evidence.{evidence_id}.observed_at")
                future_seconds = (observed_at - self.now).total_seconds()
                age_seconds = max(0, int((self.now - observed_at).total_seconds()))
                check["age_seconds"] = age_seconds
                if future_seconds > MAX_EVIDENCE_CLOCK_SKEW_SECONDS:
                    check.update(
                        {
                            "result": "failed",
                            "code": "evidence_observed_in_future",
                            "clock_skew_seconds": int(future_seconds),
                            "max_clock_skew_seconds": MAX_EVIDENCE_CLOCK_SKEW_SECONDS,
                        }
                    )
                    all_passed = False
                elif requirement.get("verified") is True and item.get("verified") is not True:
                    check.update({"result": "failed", "code": "evidence_unverified"})
                    all_passed = False
                elif "freshness_seconds" in requirement and age_seconds > requirement["freshness_seconds"]:
                    check.update({"result": "failed", "code": "evidence_stale", "freshness_seconds": requirement["freshness_seconds"]})
                    all_passed = False
                elif self.project_trust.exists() and requirement.get("verified") is True:
                    verified, claims, message = self._verify_assertion(
                        item.get("attestation"), "evidence_attestation"
                    )
                    check.update({"issuer_verified": verified, "issuer_message": message})
                    required_roles = set(requirement.get("issuer_roles") or ["evidence_issuer"])
                    try:
                        source = claims.get("source") if claims else None
                        source_kind = source.get("kind") if isinstance(source, dict) else None
                        if not verified or claims is None:
                            check.update({"result": "failed", "code": "evidence_issuer_unverified"})
                            all_passed = False
                        elif claims.get("evidence_id") != evidence_id:
                            check.update({"result": "failed", "code": "evidence_attestation_id_mismatch"})
                            all_passed = False
                        elif claims.get("sha256") != item["sha256"].lower():
                            check.update({"result": "failed", "code": "evidence_attestation_digest_mismatch"})
                            all_passed = False
                        elif claims.get("observed_at") != isoformat(observed_at):
                            check.update({"result": "failed", "code": "evidence_attestation_time_mismatch"})
                            all_passed = False
                        elif not isinstance(source_kind, str) or not source_kind:
                            check.update({"result": "failed", "code": "evidence_source_missing"})
                            all_passed = False
                        elif (
                            requirement.get("source_kinds")
                            and source_kind not in set(requirement["source_kinds"])
                        ):
                            check.update({"result": "failed", "code": "evidence_source_untrusted"})
                            all_passed = False
                        elif not any(
                            self.project_trust.signer_has_role(str(claims.get("_signer_id")), role)
                            for role in required_roles
                        ):
                            check.update({"result": "failed", "code": "evidence_issuer_unqualified"})
                            all_passed = False
                        else:
                            check.update(
                                {
                                    "result": "satisfied",
                                    "code": "evidence_satisfied",
                                    "sha256": item["sha256"],
                                    "issuer_id": claims.get("_signer_id"),
                                    "source_kind": source_kind,
                                }
                            )
                    except ValueError as exc:
                        check.update({"result": "failed", "code": "evidence_attestation_invalid", "message": str(exc)})
                        all_passed = False
                else:
                    check.update({"result": "satisfied", "code": "evidence_satisfied", "sha256": item["sha256"]})
            decision["evidence"].append(check)
            if check["result"] != "satisfied":
                decision["reasons"].append({"code": check["code"], "message": f"Evidence {evidence_id} is {check['result']}."})
                decision["remediation"].append(f"Attach current verified evidence {evidence_id} and validate again.")
        return all_passed

    def _validate_approvals(
        self,
        contract: Dict[str, Any],
        request: Dict[str, Any],
        decision: Dict[str, Any],
        digests: Dict[str, str],
    ) -> bool:
        requirement = contract.get("approval", {"required": False})
        required = requirement.get("required", False) or contract["policy"]["decision"] == "approval_required"
        decision["approval"] = {"required": required, "satisfied": not required, "checks": []}
        if not required:
            return True
        required_roles = set(requirement.get("roles") or [])
        for artifact in request.get("approvals", []):
            verified, claims, message = self._verify_assertion(artifact, "action_approval")
            check: Dict[str, Any] = {"verified": verified, "message": message}
            if not verified or claims is None:
                check["code"] = "approval_unverified"
            else:
                check.update({"approval_id": claims.get("approval_id"), "role": claims.get("role"), "approver_id": claims.get("approver_id")})
                try:
                    if self.project_trust.exists() and claims.get("approver_id") != claims.get("_signer_id"):
                        check["code"] = "approval_signer_mismatch"
                    elif self.project_trust.exists() and not self.project_trust.signer_has_role(
                        str(claims.get("_signer_id")), str(claims.get("role"))
                    ):
                        check["code"] = "approval_role_unqualified"
                    elif claims.get("action_name") != decision["action_name"] or claims.get("request_id") != request["request_id"]:
                        check["code"] = "approval_request_mismatch"
                    elif claims.get("action_digest") != digests["request_digest"] or claims.get("evidence_digest") != digests["evidence_digest"]:
                        check["code"] = "approval_digest_mismatch"
                    elif parse_timestamp(claims.get("expires_at"), "approval.expires_at") <= self.now:
                        check["code"] = "approval_expired"
                    elif required_roles and claims.get("role") not in required_roles:
                        check["code"] = "approval_role_unqualified"
                    else:
                        check["code"] = "approval_valid"
                except ValueError as exc:
                    check.update({"code": "approval_invalid", "message": str(exc)})
            decision["approval"]["checks"].append(check)
        valid_roles = {
            check.get("role")
            for check in decision["approval"]["checks"]
            if check.get("code") == "approval_valid"
        }
        decision["approval"]["satisfied"] = bool(valid_roles) if required_roles else bool(valid_roles)
        if not decision["approval"]["satisfied"]:
            decision["reasons"].append(
                {"code": "approval_missing_or_invalid", "message": "No current approval is bound to the action and evidence digest."}
            )
        return decision["approval"]["satisfied"]
