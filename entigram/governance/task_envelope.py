"""Structured, reviewable task envelopes for agent intent alignment."""

from __future__ import annotations

import json
import uuid
import datetime
import os
import re
import tempfile
from pathlib import Path
from typing import Dict, Any, List, Optional, Callable
import hashlib

ENVELOPES_DIR = ".etg/task_envelopes"
_ENVELOPE_ID_RE = re.compile(r"^task-envelope-[a-zA-Z0-9-]+$")

class TaskEnvelopeError(ValueError):
    """Raised for invalid task envelope definitions."""
    pass

def _get_envelopes_dir(target_dir: str | Path, ensure_exists: bool = False) -> Path:
    path = Path(target_dir).expanduser().resolve() / ENVELOPES_DIR
    if ensure_exists:
        path.mkdir(parents=True, exist_ok=True)
    return path

def _validate_envelope_id(envelope_id: str) -> None:
    if not isinstance(envelope_id, str) or not _ENVELOPE_ID_RE.match(envelope_id):
        raise TaskEnvelopeError("Invalid envelope ID format.")

def _atomic_write(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(dir=str(path.parent), prefix="tmp-env-")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2, sort_keys=True)
            f.write("\n")
        os.replace(temp_path, str(path))
    except Exception:
        if os.path.exists(temp_path):
            os.remove(temp_path)
        raise

def _require_string_list(value: Any, name: str) -> List[str]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise TaskEnvelopeError(f"{name} must be a list of strings.")
    return list(value)

