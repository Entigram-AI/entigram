"""Deterministic task bootstrap records for governed agent workspaces.

Task preparation is deliberately separate from semantic model generation.  It
captures the user-supplied task, the immutable governance inputs, and a small
read-only repository inventory before an agent is allowed to write.  Any
model-generated interpretation remains an untrusted proposal outside this
record.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

import yaml


TASK_CONTEXT_VERSION = 1
EXPECTATION_ENVELOPE_VERSION = 1
TASK_CONTEXT_RELATIVE_PATH = ".etg/lifecycle/task-context.json"
_PATH_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9_./-])(?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+\.(?:py|pyi|js|jsx|ts|tsx|java|swift|go|rs|rb|php|sql|yaml|yml|json|toml|md)(?![A-Za-z0-9_./-])")
_INVENTORY_FILES = {
    "pyproject.toml", "setup.py", "setup.cfg", "requirements.txt",
    "package.json", "package-lock.json", "pnpm-lock.yaml", "yarn.lock",
    "go.mod", "Cargo.toml", "Gemfile", "pom.xml", "build.gradle",
    "build.gradle.kts", "Package.swift",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_text(value: str) -> str:
    return _sha256_bytes(value.encode("utf-8"))


def _git(root: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip()


def _tracked_paths(root: Path) -> list[str]:
    output = _git(root, "ls-files", "-co", "--exclude-standard") or ""
    paths = {line.strip() for line in output.splitlines() if line.strip()}
    if paths:
        return sorted(paths)
    ignored = {".git", ".etg", ".venv", "venv", "node_modules", "__pycache__", "build", "dist"}
    for path in root.rglob("*"):
        if not path.is_file() or any(part in ignored for part in path.relative_to(root).parts):
            continue
        paths.add(path.relative_to(root).as_posix())
    return sorted(paths)


def _file_record(root: Path, relative: str) -> dict[str, Any]:
    path = root / relative
    try:
        data = path.read_bytes()
    except OSError:
        return {"path": relative, "missing": True}
    return {"path": relative, "size": len(data), "sha256": _sha256_bytes(data)}


def _schema_entities(root: Path) -> list[str]:
    schema = root / "schema.lds"
    if not schema.is_file():
        return []
    return sorted(set(re.findall(r"(?m)^\s*ENTITY:\s*([A-Za-z_][A-Za-z0-9_]*)", schema.read_text(errors="replace"))))


def _referenced_paths(description: str, available: Iterable[str]) -> list[str]:
    available_set = set(available)
    referenced: set[str] = set()
    for token in _PATH_TOKEN_RE.findall(description):
        token = token.strip("`'\"()[]{}:,;")
        if token in available_set:
            referenced.add(token)
    return sorted(referenced)


def _manifest_semantic_record(root: Path, relative: str = ".etg/entigram.yaml") -> dict[str, Any]:
    path = root / relative
    if not path.is_file():
        return {"path": relative, "missing": True}
    try:
        manifest = yaml.safe_load(path.read_text()) or {}
        if isinstance(manifest, dict):
            semantic = dict(manifest)
            semantic.pop("last_locked", None)
            semantic.pop("last_updated", None)
            payload = json.dumps(semantic, sort_keys=True, separators=(",", ":"))
            return {
                "path": relative,
                "size": len(payload.encode("utf-8")),
                "sha256": _sha256_text(payload),
                "semantic": True,
            }
    except Exception:
        pass
    return _file_record(root, relative)


def _governance_fingerprint(root: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    paths = [".etg/entigram.yaml", ".etg/agent_policy.md"]
    paths.extend(str(path) for path in manifest.get("schema_paths", ["schema.lds"]))
    records = [
        _manifest_semantic_record(root, path) if path == ".etg/entigram.yaml" else _file_record(root, path)
        for path in dict.fromkeys(paths)
    ]
    canonical = json.dumps(records, sort_keys=True, separators=(",", ":"))
    return {"files": records, "sha256": _sha256_text(canonical)}


def task_context_path(root: Path) -> Path:
    return root / TASK_CONTEXT_RELATIVE_PATH


def build_expectation_envelope(context: dict[str, Any]) -> dict[str, Any]:
    """Build a compact, deterministic context envelope for an LLM prompt.

    The envelope describes facts Entigram observed while preparing the task;
    it does not infer acceptance criteria or grant authorization.  In
    particular, an absent scope or file reference is surfaced as an unknown,
    not converted into a write restriction.  This keeps read-only discovery
    available while leaving writes and final submission to existing workspace
    policy and admission checks.
    """
    def _sorted_text_values(value: Any) -> list[str]:
        if not isinstance(value, (list, tuple, set)):
            return []
        return sorted({str(item) for item in value if item is not None and str(item)})

    scope = _sorted_text_values(context.get("scope", []))
    referenced_files = _sorted_text_values(context.get("referenced_files", []))
    dependency_files = sorted(
        {
            str(record.get("path"))
            for record in context.get("dependency_files", [])
            if isinstance(record, dict) and record.get("path")
        }
    )
    unknowns: list[str] = []
    if not scope:
        unknowns.append("write_scope_not_explicitly_declared")
    if not referenced_files:
        unknowns.append("no_prompt_file_references_detected")
    unknowns.append("semantic_acceptance_criteria_require_agent_or_human_interpretation")

    return {
        "kind": "entigram.task_expectation",
        "version": EXPECTATION_ENVELOPE_VERSION,
        "task_id": context.get("task_id"),
        "original_prompt": context.get("description", ""),
        "original_prompt_sha256": context.get("description_sha256")
        or _sha256_text(str(context.get("description", ""))),
        "context_sha256": context.get("context_sha256"),
        "base_commit": context.get("base_commit"),
        "scope": scope,
        "referenced_files": referenced_files,
        "dependency_files": dependency_files,
        "schema_entities": _sorted_text_values(context.get("schema_entities", [])),
        "unknowns": unknowns,
        "discovery": {
            "allowed": True,
            "mode": "read_only",
            "purpose": "Resolve unknowns before proposing a change.",
        },
        "trust_boundary": {
            "deterministic_facts": True,
            "model_interpretation": "proposal_only",
            "writes": "subject_to_workspace_policy",
            "submission": "subject_to_action_admission",
        },
        "interpretation": (
            "Use this as deterministic task context. Do not treat model-generated "
            "interpretations as schema, authorization, or proof of correctness."
        ),
    }


def load_task_context(root: Path) -> dict[str, Any] | None:
    path = task_context_path(root)
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def task_prepare_required(manifest: dict[str, Any]) -> bool:
    governance = manifest.get("governance")
    return bool(isinstance(governance, dict) and governance.get("require_task_prepare", False))


def task_context_status(root: Path, manifest: dict[str, Any] | None = None) -> dict[str, Any]:
    root = Path(root).expanduser().resolve()
    if manifest is None:
        manifest_path = root / ".etg" / "entigram.yaml"
        if not manifest_path.is_file():
            return {"status": "uninitialized", "required": False, "path": TASK_CONTEXT_RELATIVE_PATH}
        manifest = yaml.safe_load(manifest_path.read_text()) or {}
    required = task_prepare_required(manifest)
    context = load_task_context(root)
    result: dict[str, Any] = {
        "status": "missing" if context is None else "stale" if not _context_matches(root, manifest, context) else "prepared",
        "required": required,
        "path": TASK_CONTEXT_RELATIVE_PATH,
    }
    if context:
        result.update({"task_id": context.get("task_id"), "prepared_at": context.get("prepared_at")})
    return result


def _context_matches(root: Path, manifest: dict[str, Any], context: dict[str, Any]) -> bool:
    if context.get("version") != TASK_CONTEXT_VERSION or not context.get("task_id"):
        return False
    if context.get("base_commit") and _git(root, "rev-parse", "HEAD") != context.get("base_commit"):
        return False
    current_fp = _governance_fingerprint(root, manifest)
    saved_fp = context.get("governance_fingerprint") or {}
    if saved_fp.get("sha256") == current_fp.get("sha256"):
        return True

    # Fallback / cross-transition: compare non-manifest files by exact digest
    # and manifest by semantic record.
    saved_files = {
        r.get("path"): r
        for r in saved_fp.get("files", [])
        if isinstance(r, dict) and r.get("path")
    }
    current_files = {
        r.get("path"): r
        for r in current_fp.get("files", [])
        if isinstance(r, dict) and r.get("path")
    }
    if not saved_files or set(saved_files.keys()) != set(current_files.keys()):
        return False
    for path, cur_r in current_files.items():
        sav_r = saved_files[path]
        if path == ".etg/entigram.yaml":
            # If saved was also semantic, they would have matched above. If saved was raw,
            # we allow it if current semantic record exists.
            continue
        if cur_r.get("sha256") != sav_r.get("sha256"):
            return False
    return True


def prepare_task(
    root: Path,
    *,
    task_id: str,
    description: str,
    scope: list[str] | None = None,
    agent: str | None = None,
    model: str | None = None,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    root = Path(root).expanduser().resolve()
    manifest_path = root / ".etg" / "entigram.yaml"
    if not manifest_path.is_file():
        raise ValueError("Entigram workspace is not initialized; run `etg init` first.")
    if not task_id.strip():
        raise ValueError("task_id must not be empty")
    if not description.strip():
        raise ValueError("task description must not be empty")
    def report(message: str) -> None:
        if progress is not None:
            progress(message)

    report("Checking workspace governance")
    manifest = yaml.safe_load(manifest_path.read_text()) or {}
    report("Collecting Git inventory")
    paths = _tracked_paths(root)
    governance = _governance_fingerprint(root, manifest)
    # Hydration includes delivery-status artifact scans, which can be very
    # expensive in large workspaces. Task preparation must be bounded; callers
    # can run `hydrate` explicitly when they need the full workspace vector.
    hydration = {
        "status": "deferred",
        "reason": "Run `hydrate` explicitly to generate the full workspace vector.",
    }
    report("Recording deterministic task context")
    scope_values = sorted({str(value).strip().lstrip("./") for value in (scope or []) if str(value).strip()})
    dependency_files = [path for path in paths if path in _INVENTORY_FILES or Path(path).name in _INVENTORY_FILES]
    context: dict[str, Any] = {
        "version": TASK_CONTEXT_VERSION,
        "task_id": task_id.strip(),
        "description": description.strip(),
        "description_sha256": _sha256_text(description.strip()),
        "prepared_at": _utc_now(),
        "agent": agent or os.environ.get("ENTIGRAM_AGENT_RUNTIME"),
        "model": model,
        "base_commit": _git(root, "rev-parse", "HEAD"),
        "git_status": _git(root, "status", "--porcelain=v1") or "",
        "governance_fingerprint": governance,
        "file_count": len(paths),
        "files": paths,
        "referenced_files": _referenced_paths(description, paths),
        "scope": scope_values,
        "dependency_files": [_file_record(root, path) for path in dependency_files],
        "schema_entities": _schema_entities(root),
        "hydration": hydration,
        "interpretation": "Deterministic bootstrap inventory; no model-generated schema is trusted.",
    }
    canonical = json.dumps(context, sort_keys=True, separators=(",", ":"))
    context["context_sha256"] = _sha256_text(canonical)
    path = task_context_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(context, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)
    return {
        "ok": True,
        "task": context,
        "expectation": build_expectation_envelope(context),
        "status": task_context_status(root, manifest),
    }


def task_context_is_ready(root: Path, manifest: dict[str, Any] | None = None) -> bool:
    status = task_context_status(root, manifest)
    return status.get("status") == "prepared"
