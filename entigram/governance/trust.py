"""Shared project trust registry and Ed25519 identities.

Private signer material is intentionally kept outside a workspace.  A workspace
commits only ``.etg/trust.yaml``: public keys, roles, enrolled agent runtimes
and versions, quorum rules, and signed key-transition history. This is the
local-file foundation for a future IAM or KMS adapter; it does not require
collaborators to share a private key or agents to assert their own identity.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import yaml
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)


TRUST_REGISTRY_FILE = ".etg/trust.yaml"
TRUST_REGISTRY_VERSION = "entigram.project-trust.v2"
TRUST_ANCHOR_VERSION = "entigram.project-trust-anchor.v1"
IDENTITY_SIGNATURE_TYPE = "entigram.identity.ed25519.v1"
PUBLIC_IDENTITY_TYPE = "entigram.identity-public-key.v1"
AGENT_SIGNATURE_TYPE = "entigram.agent-attestation.ed25519.v1"
PUBLIC_AGENT_IDENTITY_TYPE = "entigram.agent-public-key.v1"
_IDENTIFIER = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]*$")
_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:+-]*$")
_KEY_STATES = {"active", "retired", "revoked"}
MAX_AGENT_ATTESTATION_TTL_SECONDS = 900
MAX_AGENT_ATTESTATION_FUTURE_SKEW_SECONDS = 60


class TrustRegistryError(ValueError):
    """Raised when trust material is malformed or an operation is unauthorized."""


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_timestamp(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise TrustRegistryError(f"{field} must be an RFC 3339 timestamp")
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise TrustRegistryError(f"{field} must be an RFC 3339 timestamp") from exc
    if parsed.tzinfo is None:
        raise TrustRegistryError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _identifier(value: Any, field: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise TrustRegistryError(
            f"{field} must use letters, digits, '.', '_', ':', or '-' and begin with a letter"
        )
    return value


def _version(value: Any, field: str) -> str:
    if not isinstance(value, str) or not _VERSION.fullmatch(value):
        raise TrustRegistryError(
            f"{field} must use letters, digits, '.', '_', ':', '+', or '-' and begin with a letter or digit"
        )
    return value


def _string_list(value: Any, field: str, *, allow_empty: bool = False) -> List[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
        raise TrustRegistryError(f"{field} must be a list of non-empty strings")
    normalized = [item.strip() for item in value]
    if not allow_empty and not normalized:
        raise TrustRegistryError(f"{field} must not be empty")
    return normalized


def _raw_public_key(encoded: Any, field: str = "public_key") -> bytes:
    if not isinstance(encoded, str):
        raise TrustRegistryError(f"{field} must be base64-encoded Ed25519 public-key bytes")
    try:
        value = base64.b64decode(encoded, validate=True)
        Ed25519PublicKey.from_public_bytes(value)
    except (ValueError, TypeError) as exc:
        raise TrustRegistryError(f"{field} is not a valid Ed25519 public key") from exc
    return value


def key_id(public_key_bytes: bytes) -> str:
    return hashlib.sha256(public_key_bytes).hexdigest()


def default_identity_path(signer_id: str, key_name: str = "default") -> Path:
    """Return a per-user path, intentionally outside every project checkout."""
    safe_signer = signer_id.replace(":", "_").replace("/", "_")
    safe_name = key_name.replace(":", "_").replace("/", "_")
    root = Path(os.environ.get("ENTIGRAM_IDENTITY_DIR", Path.home() / ".config" / "entigram" / "identities"))
    return root.expanduser() / f"{safe_signer}-{safe_name}-ed25519.pem"


def default_agent_identity_path(agent_id: str, key_name: str = "default") -> Path:
    """Return a host-controlled agent-key path outside a project checkout.

    This path is deliberately distinct from a person's signing identity. An
    adapter host or a workload secret store should control the resulting key;
    it must never be made available to an LLM through its workspace context.
    """
    safe_agent = agent_id.replace(":", "_").replace("/", "_")
    safe_name = key_name.replace(":", "_").replace("/", "_")
    root = Path(os.environ.get("ENTIGRAM_AGENT_IDENTITY_DIR", Path.home() / ".config" / "entigram" / "agents"))
    return root.expanduser() / f"{safe_agent}-{safe_name}-ed25519.pem"


def default_trust_anchor_path(project_id: str) -> Path:
    """Return the local, non-workspace pin for a project's signed trust root."""
    safe_project = project_id.replace(":", "_").replace("/", "_")
    root = Path(os.environ.get("ENTIGRAM_TRUST_ANCHOR_DIR", Path.home() / ".config" / "entigram" / "trust-anchors"))
    return root.expanduser() / f"{safe_project}.json"


class PersonalIdentity:
    """A person-controlled signing key stored outside the project workspace."""

    def __init__(self, signer_id: str, key_path: str | Path):
        self.signer_id = _identifier(signer_id, "signer_id")
        self.key_path = Path(key_path).expanduser().resolve()

    def create(self) -> Dict[str, str]:
        if self.key_path.exists():
            raise TrustRegistryError(f"identity key already exists: {self.key_path}")
        self.key_path.parent.mkdir(parents=True, exist_ok=True)
        private_key = Ed25519PrivateKey.generate()
        self.key_path.write_bytes(
            private_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption(),
            )
        )
        try:
            self.key_path.chmod(0o600)
        except OSError:
            pass
        return self.public_record()

    def _private_key(self) -> Ed25519PrivateKey:
        if not self.key_path.is_file():
            raise TrustRegistryError(f"identity key is not available: {self.key_path}")
        loaded = serialization.load_pem_private_key(self.key_path.read_bytes(), password=None)
        if not isinstance(loaded, Ed25519PrivateKey):
            raise TrustRegistryError(f"identity key is not Ed25519: {self.key_path}")
        return loaded

    def public_record(self) -> Dict[str, str]:
        public = self._private_key().public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        return {
            "type": PUBLIC_IDENTITY_TYPE,
            "signer_id": self.signer_id,
            "key_id": key_id(public),
            "public_key": base64.b64encode(public).decode("ascii"),
        }

    def sign(self, kind: str, claims: Dict[str, Any]) -> Dict[str, Any]:
        _identifier(kind, "signature kind")
        record = self.public_record()
        payload = {
            "type": IDENTITY_SIGNATURE_TYPE,
            "kind": kind,
            "signer_id": self.signer_id,
            "key_id": record["key_id"],
            "claims": claims,
        }
        signature = self._private_key().sign(canonical_json(payload))
        return {**payload, "signature": base64.b64encode(signature).decode("ascii")}


