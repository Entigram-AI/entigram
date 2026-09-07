"""Structured, reviewable task envelopes for agent intent alignment."""

from __future__ import annotations

import json
import uuid
import datetime
import os
import re
import tempfile
from pathlib import Path
from typing import Dict, Any, List, Optional

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
