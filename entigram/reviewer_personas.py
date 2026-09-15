"""Governed, workspace-local reviewer persona configuration."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import re
import yaml


_RUNTIMES = {"codex", "antigravity", "claude"}
_PERSONA_ID = re.compile(r"^[a-z][a-z0-9_-]{2,79}$")


def reviewer_questions(*, name: str = "", runtime: str = "", context: str = "") -> List[str]:
    """Return only the owner decisions still needed to create a reviewer."""
    questions = []
    if not name.strip():
        questions.append("What should this reviewer be called?")
    if runtime.strip().casefold() not in _RUNTIMES:
        questions.append("Which installed runtime should power it: Codex, Antigravity, or Claude?")
    if not context.strip():
        questions.append("What should this reviewer focus on, and what should it never do?")
    return questions


def create_reviewer_persona(
    workspace: Path,
    *,
    persona_id: str,
    name: str,
    runtime: str,
    context: str,
    requested_by: str,
    approved_by: str,
) -> Dict[str, Any]:
    """Record a read-only reviewer after a locally confirmed owner action.

    Persona configuration is workspace-local policy, not an identity or
    authorization system.  The confirmation is retained as audit context; an
    enforced action still requires the project's trust/action-admission layer.
    """
    normalized_id = persona_id.strip().casefold()
    normalized_runtime = runtime.strip().casefold()
    questions = reviewer_questions(name=name, runtime=runtime, context=context)
    if not _PERSONA_ID.fullmatch(normalized_id):
        return {"ok": False, "reason": "INVALID_REVIEWER_ID"}
    if questions:
        return {"ok": False, "reason": "REVIEWER_DETAILS_NEEDED", "questions": questions}
    if not approved_by.startswith("user:"):
        return {
            "ok": False,
            "reason": "LOCAL_OWNER_CONFIRMATION_REQUIRED",
            "questions": [f"Create the read-only reviewer '{name.strip()}' using {normalized_runtime.title()}?"],
        }
    path = workspace / ".etg" / "agent-personas.yaml"
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        document = {}
    if not isinstance(document, dict):
        document = {}
    personas = document.setdefault("personas", {})
    if not isinstance(personas, dict):
        return {"ok": False, "reason": "INVALID_PERSONA_CONFIGURATION"}
    if normalized_id in personas:
        return {"ok": False, "reason": "REVIEWER_ALREADY_EXISTS"}
    profile = {
        "name": name.strip()[:120],
        "runtime": normalized_runtime,
        "task_types": ["read_only"],
        "context": context.strip()[:4000],
        "requested_by": requested_by.strip()[:120],
        "locally_confirmed_by": approved_by,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    personas[normalized_id] = profile
    document["version"] = 1
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(yaml.safe_dump(document, sort_keys=True), encoding="utf-8")
    temporary.replace(path)
    return {"ok": True, "persona_id": normalized_id, "persona": profile}