class AgentIdentity:
    """A host-controlled workload key for signed action-request attestations.

    Creating a key does not authorize the agent. A trusted human must enroll
    its public record in the project registry with a runtime and an explicit
    set of allowed versions before an action admission will accept it.
    """

    def __init__(self, agent_id: str, runtime: str, key_path: str | Path):
        self.agent_id = _identifier(agent_id, "agent_id")
        self.runtime = _identifier(runtime, "runtime")
        self.key_path = Path(key_path).expanduser().resolve()

    def create(self) -> Dict[str, str]:
        if self.key_path.exists():
            raise TrustRegistryError(f"agent identity key already exists: {self.key_path}")
        self.key_path.parent.mkdir(parents=True, exist_ok=True)
        private_key = Ed25519PrivateKey.generate()
        self.key_path.write_bytes(
            private_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption(),
            )
        )
        try:
            self.key_path.chmod(0o600)
        except OSError:
            pass
        return self.public_record()

    def _private_key(self) -> Ed25519PrivateKey:
        if not self.key_path.is_file():
            raise TrustRegistryError(f"agent identity key is not available: {self.key_path}")
        loaded = serialization.load_pem_private_key(self.key_path.read_bytes(), password=None)
        if not isinstance(loaded, Ed25519PrivateKey):
            raise TrustRegistryError(f"agent identity key is not Ed25519: {self.key_path}")
        return loaded

    def public_record(self) -> Dict[str, str]:
        public = self._private_key().public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        return {
            "type": PUBLIC_AGENT_IDENTITY_TYPE,
            "agent_id": self.agent_id,
            "runtime": self.runtime,
            "key_id": key_id(public),
            "public_key": base64.b64encode(public).decode("ascii"),
        }

    def sign(self, kind: str, claims: Dict[str, Any]) -> Dict[str, Any]:
        _identifier(kind, "signature kind")
        record = self.public_record()
        payload = {
            "type": AGENT_SIGNATURE_TYPE,
            "kind": kind,
            "agent_id": self.agent_id,
            "runtime": self.runtime,
            "key_id": record["key_id"],
            "claims": claims,
        }
        signature = self._private_key().sign(canonical_json(payload))
        return {**payload, "signature": base64.b64encode(signature).decode("ascii")}