def create_envelope(
    target_dir: str | Path,
    intent: str,
    proposed_entities: List[str],
    invariants: List[str],
    affected_paths: List[str],
    validation_commands: List[str],
    uncertainty_unknowns: List[str],
    agent_id: str = "agent",
    provenance: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    if not isinstance(intent, str) or not intent.strip():
        raise TaskEnvelopeError("intent must be a non-empty string.")

    envelope_id = f"task-envelope-{uuid.uuid4()}"
    envelope = {
        "envelope_id": envelope_id,
        "agent_id": str(agent_id),
        "intent": intent.strip(),
        "proposed_entities_and_relationships": _require_string_list(proposed_entities, "proposed_entities"),
        "invariants": _require_string_list(invariants, "invariants"),
        "affected_paths": _require_string_list(affected_paths, "affected_paths"),
        "validation_commands": _require_string_list(validation_commands, "validation_commands"),
        "uncertainty_unknowns": _require_string_list(uncertainty_unknowns, "uncertainty_unknowns"),
        "status": "proposed",
        "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }

    if provenance is not None:
        envelope["provenance"] = provenance

    path = _get_envelopes_dir(target_dir, ensure_exists=True) / f"{envelope_id}.json"
    _atomic_write(path, envelope)

    return envelope

def _read_envelopes(target_dir: str | Path, expected_status: str) -> List[Dict[str, Any]]:
    envelopes_dir = _get_envelopes_dir(target_dir, ensure_exists=False)
    if not envelopes_dir.is_dir():
        return []

    results = []
    for path in envelopes_dir.glob("task-envelope-*.json"):
        try:
            with open(path, "r") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                continue
            if data.get("status") == expected_status:
                results.append(data)
        except (json.JSONDecodeError, OSError):
            if expected_status == "error":
                results.append({
                    "envelope_id": path.stem,
                    "status": "error",
                    "error": "Malformed or unreadable envelope file.",
                    "created_at": "1970-01-01T00:00:00Z"
                })

    return sorted(results, key=lambda x: x.get("created_at", ""))

def get_pending_envelopes(target_dir: str | Path) -> List[Dict[str, Any]]:
    return _read_envelopes(target_dir, "proposed")

def get_accepted_envelopes(target_dir: str | Path) -> List[Dict[str, Any]]:
    return _read_envelopes(target_dir, "accepted")

def get_error_envelopes(target_dir: str | Path) -> List[Dict[str, Any]]:
    return _read_envelopes(target_dir, "error")

def accept_envelope(target_dir: str | Path, envelope_id: str, approver_id: str = "operator") -> Dict[str, Any]:
    _validate_envelope_id(envelope_id)
    envelopes_dir = _get_envelopes_dir(target_dir, ensure_exists=True)
    path = envelopes_dir / f"{envelope_id}.json"

    if not path.is_file():
        raise TaskEnvelopeError(f"Task envelope {envelope_id} not found.")

    try:
        with open(path, "r") as f:
            envelope = json.load(f)
    except json.JSONDecodeError:
        raise TaskEnvelopeError(f"Task envelope {envelope_id} is malformed.")

    if envelope.get("status") != "proposed":
        raise TaskEnvelopeError(f"Task envelope {envelope_id} is not in proposed state.")

    envelope["status"] = "accepted"
    envelope["accepted_by"] = str(approver_id)
    envelope["accepted_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()

    _atomic_write(path, envelope)
    return envelope

def authorize_execution(target_dir: str | Path, envelope_id: str) -> Dict[str, Any]:
    """Check if an envelope is accepted and valid for authorizing execution."""
    _validate_envelope_id(envelope_id)
    envelopes_dir = _get_envelopes_dir(target_dir, ensure_exists=False)
    path = envelopes_dir / f"{envelope_id}.json"

    if not path.is_file():
        return {"authorized": False, "reason": f"Envelope {envelope_id} not found."}

    try:
        with open(path, "r") as f:
            envelope = json.load(f)
    except json.JSONDecodeError:
        return {"authorized": False, "reason": f"Envelope {envelope_id} is malformed."}

    if envelope.get("status") == "accepted":
        return {"authorized": True, "envelope": envelope}

    return {"authorized": False, "reason": f"Envelope {envelope_id} is in '{envelope.get('status', 'unknown')}' state, not 'accepted'."}

class ProposerNotRegisteredError(TaskEnvelopeError):
    """Raised when an attempt is made to propose an envelope without a registered adapter."""
    pass

class MalformedProposalError(TaskEnvelopeError):
    """Raised when the proposer adapter returns a malformed proposal."""
    pass

_PROPOSER_ADAPTER: Optional[Callable[[str], Dict[str, Any]]] = None
_PROPOSER_ADAPTER_INFO: Dict[str, str] = {}

def register_proposer_adapter(adapter: Callable[[str], Dict[str, Any]], name: str, version: str = "task-envelope-proposer/v1") -> None:
    """Registers an adapter for automatic task proposals."""
    global _PROPOSER_ADAPTER, _PROPOSER_ADAPTER_INFO
    _PROPOSER_ADAPTER = adapter
    _PROPOSER_ADAPTER_INFO = {"name": name, "protocol": version}

def propose_task_envelope(target_dir: str | Path, intent: str, policy: str = "review-required", agent_id: str = "agent") -> Dict[str, Any]:
    """Uses the registered adapter to automatically propose a task envelope and process it based on policy."""
    if _PROPOSER_ADAPTER is None:
        raise ProposerNotRegisteredError("No proposer adapter registered.")

    try:
        proposal = _PROPOSER_ADAPTER(intent)
    except Exception as e:
        raise MalformedProposalError(f"Proposer adapter failed: {e}")

    if not isinstance(proposal, dict):
        raise MalformedProposalError("Proposal must be a dictionary.")

    required_keys = ["proposed_entities", "invariants", "affected_paths", "validation_commands", "uncertainty_unknowns"]
    for k in required_keys:
        if k not in proposal or not isinstance(proposal[k], list):
            raise MalformedProposalError(f"Proposal missing or malformed required field: {k}")

    # Calculate provenance fingerprint
    hasher = hashlib.sha256()
    hasher.update(json.dumps(proposal, sort_keys=True).encode("utf-8"))
    hasher.update(json.dumps(_PROPOSER_ADAPTER_INFO, sort_keys=True).encode("utf-8"))
    digest = hasher.hexdigest()

    provenance = {
        "adapter_name": _PROPOSER_ADAPTER_INFO.get("name", "unknown"),
        "protocol": _PROPOSER_ADAPTER_INFO.get("protocol", "unknown"),
        "digest": digest
    }

    envelope = create_envelope(
        target_dir=target_dir,
        intent=intent,
        proposed_entities=proposal["proposed_entities"],
        invariants=proposal["invariants"],
        affected_paths=proposal["affected_paths"],
        validation_commands=proposal["validation_commands"],
        uncertainty_unknowns=proposal["uncertainty_unknowns"],
        agent_id=agent_id,
        provenance=provenance
    )

    if policy == "auto-accept":
        # Accept it immediately (accept_envelope will handle saving the status change)
        envelope = accept_envelope(target_dir, envelope["envelope_id"], approver_id=f"auto-policy:{provenance['adapter_name']}")

    return envelope
