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
from typing import Any, Iterable

import yaml


TASK_CONTEXT_VERSION = 1
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


def _governance_fingerprint(root: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    paths = [".etg/entigram.yaml", ".etg/agent_policy.md"]
    paths.extend(str(path) for path in manifest.get("schema_paths", ["schema.lds"]))
    records = [_file_record(root, path) for path in dict.fromkeys(paths)]
    canonical = json.dumps(records, sort_keys=True, separators=(",", ":"))
    return {"files": records, "sha256": _sha256_text(canonical)}


def task_context_path(root: Path) -> Path:
    return root / TASK_CONTEXT_RELATIVE_PATH


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
    return context.get("governance_fingerprint", {}).get("sha256") == _governance_fingerprint(root, manifest).get("sha256")


def prepare_task(
    root: Path,
    *,
    task_id: str,
    description: str,
    scope: list[str] | None = None,
    agent: str | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    root = Path(root).expanduser().resolve()
    manifest_path = root / ".etg" / "entigram.yaml"
    if not manifest_path.is_file():
        raise ValueError("Entigram workspace is not initialized; run `etg init` first.")
    if not task_id.strip():
        raise ValueError("task_id must not be empty")
    if not description.strip():
        raise ValueError("task description must not be empty")
    manifest = yaml.safe_load(manifest_path.read_text()) or {}
    paths = _tracked_paths(root)
    governance = _governance_fingerprint(root, manifest)
    hydration = None
    try:
        # Import lazily to keep the task module independent of the CLI parser.
        from entigram.cli_runner.etg_cli import get_hydration_vector
        raw = get_hydration_vector(root, compact=True)
        start = raw.find("\n") + 1
        end = raw.rfind("\n--- SEQUENCE COMPLETE ---")
        hydration = json.loads(raw[start:end]) if end > start else None
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        hydration = None
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
    return {"ok": True, "task": context, "status": task_context_status(root, manifest)}


def task_context_is_ready(root: Path, manifest: dict[str, Any] | None = None) -> bool:
    status = task_context_status(root, manifest)
    return status.get("status") == "prepared"
