import hashlib
import os
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional, Any, List

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
    Implements semantic-governance integrity for schemas, ontologies, action
    contracts, and shared project-trust registries by enforcing immutability.
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
        """Generates a fingerprint of all current governed contracts."""
        fingerprint: Dict[str, Any] = {}
        schema_path = self.target_dir / "schema.lds"
        ttl_path = self.target_dir / "schema.ttl"

        if schema_path.exists():
            fingerprint["schema_checksum"] = self.calculate_checksum(str(schema_path))
        if ttl_path.exists():
            fingerprint["ontology_checksum"] = self.calculate_checksum(str(ttl_path))
        actions_path = self.target_dir / "actions.yaml"
        if actions_path.exists():
            fingerprint["actions_checksum"] = self.calculate_checksum(str(actions_path))
        trust_path = self.target_dir / ".etg" / "trust.yaml"
        if trust_path.exists():
            fingerprint["trust_registry_checksum"] = self.calculate_checksum(str(trust_path))

        schema_checksums = {}
        for path in authoritative_schema_paths(self.target_dir, require_existing=False):
            relative = workspace_relative_path(self.target_dir, path)
            schema_checksums[relative] = (
                self.calculate_checksum(str(path)) if path.is_file() else None
            )
        if schema_checksums:
            fingerprint["schema_path_checksums"] = schema_checksums

        return fingerprint

    def verify_integrity(
        self,
        emit_human: bool = True,
        *,
        allow_unlocked: bool = False,
    ) -> bool:
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
        unlock_record = manifest.get("integrity_unlock")
        explicitly_unlocked = isinstance(unlock_record, dict)
        if not stored_fingerprint and explicitly_unlocked:
            # An explicit unlock is an authorized contract-change window.  The
            # old fingerprint remains available in integrity_unlock for the
            # recommission diff, but it must not be reported as tampering.
            return True
        if not stored_fingerprint and allow_unlocked:
            # A legacy/unlocked workspace may have no prior unlock marker.  An
            # accepting handoff can establish its first current lock.
            return True
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

        if (
            current_fingerprint.get("actions_checksum")
            and "actions_checksum" not in stored_fingerprint
        ):
            self.last_halt_event = HaltEvent(
                halt_code="ACTION_CONTRACT_INTEGRITY_COVERAGE_MISSING",
                message="The action contract is not covered by the current Warden lock.",
                expected_schema={"integrity_fingerprint": "actions_checksum"},
                actual_payload={"actions_checksum": current_fingerprint["actions_checksum"]},
                suggested_fix=(
                    "Review actions.yaml, then run `etg warden unlock` and "
                    "`etg broker handoff --accept-contract-change` to lock the new action contract."
                ),
                details={
                    "target_dir": str(self.target_dir),
                    "differences": self.fingerprint_differences(
                        stored_fingerprint, current_fingerprint
                    ),
                },
            )
            if emit_human:
                print("🚨 [SCHEMA_GUARD_HALT] Action contract is not covered by the Warden lock.")
            return False

        if (
            current_fingerprint.get("trust_registry_checksum")
            and "trust_registry_checksum" not in stored_fingerprint
        ):
            self.last_halt_event = HaltEvent(
                halt_code="TRUST_REGISTRY_INTEGRITY_COVERAGE_MISSING",
                message="The project trust registry is not covered by the current Warden lock.",
                expected_schema={"integrity_fingerprint": "trust_registry_checksum"},
                actual_payload={"trust_registry_checksum": current_fingerprint["trust_registry_checksum"]},
                suggested_fix=(
                    "Review .etg/trust.yaml, then run `etg warden unlock` and "
                    "`etg broker handoff --accept-contract-change` to lock the trust change."
                ),
                details={
                    "target_dir": str(self.target_dir),
                    "differences": self.fingerprint_differences(
                        stored_fingerprint, current_fingerprint
                    ),
                },
            )
            if emit_human:
                print("🚨 [SCHEMA_GUARD_HALT] Project trust registry is not covered by the Warden lock.")
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
                    details={
                        "target_dir": str(self.target_dir),
                        "differences": self.fingerprint_differences(
                            stored_fingerprint, current_fingerprint
                        ),
                    },
                )
                if emit_human:
                    print(f"🚨 [SCHEMA_GUARD_HALT] Warden Integrity Violation Detected in {key}!")
                    print(f"   Expected: {expected_hash}")
                    print(f"   Actual:   {actual_hash}")
                    print(f"   The model is attempting to alter the schema contracts of the system.")
                return False

        return True

    @staticmethod
    def fingerprint_differences(
        expected: Optional[Dict[str, Any]],
        actual: Optional[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Return stable, human-readable checksum differences."""
        expected = expected if isinstance(expected, dict) else {}
        actual = actual if isinstance(actual, dict) else {}
        differences = []
        for key in sorted(set(expected) | set(actual)):
            old = expected.get(key)
            new = actual.get(key)
            if isinstance(old, dict) or isinstance(new, dict):
                old_map = old if isinstance(old, dict) else {}
                new_map = new if isinstance(new, dict) else {}
                for path in sorted(set(old_map) | set(new_map)):
                    before = old_map.get(path)
                    after = new_map.get(path)
                    if before != after:
                        differences.append({
                            "key": key,
                            "path": path,
                            "expected_checksum": before,
                            "actual_checksum": after,
                        })
            elif old != new:
                differences.append({
                    "key": key,
                    "path": key,
                    "expected_checksum": old,
                    "actual_checksum": new,
                })
        return differences

    def integrity_state(self) -> Dict[str, Any]:
        """Describe lock state and current-vs-baseline contract checksums."""
        import yaml

        manifest = (
            yaml.safe_load(self.manifest_path.read_text()) or {}
            if self.manifest_path.exists()
            else {}
        )
        stored = manifest.get("integrity_fingerprint")
        unlock = manifest.get("integrity_unlock")
        baseline = stored
        if not isinstance(baseline, dict) and isinstance(unlock, dict):
            baseline = unlock.get("previous_fingerprint")
        try:
            current = self.generate_fingerprint()
            error = None
        except (OSError, TypeError, ValueError) as exc:
            # Status/reporting must remain machine-readable even when a
            # manifest points at an invalid schema path.
            current = {}
            error = str(exc)
        return {
            "locked": isinstance(stored, dict) and bool(stored),
            "unlocked": not (isinstance(stored, dict) and bool(stored)),
            "pending_contract_change": isinstance(unlock, dict),
            "expected_fingerprint": baseline if isinstance(baseline, dict) else {},
            "current_fingerprint": current,
            "differences": self.fingerprint_differences(baseline, current),
            "error": error,
        }

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

    def is_locked(self) -> bool:
        """Return whether the workspace currently has an active integrity lock."""
        if not self.manifest_path.is_file():
            return False
        import yaml

        manifest = yaml.safe_load(self.manifest_path.read_text()) or {}
        return isinstance(manifest.get("integrity_fingerprint"), dict) and bool(
            manifest.get("integrity_fingerprint")
        )

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
            pending = isinstance(manifest.get("integrity_unlock"), dict)
            if pending:
                print(
                    "ℹ️  [WARDEN] Domain is already unlocked; an authorized contract "
                    "change is pending. Next: `etg broker recommission "
                    "--accept-contract-change`."
                )
            else:
                print(
                    "ℹ️  [WARDEN] Domain is already unlocked. Next: run `etg broker "
                    "handoff --accept-contract-change` to establish a current lock."
                )

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
