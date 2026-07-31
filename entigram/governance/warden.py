import hashlib
import os
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional, Any

from ..workspace_contract import (
    authoritative_schema_paths,
    workspace_relative_path,
)


@dataclass
class HaltEvent:
    """Machine-readable schema gate halt emitted by the Warden."""

    halt_code: str
    message: str
    expected_schema: Dict[str, Any]
    actual_payload: Dict[str, Any]
    suggested_fix: str
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "halt_code": self.halt_code,
            "message": self.message,
            "expected_schema": self.expected_schema,
            "actual_payload": self.actual_payload,
            "suggested_fix": self.suggested_fix,
            "details": self.details,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=True)

class Warden:
    """
    Implements 'Semantic Governance' Integrity: Decouples schema contracts (Schema/Ontology) 
    from agent execution by enforcing cryptographic immutability.
    """
    def __init__(self, target_dir: str = "."):
        self.target_dir = Path(target_dir).expanduser().resolve()
        self.manifest_path = self.target_dir / ".etg" / "entigram.yaml"
        self.last_halt_event: Optional[HaltEvent] = None

    def calculate_checksum(self, file_path: str) -> str:
        """Calculates the SHA-256 hash of a file."""
        sha256_hash = hashlib.sha256()
        with open(file_path, "rb") as f:
            for byte_block in iter(lambda: f.read(4096), b""):
                sha256_hash.update(byte_block)
        return sha256_hash.hexdigest()

    def generate_fingerprint(self) -> Dict[str, Any]:
        """Generates a fingerprint of the current governed domain (Schema and Ontology)."""
        fingerprint: Dict[str, Any] = {}
        schema_path = self.target_dir / "schema.lds"
        ttl_path = self.target_dir / "schema.ttl"

        if schema_path.exists():
            fingerprint["schema_checksum"] = self.calculate_checksum(str(schema_path))
        if ttl_path.exists():
            fingerprint["ontology_checksum"] = self.calculate_checksum(str(ttl_path))

        schema_checksums = {}
        for path in authoritative_schema_paths(self.target_dir, require_existing=False):
            relative = workspace_relative_path(self.target_dir, path)
            schema_checksums[relative] = (
                self.calculate_checksum(str(path)) if path.is_file() else None
            )
        if schema_checksums:
            fingerprint["schema_path_checksums"] = schema_checksums

        return fingerprint

    def verify_integrity(self, emit_human: bool = True) -> bool:
        """
        Validates the current files against the hashes stored in the manifest.
        Triggers SCHEMA_GUARD_HALT if a mismatch is detected.
        """
        import yaml
        self.last_halt_event = None
        if not self.manifest_path.exists():
            return True # Nothing to verify yet

        with open(self.manifest_path, "r") as f:
            manifest = yaml.safe_load(f) or {}

        stored_fingerprint = manifest.get("integrity_fingerprint", {})
        if not isinstance(stored_fingerprint, dict):
            self.last_halt_event = HaltEvent(
                halt_code="SCHEMA_MANIFEST_INVALID",
                message="Workspace integrity fingerprint is invalid.",
                expected_schema={"integrity_fingerprint": "object"},
                actual_payload={"integrity_fingerprint": stored_fingerprint},
                suggested_fix="Restore the manifest, then run `etg warden lock`.",
                details={"target_dir": str(self.target_dir)},
            )
            if emit_human:
                print("🚨 [SCHEMA_GUARD_HALT] Invalid integrity_fingerprint.")
            return False
        try:
            current_fingerprint = self.generate_fingerprint()
        except Exception as exc:
            self.last_halt_event = HaltEvent(
                halt_code="SCHEMA_MANIFEST_INVALID",
                message="Workspace schema paths are invalid.",
                expected_schema={"manifest_path": str(self.manifest_path)},
                actual_payload={"error": str(exc)},
                suggested_fix="Correct schema_paths, then run `etg warden lock`.",
                details={"target_dir": str(self.target_dir)},
            )
            if emit_human:
                print(f"🚨 [SCHEMA_GUARD_HALT] Invalid schema_paths: {exc}")
            return False

        schema_checksums = current_fingerprint.get("schema_path_checksums", {})
        extended_schema_paths = set(schema_checksums) - {"schema.lds"}
        if extended_schema_paths and "schema_path_checksums" not in stored_fingerprint:
            self.last_halt_event = HaltEvent(
                halt_code="SCHEMA_INTEGRITY_COVERAGE_MISSING",
                message="Authoritative schema paths are not covered by the Warden lock.",
                expected_schema={"schema_paths": sorted(schema_checksums)},
                actual_payload={"integrity_fingerprint": stored_fingerprint},
                suggested_fix="Review the authoritative schemas, then run `etg warden lock`.",
                details={"target_dir": str(self.target_dir)},
            )
            if emit_human:
                print("🚨 [SCHEMA_GUARD_HALT] Authoritative package schemas are not locked.")
            return False

        if not stored_fingerprint:
            return True # Root-only legacy workspaces may not be protected yet.

        for key, expected_hash in stored_fingerprint.items():
            actual_hash = current_fingerprint.get(key)
            if actual_hash != expected_hash:
                self.last_halt_event = HaltEvent(
                    halt_code="SCHEMA_INTEGRITY_VIOLATION",
                    message=f"Warden integrity violation detected in {key}.",
                    expected_schema={
                        "fingerprint_key": key,
                        "expected_checksum": expected_hash,
                    },
                    actual_payload={
                        "fingerprint_key": key,
                        "actual_checksum": actual_hash,
                    },
                    suggested_fix=(
                        "Restore the governed schema/ontology files, or run "
                        "`etg warden lock` only after an authorized contract change."
                    ),
                    details={"target_dir": str(self.target_dir)},
                )
                if emit_human:
                    print(f"🚨 [SCHEMA_GUARD_HALT] Warden Integrity Violation Detected in {key}!")
                    print(f"   Expected: {expected_hash}")
                    print(f"   Actual:   {actual_hash}")
                    print(f"   The model is attempting to alter the schema contracts of the system.")
                return False

        return True

    def lock_fingerprint(
        self,
        *,
        require_existing_match: bool = False,
        expected_fingerprint: Optional[Dict[str, Any]] = None,
    ):
        """Persist checksums without accepting drift observed during validation."""
        import yaml
        from datetime import datetime
        
        fingerprint = self.generate_fingerprint()
        if not self.manifest_path.exists():
            return

        with open(self.manifest_path, "r") as f:
            manifest = yaml.safe_load(f) or {}

        stored_fingerprint = manifest.get("integrity_fingerprint")
        if expected_fingerprint is not None and fingerprint != expected_fingerprint:
            raise RuntimeError(
                "Warden detected a contract change while handoff validations ran"
            )
        if (
            require_existing_match
            and stored_fingerprint
            and stored_fingerprint != fingerprint
        ):
            raise RuntimeError(
                "Warden refused to replace a mismatched integrity fingerprint"
            )

        manifest["integrity_fingerprint"] = fingerprint
        manifest.pop("integrity_unlock", None)
        manifest["last_locked"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        with open(self.manifest_path, "w") as f:
            yaml.dump(manifest, f, default_flow_style=False)
        
        print(f"🔒 [WARDEN] Schema contracts locked via checksum integrity.")
        return True

    def has_pending_contract_change(self) -> bool:
        """Return whether an explicit unlock has not yet been re-locked."""
        if not self.manifest_path.is_file():
            return False
        import yaml

        manifest = yaml.safe_load(self.manifest_path.read_text()) or {}
        return isinstance(manifest.get("integrity_unlock"), dict)

    def unlock(self):
        """Removes the integrity fingerprint from the manifest, allowing modifications."""
        import yaml
        if not self.manifest_path.exists():
            return

        with open(self.manifest_path, "r") as f:
            manifest = yaml.safe_load(f) or {}

        if "integrity_fingerprint" in manifest:
            from datetime import datetime, timezone

            manifest["integrity_unlock"] = {
                "previous_fingerprint": manifest["integrity_fingerprint"],
                "unlocked_at": datetime.now(timezone.utc).isoformat(),
            }
            del manifest["integrity_fingerprint"]
            if "last_locked" in manifest:
                del manifest["last_locked"]

            with open(self.manifest_path, "w") as f:
                yaml.dump(manifest, f, default_flow_style=False)
            
            print(f"🔓 [WARDEN] Schema contracts UNLOCKED. The domain can now be modified.")
        else:
            print(f"ℹ️  [WARDEN] Domain was not locked.")

    def validate_payload(self, entity_name: str, payload: Dict[str, Any], emit_human: bool = True) -> bool:
        """
        Deterministially validates an agent-proposed payload against the locked Schema.
        Prevents agents from inventing new attributes or drifting from strict types.
        """
        from ..schema_compiler.parser import SchemaParser
        self.last_halt_event = None

        try:
            schema_paths = authoritative_schema_paths(self.target_dir)
            entities = {}
            for schema_path in schema_paths:
                parsed, _ = SchemaParser(schema_path.read_text()).parse()
                entities.update(parsed)
        except Exception as exc:
            self.last_halt_event = HaltEvent(
                halt_code="SCHEMA_CONTRACT_INVALID",
                message="Authoritative schema contracts could not be loaded.",
                expected_schema={"manifest_path": str(self.manifest_path)},
                actual_payload={"error": str(exc)},
                suggested_fix="Correct schema_paths and restore valid LDS contracts.",
                details={"target_dir": str(self.target_dir)},
            )
            if emit_human:
                print(f"🚨 [SCHEMA_GUARD_HALT] Invalid schema contract: {exc}")
            return False

        if not entities:
            return True # No schema contracts to enforce

        if entity_name not in entities:
            self.last_halt_event = HaltEvent(
                halt_code="UNKNOWN_ENTITY",
                message=f"Agent proposed unknown entity '{entity_name}'.",
                expected_schema={"entities": sorted(entities.keys())},
                actual_payload={"entity": entity_name, "payload": payload},
                suggested_fix="Use an entity declared in schema.lds before proposing state.",
                details={"target_dir": str(self.target_dir)},
            )
            if emit_human:
                print(f"🚨 [SCHEMA_GUARD_HALT] Semantic Drift: Agent proposed unknown entity '{entity_name}'.")
            return False

        allowed_entity = entities[entity_name]
        allowed_attributes = [attr['name'] for attr in allowed_entity.attributes]

        unknown_attributes = []
        for attr_name in payload.keys():
            if attr_name not in allowed_attributes:
                unknown_attributes.append(attr_name)

        if unknown_attributes:
            self.last_halt_event = HaltEvent(
                halt_code="UNKNOWN_ATTRIBUTE",
                message=(
                    f"Agent attempted to invent attribute(s) "
                    f"{', '.join(sorted(unknown_attributes))} for '{entity_name}'."
                ),
                expected_schema={
                    "entity": entity_name,
                    "allowed_attributes": allowed_attributes,
                },
                actual_payload=payload,
                suggested_fix=(
                    "Remove the unknown attribute(s), or add them to schema.lds "
                    "through an authorized schema change before retrying."
                ),
                details={
                    "entity": entity_name,
                    "unknown_attributes": sorted(unknown_attributes),
                    "target_dir": str(self.target_dir),
                },
            )
            if emit_human:
                first_unknown = sorted(unknown_attributes)[0]
                print(f"🚨 [SCHEMA_GUARD_HALT] Unauthorized Mutation: Agent attempted to invent attribute '{first_unknown}' for '{entity_name}'.")
            return False

        return True

    def halt_event_payload(self, ok: bool = False) -> Dict[str, Any]:
        return {
            "ok": ok,
            "halt_event": self.last_halt_event.to_dict() if self.last_halt_event else None,
        }