class ProjectTrustRegistry:
    """Verifies personal signatures and records governed key transitions."""

    def __init__(self, target_dir: str | Path, *, anchor_path: str | Path | None = None):
        self.target_dir = Path(target_dir).expanduser().resolve()
        self.path = self.target_dir / TRUST_REGISTRY_FILE
        self.anchor_path = Path(anchor_path).expanduser().resolve() if anchor_path else None

    def exists(self) -> bool:
        return self.path.is_file()

    def load(self) -> Dict[str, Any]:
        if not self.path.is_file():
            raise TrustRegistryError(f"missing {TRUST_REGISTRY_FILE}")
        try:
            document = yaml.safe_load(self.path.read_text()) or {}
        except yaml.YAMLError as exc:
            raise TrustRegistryError(f"could not parse {TRUST_REGISTRY_FILE}: {exc}") from exc
        self._validate_document(document)
        return document

    def initialize(
        self,
        *,
        project_id: str,
        owner_public_key: Dict[str, str],
        owner_roles: Iterable[str],
        owner_identity: PersonalIdentity,
        recovery_quorum: int = 1,
    ) -> Dict[str, Any]:
        if self.path.exists():
            raise TrustRegistryError(f"trust registry already exists: {self.path}")
        if not isinstance(recovery_quorum, int) or recovery_quorum < 1:
            raise TrustRegistryError("recovery_quorum must be a positive integer")
        signer = self._signer_from_public_record(owner_public_key, list(owner_roles))
        if owner_identity.signer_id != signer["signer_id"]:
            raise TrustRegistryError("initial trust root must be signed by its owner identity")
        if "trust_admin" not in signer["roles"] or "recovery_admin" not in signer["roles"]:
            raise TrustRegistryError("initial signer must have trust_admin and recovery_admin roles")
        created_at = iso_now()
        root = {
            "project_id": _identifier(project_id, "project_id"),
            "created_at": created_at,
            "quorum": {
                "trust_change": {"minimum": 1, "roles": ["trust_admin"]},
                "recovery": {"minimum": recovery_quorum, "roles": ["recovery_admin"]},
            },
            "signers": [signer],
            "agents": [],
            "revoked_grants": [],
        }
        root_signature = owner_identity.sign(
            "trust_root",
            {"project_id": root["project_id"], "root_digest": digest(root), "created_at": created_at},
        )
        document = {
            "format": TRUST_REGISTRY_VERSION,
            "project_id": root["project_id"],
            "version": 1,
            "root": root,
            "root_signature": root_signature,
            "quorum": copy.deepcopy(root["quorum"]),
            "signers": copy.deepcopy(root["signers"]),
            "agents": [],
            "revoked_grants": [],
            "events": [],
        }
        self._validate_state(document)
        self._validate_root(document)
        # Establish the local root pin before writing the workspace registry.
        # A conflicting anchor must leave the prospective workspace untouched.
        self._write_anchor(document)
        self._write(document)
        self._validate_document(document)
        return document

    def registry_digest(self, document: Optional[Dict[str, Any]] = None) -> str:
        return digest(document if document is not None else self.load())

    def signer_has_role(self, signer_id: str, role: str, *, active_only: bool = True) -> bool:
        document = self.load()
        signer = self._find_signer(document, signer_id)
        if not signer or role not in signer["roles"]:
            return False
        if not active_only:
            return True
        return any(key["state"] == "active" for key in signer["keys"])

    def is_grant_revoked(self, grant_id: str) -> bool:
        """Return whether a signed project-wide grant-revocation event exists."""
        return self.get_grant_revocation(grant_id) is not None

    def get_grant_revocation(self, grant_id: str) -> Optional[Dict[str, Any]]:
        """Return the signed project-wide revocation for a grant, if present."""
        if not isinstance(grant_id, str) or not grant_id:
            return None
        return next(
            (item for item in self.load().get("revoked_grants", []) if item["grant_id"] == grant_id),
            None,
        )

    def verify(
        self,
        artifact: Any,
        kind: str,
        *,
        require_active: bool = True,
    ) -> Tuple[bool, Optional[Dict[str, Any]], str]:
        if not isinstance(artifact, dict):
            return False, None, "missing signed assertion"
        if artifact.get("type") != IDENTITY_SIGNATURE_TYPE or artifact.get("kind") != kind:
            return False, None, "assertion type or kind is not supported"
        try:
            signer_id = _identifier(artifact.get("signer_id"), "signer_id")
            supplied_key_id = artifact.get("key_id")
            document = self.load()
            signer = self._find_signer(document, signer_id)
            if signer is None:
                return False, None, "signer is not trusted by this project"
            key = next((item for item in signer["keys"] if item["key_id"] == supplied_key_id), None)
            if key is None:
                return False, None, "signer key is not trusted by this project"
            if require_active and key["state"] != "active":
                return False, None, f"signer key is {key['state']}"
            claims = artifact.get("claims")
            if not isinstance(claims, dict):
                return False, None, "assertion claims must be an object"
            signature = base64.b64decode(artifact.get("signature", ""), validate=True)
            payload = {
                "type": IDENTITY_SIGNATURE_TYPE,
                "kind": kind,
                "signer_id": signer_id,
                "key_id": supplied_key_id,
                "claims": claims,
            }
            Ed25519PublicKey.from_public_bytes(_raw_public_key(key["public_key"])).verify(
                signature, canonical_json(payload)
            )
            enriched = {**claims, "_signer_id": signer_id, "_key_id": supplied_key_id}
            return True, enriched, "verified"
        except (TrustRegistryError, ValueError, TypeError, InvalidSignature) as exc:
            return False, None, f"invalid assertion signature: {exc}"

    def verify_agent_attestation(
        self,
        artifact: Any,
        *,
        action_name: str,
        request_id: str,
        request_digest: str,
        now: datetime,
    ) -> Tuple[bool, Optional[Dict[str, Any]], str]:
        """Verify a short-lived action request signed by an enrolled agent.

        The agent key signs the exact request digest. The registry supplies the
        human-approved owner, runtime, allowed versions, and key state. A
        string such as ``agent:codex`` in an action request therefore no
        longer establishes an identity by itself.
        """
        if not isinstance(artifact, dict):
            return False, None, "agent_attestation_missing"
        if artifact.get("type") != AGENT_SIGNATURE_TYPE or artifact.get("kind") != "action_request":
            return False, None, "agent_attestation_type_unsupported"
        try:
            agent_id = _identifier(artifact.get("agent_id"), "agent_id")
            runtime = _identifier(artifact.get("runtime"), "runtime")
            supplied_key_id = artifact.get("key_id")
            document = self.load()
            agent = self._find_agent(document, agent_id)
            if agent is None:
                return False, None, "agent_unenrolled"
            if agent["runtime"] != runtime:
                return False, None, "agent_runtime_mismatch"
            owner = self._find_signer(document, agent["owner_id"])
            if owner is None or not any(key["state"] == "active" for key in owner["keys"]):
                return False, None, "agent_owner_inactive"
            key = next((item for item in agent["keys"] if item["key_id"] == supplied_key_id), None)
            if key is None:
                return False, None, "agent_key_unenrolled"
            if key["state"] != "active":
                return False, None, f"agent_key_{key['state']}"
            claims = artifact.get("claims")
            if not isinstance(claims, dict):
                return False, None, "agent_attestation_claims_invalid"
            signature = base64.b64decode(artifact.get("signature", ""), validate=True)
            payload = {
                "type": AGENT_SIGNATURE_TYPE,
                "kind": "action_request",
                "agent_id": agent_id,
                "runtime": runtime,
                "key_id": supplied_key_id,
                "claims": claims,
            }
            Ed25519PublicKey.from_public_bytes(_raw_public_key(key["public_key"])).verify(
                signature, canonical_json(payload)
            )
            if claims.get("agent_id") != agent_id or claims.get("runtime") != runtime:
                return False, None, "agent_attestation_subject_mismatch"
            version = _version(claims.get("version"), "agent attestation version")
            if version not in agent["allowed_versions"]:
                return False, None, "agent_version_unenrolled"
            if claims.get("action_name") != action_name or claims.get("request_id") != request_id:
                return False, None, "agent_attestation_request_mismatch"
            if claims.get("request_digest") != request_digest:
                return False, None, "agent_attestation_digest_mismatch"
            _identifier(claims.get("attestation_id"), "agent attestation id")
            _identifier(claims.get("nonce"), "agent attestation nonce")
            issued_at = _parse_timestamp(claims.get("issued_at"), "agent attestation issued_at")
            expires_at = _parse_timestamp(claims.get("expires_at"), "agent attestation expires_at")
            if issued_at > now.astimezone(timezone.utc) + timedelta(
                seconds=MAX_AGENT_ATTESTATION_FUTURE_SKEW_SECONDS
            ):
                return False, None, "agent_attestation_issued_in_future"
            if (expires_at - issued_at).total_seconds() > MAX_AGENT_ATTESTATION_TTL_SECONDS:
                return False, None, "agent_attestation_ttl_exceeded"
            if expires_at <= issued_at:
                return False, None, "agent_attestation_expiry_invalid"
            if expires_at <= now.astimezone(timezone.utc):
                return False, None, "agent_attestation_expired"
            return True, {
                **claims,
                "_agent_id": agent_id,
                "_runtime": runtime,
                "_key_id": supplied_key_id,
                "_owner_id": agent["owner_id"],
            }, "agent_attestation_valid"
        except (TrustRegistryError, ValueError, TypeError, InvalidSignature) as exc:
            return False, None, f"agent_attestation_invalid: {exc}"

    def make_change(
        self,
        *,
        operation: str,
        signer_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        owner_id: Optional[str] = None,
        runtime: Optional[str] = None,
        version: Optional[str] = None,
        grant_id: Optional[str] = None,
        grant: Optional[Dict[str, Any]] = None,
        public_key: Optional[Dict[str, str]] = None,
        roles: Optional[Iterable[str]] = None,
        key_id_to_revoke: Optional[str] = None,
    ) -> Dict[str, Any]:
        allowed = {
            "add_signer",
            "rotate_key",
            "recover_key",
            "revoke_key",
            "revoke_grant",
            "enroll_agent",
            "add_agent_version",
            "remove_agent_version",
            "rotate_agent_key",
            "revoke_agent_key",
        }
        if operation not in allowed:
            raise TrustRegistryError(f"operation must be one of {', '.join(sorted(allowed))}")
        document = self.load()
        change: Dict[str, Any] = {
            "type": "entigram.trust-change.v1",
            "change_id": f"trust-change-{uuid.uuid4()}",
            "project_id": document["project_id"],
            "registry_digest": self.registry_digest(document),
            "operation": operation,
            "created_at": iso_now(),
        }
        signer_operations = {"add_signer", "rotate_key", "recover_key", "revoke_key", "revoke_grant"}
        agent_operations = {
            "enroll_agent", "add_agent_version", "remove_agent_version", "rotate_agent_key", "revoke_agent_key"
        }
        if operation in signer_operations:
            change["signer_id"] = _identifier(signer_id, "signer_id")
        if operation in agent_operations:
            change["agent_id"] = _identifier(agent_id, "agent_id")
        if operation in {"add_signer", "rotate_key", "recover_key"}:
            if not isinstance(public_key, dict):
                raise TrustRegistryError("public_key is required for this trust change")
            normalized = self._public_record(public_key)
            if normalized["signer_id"] != change["signer_id"]:
                raise TrustRegistryError("public key signer_id must match trust change signer_id")
            change["public_key"] = normalized
        if operation == "add_signer":
            change["roles"] = _string_list(list(roles or []), "roles")
        if operation == "revoke_key":
            if not isinstance(key_id_to_revoke, str) or not key_id_to_revoke:
                raise TrustRegistryError("key_id_to_revoke is required for revoke_key")
            change["key_id_to_revoke"] = key_id_to_revoke
        if operation == "revoke_grant":
            change["grant_id"] = _identifier(grant_id, "grant_id")
            if not isinstance(grant, dict):
                raise TrustRegistryError("signed grant is required for revoke_grant")
            change["grant"] = copy.deepcopy(grant)
        if operation == "enroll_agent":
            if not isinstance(public_key, dict):
                raise TrustRegistryError("public_key is required for agent enrollment")
            normalized_agent_key = self._agent_public_record(public_key)
            if normalized_agent_key["agent_id"] != change["agent_id"]:
                raise TrustRegistryError("agent public key agent_id must match trust change agent_id")
            change["owner_id"] = _identifier(owner_id, "owner_id")
            change["runtime"] = _identifier(runtime, "runtime")
            if normalized_agent_key["runtime"] != change["runtime"]:
                raise TrustRegistryError("agent public key runtime must match trust change runtime")
            change["version"] = _version(version, "version")
            change["public_key"] = normalized_agent_key
        elif operation in {"add_agent_version", "remove_agent_version"}:
            change["version"] = _version(version, "version")
        elif operation == "rotate_agent_key":
            if not isinstance(public_key, dict):
                raise TrustRegistryError("public_key is required for agent key rotation")
            normalized_agent_key = self._agent_public_record(public_key)
            if normalized_agent_key["agent_id"] != change["agent_id"]:
                raise TrustRegistryError("agent public key agent_id must match trust change agent_id")
            change["public_key"] = normalized_agent_key
        elif operation == "revoke_agent_key":
            if not isinstance(key_id_to_revoke, str) or not key_id_to_revoke:
                raise TrustRegistryError("key_id_to_revoke is required for revoke_agent_key")
            change["key_id_to_revoke"] = key_id_to_revoke
        change["change_digest"] = digest(change)
        return change

    def approve_change(self, change: Dict[str, Any], identity: PersonalIdentity) -> Dict[str, Any]:
        self._validate_change(change)
        return identity.sign(
            "trust_change_approval",
            {
                "approval_id": f"trust-approval-{uuid.uuid4()}",
                "change_id": change["change_id"],
                "change_digest": change["change_digest"],
                "registry_digest": change["registry_digest"],
                "operation": change["operation"],
                "approved_at": iso_now(),
            },
        )

    def apply_change(self, change: Dict[str, Any], approvals: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
        document = self.load()
        approval_list = list(approvals)
        applied_at = iso_now()
        approvers = self._apply_state_change(document, change, approval_list, applied_at=applied_at)
        operation = change["operation"]

        event = {
            "event_id": f"trust-event-{uuid.uuid4()}",
            "event_type": operation,
            "change": change,
            "approvals": approval_list,
            "approvers": sorted(approvers),
            "applied_at": applied_at,
        }
        event["event_digest"] = digest(event)
        document["events"].append(event)
        self._validate_document(document)
        self._write(document)
        return event

    def _validate_approvals(
        self,
        change: Dict[str, Any],
        approvals: Iterable[Dict[str, Any]],
        document: Dict[str, Any],
    ) -> List[str]:
        approvers: List[str] = []
        for artifact in approvals:
            valid, claims, _ = self._verify_personal_assertion(
                artifact, "trust_change_approval", document, require_active=True
            )
            if not valid or claims is None:
                continue
            if (
                claims.get("change_id") != change["change_id"]
                or claims.get("change_digest") != change["change_digest"]
                or claims.get("registry_digest") != change["registry_digest"]
                or claims.get("operation") != change["operation"]
            ):
                continue
            signer_id = claims["_signer_id"]
            if signer_id not in approvers:
                approvers.append(signer_id)
        if not approvers:
            raise TrustRegistryError("no current trusted signer approved this change")
        return approvers

    def _verify_personal_assertion(
        self,
        artifact: Any,
        kind: str,
        document: Dict[str, Any],
        *,
        require_active: bool,
    ) -> Tuple[bool, Optional[Dict[str, Any]], str]:
        if not isinstance(artifact, dict):
            return False, None, "missing signed assertion"
        if artifact.get("type") != IDENTITY_SIGNATURE_TYPE or artifact.get("kind") != kind:
            return False, None, "assertion type or kind is not supported"
        try:
            signer_id = _identifier(artifact.get("signer_id"), "signer_id")
            supplied_key_id = artifact.get("key_id")
            signer = self._find_signer(document, signer_id)
            if signer is None:
                return False, None, "signer is not trusted by this project"
            key = next((item for item in signer["keys"] if item["key_id"] == supplied_key_id), None)
            if key is None:
                return False, None, "signer key is not trusted by this project"
            if require_active and key["state"] != "active":
                return False, None, f"signer key is {key['state']}"
            claims = artifact.get("claims")
            if not isinstance(claims, dict):
                return False, None, "assertion claims must be an object"
            signature = base64.b64decode(artifact.get("signature", ""), validate=True)
            payload = {
                "type": IDENTITY_SIGNATURE_TYPE,
                "kind": kind,
                "signer_id": signer_id,
                "key_id": supplied_key_id,
                "claims": claims,
            }
            Ed25519PublicKey.from_public_bytes(_raw_public_key(key["public_key"])).verify(
                signature, canonical_json(payload)
            )
            return True, {**claims, "_signer_id": signer_id, "_key_id": supplied_key_id}, "verified"
        except (TrustRegistryError, ValueError, TypeError, InvalidSignature) as exc:
            return False, None, f"invalid assertion signature: {exc}"

    def _apply_state_change(
        self,
        document: Dict[str, Any],
        change: Dict[str, Any],
        approvals: Iterable[Dict[str, Any]],
        *,
        applied_at: str,
    ) -> List[str]:
        """Apply one already-signed change to a registry state, deterministically."""
        self._validate_change(change)
        if change["project_id"] != document["project_id"]:
            raise TrustRegistryError("trust change is for a different project")
        if change["registry_digest"] != digest(document):
            raise TrustRegistryError("trust registry changed; create and approve a fresh change")
        approvers = self._validate_approvals(change, approvals, document)
        operation = change["operation"]
        subject = change.get("signer_id") or change.get("agent_id")
        if operation == "rotate_key":
            if subject not in approvers:
                raise TrustRegistryError("key rotation requires a signature from the current signer")
        elif operation == "recover_key":
            self._require_quorum(document, approvers, "recovery")
        elif operation == "revoke_grant":
            issuer_id = change["signer_id"]
            if issuer_id not in approvers:
                raise TrustRegistryError("grant revocation requires a signature from its issuing signer")
            if not self._signer_has_role(document, issuer_id, "authority_issuer"):
                raise TrustRegistryError("grant revocation signer lacks the authority_issuer role")
            valid_grant, grant_claims, message = self._verify_personal_assertion(
                change["grant"], "authority_grant", document, require_active=False
            )
            if not valid_grant or grant_claims is None:
                raise TrustRegistryError(f"grant revocation must include a valid signed grant: {message}")
            if grant_claims.get("grant_id") != change["grant_id"]:
                raise TrustRegistryError("grant revocation grant_id does not match the signed grant")
            if grant_claims.get("issuer_id") != issuer_id or grant_claims.get("_signer_id") != issuer_id:
                raise TrustRegistryError("grant revocation signer does not match the grant issuer")
        else:
            self._require_quorum(document, approvers, "trust_change")

        if operation == "add_signer":
            if self._find_signer(document, subject):
                raise TrustRegistryError("signer already exists; use rotate_key or recover_key")
            document["signers"].append(
                self._signer_from_public_record(
                    change["public_key"], change["roles"], registered_at=applied_at
                )
            )
        elif operation in {"rotate_key", "recover_key"}:
            signer = self._find_signer(document, subject)
            if signer is None:
                raise TrustRegistryError("signer does not exist; use add_signer")
            if any(item["key_id"] == change["public_key"]["key_id"] for item in signer["keys"]):
                raise TrustRegistryError("new key is already registered for this signer")
            replacement_state = "retired" if operation == "rotate_key" else "revoked"
            for item in signer["keys"]:
                if item["state"] == "active":
                    item["state"] = replacement_state
                    item["ended_at"] = applied_at
            signer["keys"].append(self._key_from_public_record(change["public_key"], registered_at=applied_at))
        elif operation == "revoke_key":
            signer = self._find_signer(document, subject)
            if signer is None:
                raise TrustRegistryError("signer does not exist")
            key = next((item for item in signer["keys"] if item["key_id"] == change["key_id_to_revoke"]), None)
            if key is None:
                raise TrustRegistryError("key is not registered for signer")
            key["state"] = "revoked"
            key["ended_at"] = applied_at
        elif operation == "revoke_grant":
            if any(item["grant_id"] == change["grant_id"] for item in document["revoked_grants"]):
                raise TrustRegistryError("grant is already revoked")
            document["revoked_grants"].append(
                {
                    "grant_id": change["grant_id"],
                    "issuer_id": change["signer_id"],
                    "grant_digest": digest(change["grant"]),
                    "revoked_at": applied_at,
                    "change_id": change["change_id"],
                }
            )
        elif operation == "enroll_agent":
            if self._find_agent(document, subject):
                raise TrustRegistryError("agent already exists; use add_agent_version or rotate_agent_key")
            if self._find_signer(document, change["owner_id"]) is None:
                raise TrustRegistryError("agent owner must be an enrolled project signer")
            document["agents"].append(
                self._agent_from_public_record(
                    change["public_key"],
                    owner_id=change["owner_id"],
                    runtime=change["runtime"],
                    version=change["version"],
                    registered_at=applied_at,
                )
            )
        elif operation == "add_agent_version":
            agent = self._find_agent(document, subject)
            if agent is None:
                raise TrustRegistryError("agent does not exist; enroll it first")
            if change["version"] in agent["allowed_versions"]:
                raise TrustRegistryError("agent version is already allowed")
            agent["allowed_versions"].append(change["version"])
            agent["allowed_versions"].sort()
        elif operation == "remove_agent_version":
            agent = self._find_agent(document, subject)
            if agent is None:
                raise TrustRegistryError("agent does not exist")
            if change["version"] not in agent["allowed_versions"]:
                raise TrustRegistryError("agent version is not currently allowed")
            if len(agent["allowed_versions"]) == 1:
                raise TrustRegistryError("cannot remove the final allowed version; revoke the agent key to disable the workload")
            agent["allowed_versions"].remove(change["version"])
        elif operation == "rotate_agent_key":
            agent = self._find_agent(document, subject)
            if agent is None:
                raise TrustRegistryError("agent does not exist; enroll it first")
            public_key = self._agent_public_record(change["public_key"])
            if public_key["runtime"] != agent["runtime"]:
                raise TrustRegistryError("agent key runtime does not match enrolled runtime")
            if any(item["key_id"] == public_key["key_id"] for item in agent["keys"]):
                raise TrustRegistryError("new agent key is already registered")
            for item in agent["keys"]:
                if item["state"] == "active":
                    item["state"] = "retired"
                    item["ended_at"] = applied_at
            agent["keys"].append(self._agent_key_from_public_record(public_key, registered_at=applied_at))
        elif operation == "revoke_agent_key":
            agent = self._find_agent(document, subject)
            if agent is None:
                raise TrustRegistryError("agent does not exist")
            key = next((item for item in agent["keys"] if item["key_id"] == change["key_id_to_revoke"]), None)
            if key is None:
                raise TrustRegistryError("key is not registered for agent")
            key["state"] = "revoked"
            key["ended_at"] = applied_at

        document["version"] += 1
        return approvers

    def _require_quorum(self, document: Dict[str, Any], approvers: List[str], rule_name: str) -> None:
        rule = document["quorum"][rule_name]
        qualified = [
            signer_id
            for signer_id in approvers
            if any(self._signer_has_role(document, signer_id, role) for role in rule["roles"])
        ]
        if len(qualified) < rule["minimum"]:
            raise TrustRegistryError(
                f"{rule_name} requires {rule['minimum']} trusted approver(s) with one of: {', '.join(rule['roles'])}"
            )

    @staticmethod
    def _signer_has_role(document: Dict[str, Any], signer_id: str, role: str) -> bool:
        signer = ProjectTrustRegistry._find_signer(document, signer_id)
        return bool(signer and role in signer["roles"] and any(key["state"] == "active" for key in signer["keys"]))

    @staticmethod
    def _find_signer(document: Dict[str, Any], signer_id: str) -> Optional[Dict[str, Any]]:
        return next((item for item in document["signers"] if item["signer_id"] == signer_id), None)

    @staticmethod
    def _find_agent(document: Dict[str, Any], agent_id: str) -> Optional[Dict[str, Any]]:
        return next((item for item in document.get("agents", []) if item["agent_id"] == agent_id), None)

    def _write(self, document: Dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(yaml.safe_dump(document, sort_keys=False))

    def _validate_change(self, change: Any) -> None:
        if not isinstance(change, dict) or change.get("type") != "entigram.trust-change.v1":
            raise TrustRegistryError("invalid trust change")
        required = {"change_id", "project_id", "registry_digest", "operation", "created_at", "change_digest"}
        missing = sorted(field for field in required if not change.get(field))
        if missing:
            raise TrustRegistryError(f"trust change missing: {', '.join(missing)}")
        signer_operations = {"add_signer", "rotate_key", "recover_key", "revoke_key", "revoke_grant"}
        agent_operations = {
            "enroll_agent", "add_agent_version", "remove_agent_version", "rotate_agent_key", "revoke_agent_key"
        }
        operation = change["operation"]
        if operation not in signer_operations | agent_operations:
            raise TrustRegistryError("trust change operation is not supported")
        if operation in signer_operations:
            _identifier(change.get("signer_id"), "trust change signer_id")
        else:
            _identifier(change.get("agent_id"), "trust change agent_id")
        if operation == "enroll_agent":
            _identifier(change.get("owner_id"), "trust change owner_id")
            _identifier(change.get("runtime"), "trust change runtime")
            _version(change.get("version"), "trust change version")
            self._agent_public_record(change.get("public_key"))
        elif operation in {"add_agent_version", "remove_agent_version"}:
            _version(change.get("version"), "trust change version")
        elif operation == "rotate_agent_key":
            self._agent_public_record(change.get("public_key"))
        elif operation in {"revoke_key", "revoke_agent_key"}:
            if not isinstance(change.get("key_id_to_revoke"), str) or not change["key_id_to_revoke"]:
                raise TrustRegistryError("trust change key_id_to_revoke is required")
        elif operation == "revoke_grant":
            _identifier(change.get("grant_id"), "trust change grant_id")
            if not isinstance(change.get("grant"), dict):
                raise TrustRegistryError("trust change signed grant is required")
        recompute = {key: value for key, value in change.items() if key != "change_digest"}
        if change["change_digest"] != digest(recompute):
            raise TrustRegistryError("trust change digest does not match its contents")

    def _anchor_file(self, document: Dict[str, Any]) -> Path:
        return self.anchor_path or default_trust_anchor_path(document["project_id"]).resolve()

    def _root_digest(self, document: Dict[str, Any]) -> str:
        root = document.get("root")
        if not isinstance(root, dict):
            raise TrustRegistryError("trust registry is missing a signed root")
        return digest(root)

    def _write_anchor(self, document: Dict[str, Any]) -> Path:
        path = self._anchor_file(document)
        path.parent.mkdir(parents=True, exist_ok=True)
        anchor = {
            "format": TRUST_ANCHOR_VERSION,
            "project_id": document["project_id"],
            "root_digest": self._root_digest(document),
            "root_signature_digest": digest(document["root_signature"]),
            "pinned_at": iso_now(),
        }
        if path.exists():
            try:
                existing = json.loads(path.read_text())
            except (OSError, json.JSONDecodeError) as exc:
                raise TrustRegistryError(f"could not read existing external trust anchor: {exc}") from exc
            if not isinstance(existing, dict):
                raise TrustRegistryError("existing external trust anchor is invalid; refuse to overwrite it")
            same_root = all(
                existing.get(field) == anchor[field]
                for field in ("format", "project_id", "root_digest", "root_signature_digest")
            )
            if same_root:
                return path
            raise TrustRegistryError(
                f"external trust anchor already exists for {document['project_id']}; refuse to overwrite it"
            )
        path.write_text(json.dumps(anchor, indent=2, sort_keys=True) + "\n")
        try:
            path.chmod(0o600)
        except OSError:
            pass
        return path

    def pin_root(self, *, expected_root_digest: str) -> Path:
        """Pin the already signed project root outside this workspace."""
        document = self._load_unanchored_document()
        self._validate_root(document)
        if expected_root_digest != self._root_digest(document):
            raise TrustRegistryError(
                "root digest does not match the verified out-of-band value; refuse to pin this workspace"
            )
        return self._write_anchor(document)

    def root_summary(self) -> Dict[str, str]:
        """Return a signed-root fingerprint before this host pins the project.

        This deliberately verifies only the root signature.  A collaborator
        compares this fingerprint over an independent channel before calling
        :meth:`pin_root`; all current state and history remains untrusted until
        the external pin exists and :meth:`load` can replay it.
        """
        document = self._load_unanchored_document()
        self._validate_root(document)
        return {
            "project_id": document["project_id"],
            "root_digest": self._root_digest(document),
            "root_signature_digest": digest(document["root_signature"]),
            "root_signer_id": str(document["root_signature"]["signer_id"]),
        }

    def _load_unanchored_document(self) -> Dict[str, Any]:
        if not self.path.is_file():
            raise TrustRegistryError(f"missing {TRUST_REGISTRY_FILE}")
        try:
            document = yaml.safe_load(self.path.read_text()) or {}
        except yaml.YAMLError as exc:
            raise TrustRegistryError(f"could not parse {TRUST_REGISTRY_FILE}: {exc}") from exc
        return document

    def _validate_root(self, document: Dict[str, Any]) -> None:
        root = document.get("root")
        signature = document.get("root_signature")
        if not isinstance(root, dict) or not isinstance(signature, dict):
            raise TrustRegistryError("trust registry requires root and root_signature")
        if root.get("project_id") != document.get("project_id"):
            raise TrustRegistryError("trust root project_id does not match registry")
        root_state = {
            "format": TRUST_REGISTRY_VERSION,
            "project_id": root.get("project_id"),
            "version": 1,
            "root": root,
            "root_signature": signature,
            "quorum": root.get("quorum"),
            "signers": root.get("signers"),
            "agents": root.get("agents"),
            "revoked_grants": root.get("revoked_grants"),
            "events": [],
        }
        self._validate_state(root_state)
        valid, claims, message = self._verify_personal_assertion(
            signature, "trust_root", root_state, require_active=True
        )
        if not valid or claims is None:
            raise TrustRegistryError(f"trust root signature is invalid: {message}")
        if claims.get("project_id") != document["project_id"]:
            raise TrustRegistryError("trust root signature project_id does not match registry")
        if claims.get("root_digest") != self._root_digest(document):
            raise TrustRegistryError("trust root signature does not bind this root")
        if claims.get("created_at") != root.get("created_at"):
            raise TrustRegistryError("trust root signature created_at does not match root")
        signer = self._find_signer(root_state, str(claims.get("_signer_id")))
        if signer is None or "trust_admin" not in signer["roles"] or "recovery_admin" not in signer["roles"]:
            raise TrustRegistryError("trust root signer lacks required root roles")

    def _verify_anchor(self, document: Dict[str, Any]) -> None:
        path = self._anchor_file(document)
        if not path.is_file():
            raise TrustRegistryError(
                f"missing external trust anchor: {path}; pin the verified project root before using shared trust"
            )
        try:
            anchor = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            raise TrustRegistryError(f"could not read external trust anchor: {exc}") from exc
        if not isinstance(anchor, dict) or anchor.get("format") != TRUST_ANCHOR_VERSION:
            raise TrustRegistryError("external trust anchor format is invalid")
        if anchor.get("project_id") != document["project_id"]:
            raise TrustRegistryError("external trust anchor is for a different project")
        if anchor.get("root_digest") != self._root_digest(document):
            raise TrustRegistryError("external trust anchor does not match the project root")
        if anchor.get("root_signature_digest") != digest(document["root_signature"]):
            raise TrustRegistryError("external trust anchor does not match the project root signature")

    def _root_state(self, document: Dict[str, Any]) -> Dict[str, Any]:
        root = document["root"]
        return {
            "format": TRUST_REGISTRY_VERSION,
            "project_id": document["project_id"],
            "version": 1,
            "root": copy.deepcopy(root),
            "root_signature": copy.deepcopy(document["root_signature"]),
            "quorum": copy.deepcopy(root["quorum"]),
            "signers": copy.deepcopy(root["signers"]),
            "agents": copy.deepcopy(root["agents"]),
            "revoked_grants": copy.deepcopy(root["revoked_grants"]),
            "events": [],
        }

    def _verify_history(self, document: Dict[str, Any]) -> None:
        replayed = self._root_state(document)
        for event in document["events"]:
            if not isinstance(event, dict):
                raise TrustRegistryError("trust event must be an object")
            required = {"event_id", "event_type", "change", "approvals", "approvers", "applied_at", "event_digest"}
            missing = sorted(field for field in required if field not in event)
            if missing:
                raise TrustRegistryError(f"trust event missing: {', '.join(missing)}")
            if event["event_digest"] != digest({key: value for key, value in event.items() if key != "event_digest"}):
                raise TrustRegistryError("trust event digest does not match its contents")
            if not isinstance(event["approvals"], list) or not isinstance(event["approvers"], list):
                raise TrustRegistryError("trust event approvals and approvers must be lists")
            _parse_timestamp(event["applied_at"], "trust event applied_at")
            change = event["change"]
            if not isinstance(change, dict) or event["event_type"] != change.get("operation"):
                raise TrustRegistryError("trust event type does not match its change")
            approvers = self._apply_state_change(
                replayed, change, event["approvals"], applied_at=event["applied_at"]
            )
            if sorted(approvers) != event["approvers"]:
                raise TrustRegistryError("trust event approver summary does not match signed approvals")
            replayed["events"].append(copy.deepcopy(event))

        for field in ("version", "quorum", "signers", "agents", "revoked_grants"):
            if canonical_json(document.get(field)) != canonical_json(replayed.get(field)):
                raise TrustRegistryError(f"trust registry {field} does not match its signed event history")

    def _validate_document(self, document: Any) -> None:
        self._validate_state(document)
        if not isinstance(document, dict):
            raise TrustRegistryError("trust registry must be an object")
        self._validate_root(document)
        self._verify_anchor(document)
        self._verify_history(document)

    def _validate_state(self, document: Any) -> None:
        if not isinstance(document, dict) or document.get("format") != TRUST_REGISTRY_VERSION:
            raise TrustRegistryError(f"format must be {TRUST_REGISTRY_VERSION}")
        _identifier(document.get("project_id"), "project_id")
        if not isinstance(document.get("version"), int) or document["version"] < 1:
            raise TrustRegistryError("version must be a positive integer")
        quorum = document.get("quorum")
        if not isinstance(quorum, dict):
            raise TrustRegistryError("quorum must be an object")
        for name in ("trust_change", "recovery"):
            rule = quorum.get(name)
            if not isinstance(rule, dict) or not isinstance(rule.get("minimum"), int) or rule["minimum"] < 1:
                raise TrustRegistryError(f"quorum.{name}.minimum must be a positive integer")
            _string_list(rule.get("roles"), f"quorum.{name}.roles")
        signers = document.get("signers")
        if not isinstance(signers, list) or not signers:
            raise TrustRegistryError("signers must be a non-empty list")
        seen_signers, seen_keys = set(), set()
        for signer in signers:
            if not isinstance(signer, dict):
                raise TrustRegistryError("each signer must be an object")
            signer_id = _identifier(signer.get("signer_id"), "signer.signer_id")
            if signer_id in seen_signers:
                raise TrustRegistryError("signer_id must be unique")
            seen_signers.add(signer_id)
            _string_list(signer.get("roles"), f"signer.{signer_id}.roles")
            keys = signer.get("keys")
            if not isinstance(keys, list) or not keys:
                raise TrustRegistryError(f"signer.{signer_id}.keys must be non-empty")
            for item in keys:
                if not isinstance(item, dict):
                    raise TrustRegistryError("signer key must be an object")
                public = _raw_public_key(item.get("public_key"))
                if item.get("key_id") != key_id(public):
                    raise TrustRegistryError("signer key_id does not match public_key")
                if item.get("key_id") in seen_keys:
                    raise TrustRegistryError("key_id must be unique across the project")
                seen_keys.add(item["key_id"])
                if item.get("state") not in _KEY_STATES:
                    raise TrustRegistryError("signer key state must be active, retired, or revoked")
        agents = document.get("agents", [])
        if not isinstance(agents, list):
            raise TrustRegistryError("agents must be a list")
        seen_agents = set()
        for agent in agents:
            if not isinstance(agent, dict):
                raise TrustRegistryError("each agent must be an object")
            agent_id = _identifier(agent.get("agent_id"), "agent.agent_id")
            if agent_id in seen_agents:
                raise TrustRegistryError("agent_id must be unique")
            seen_agents.add(agent_id)
            owner_id = _identifier(agent.get("owner_id"), f"agent.{agent_id}.owner_id")
            if owner_id not in seen_signers:
                raise TrustRegistryError("agent owner_id must identify an enrolled signer")
            _identifier(agent.get("runtime"), f"agent.{agent_id}.runtime")
            allowed_versions = _string_list(agent.get("allowed_versions"), f"agent.{agent_id}.allowed_versions")
            for version in allowed_versions:
                _version(version, f"agent.{agent_id}.allowed_versions entry")
            if len(set(allowed_versions)) != len(allowed_versions):
                raise TrustRegistryError("agent allowed_versions must not contain duplicates")
            keys = agent.get("keys")
            if not isinstance(keys, list) or not keys:
                raise TrustRegistryError(f"agent.{agent_id}.keys must be non-empty")
            for item in keys:
                if not isinstance(item, dict):
                    raise TrustRegistryError("agent key must be an object")
                public = _raw_public_key(item.get("public_key"))
                if item.get("key_id") != key_id(public):
                    raise TrustRegistryError("agent key_id does not match public_key")
                if item.get("key_id") in seen_keys:
                    raise TrustRegistryError("key_id must be unique across the project")
                seen_keys.add(item["key_id"])
                if item.get("state") not in _KEY_STATES:
                    raise TrustRegistryError("agent key state must be active, retired, or revoked")
        revoked_grants = document.get("revoked_grants", [])
        if not isinstance(revoked_grants, list):
            raise TrustRegistryError("revoked_grants must be a list")
        seen_grants = set()
        for item in revoked_grants:
            if not isinstance(item, dict):
                raise TrustRegistryError("each revoked grant must be an object")
            grant_id = _identifier(item.get("grant_id"), "revoked grant id")
            if grant_id in seen_grants:
                raise TrustRegistryError("grant_id may be revoked only once")
            seen_grants.add(grant_id)
            _identifier(item.get("issuer_id"), "revoked grant issuer_id")
            if not isinstance(item.get("grant_digest"), str) or len(item["grant_digest"]) != 64:
                raise TrustRegistryError("revoked grant grant_digest must be a SHA-256 digest")
            _parse_timestamp(item.get("revoked_at"), "revoked grant revoked_at")
            _identifier(item.get("change_id"), "revoked grant change_id")
        if not isinstance(document.get("events", []), list):
            raise TrustRegistryError("events must be a list")

    def _public_record(self, record: Dict[str, Any]) -> Dict[str, str]:
        if record.get("type") != PUBLIC_IDENTITY_TYPE:
            raise TrustRegistryError(f"public key type must be {PUBLIC_IDENTITY_TYPE}")
        signer_id = _identifier(record.get("signer_id"), "public key signer_id")
        public = _raw_public_key(record.get("public_key"))
        if record.get("key_id") != key_id(public):
            raise TrustRegistryError("public key key_id does not match public_key")
        return {
            "type": PUBLIC_IDENTITY_TYPE,
            "signer_id": signer_id,
            "key_id": record["key_id"],
            "public_key": record["public_key"],
        }

    def _agent_public_record(self, record: Any) -> Dict[str, str]:
        if not isinstance(record, dict) or record.get("type") != PUBLIC_AGENT_IDENTITY_TYPE:
            raise TrustRegistryError(f"agent public key type must be {PUBLIC_AGENT_IDENTITY_TYPE}")
        agent_id = _identifier(record.get("agent_id"), "agent public key agent_id")
        runtime = _identifier(record.get("runtime"), "agent public key runtime")
        public = _raw_public_key(record.get("public_key"))
        if record.get("key_id") != key_id(public):
            raise TrustRegistryError("agent public key key_id does not match public_key")
        return {
            "type": PUBLIC_AGENT_IDENTITY_TYPE,
            "agent_id": agent_id,
            "runtime": runtime,
            "key_id": record["key_id"],
            "public_key": record["public_key"],
        }

    def _key_from_public_record(self, record: Dict[str, str], *, registered_at: Optional[str] = None) -> Dict[str, str]:
        normalized = self._public_record(record)
        return {
            "key_id": normalized["key_id"],
            "public_key": normalized["public_key"],
            "state": "active",
            "registered_at": registered_at or iso_now(),
        }

    def _agent_key_from_public_record(
        self, record: Dict[str, str], *, registered_at: Optional[str] = None
    ) -> Dict[str, str]:
        normalized = self._agent_public_record(record)
        return {
            "key_id": normalized["key_id"],
            "public_key": normalized["public_key"],
            "state": "active",
            "registered_at": registered_at or iso_now(),
        }

    def _signer_from_public_record(
        self, record: Dict[str, str], roles: Iterable[str], *, registered_at: Optional[str] = None
    ) -> Dict[str, Any]:
        normalized = self._public_record(record)
        return {
            "signer_id": normalized["signer_id"],
            "roles": _string_list(list(roles), "roles"),
            "keys": [self._key_from_public_record(normalized, registered_at=registered_at)],
        }

    def _agent_from_public_record(
        self,
        record: Dict[str, str],
        *,
        owner_id: str,
        runtime: str,
        version: str,
        registered_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        normalized = self._agent_public_record(record)
        if normalized["runtime"] != runtime:
            raise TrustRegistryError("agent public record runtime does not match enrollment runtime")
        return {
            "agent_id": normalized["agent_id"],
            "owner_id": _identifier(owner_id, "agent owner_id"),
            "runtime": _identifier(runtime, "agent runtime"),
            "allowed_versions": [_version(version, "agent version")],
            "keys": [self._agent_key_from_public_record(normalized, registered_at=registered_at)],
        }
