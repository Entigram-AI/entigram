import io
import json
import os
import re
import shutil
import sqlite3
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import yaml


ENTIGRAM_START = "<!-- ENTIGRAM_START -->"
ENTIGRAM_END = "<!-- ENTIGRAM_END -->"
INSTRUCTION_FILES = (
    "AGENTS.md",
    "CLAUDE.md",
    "AGY.md",
    "GEMINI.md",
    "OLLAMA.md",
    "AGENT_INSTRUCTIONS.md",
    ".agents/AGENTS.md",
)
PAUSE_BACKUP_PATH = ".etg/lifecycle/pause-backup.json"
PAUSED_POLICY = """# Entigram Governance Paused

Entigram workspace governance is paused. No schema, ledger, hydration, or
delivery context should be loaded until governance is resumed.

Allowed commands:

- `etg usage`
- `etg resume`
- `etg eject`
"""
PAUSED_INSTRUCTION_BLOCK = """<!-- ENTIGRAM_START -->
# Entigram Governance Paused

Do not load Entigram workspace context. Run `etg resume` to restore governance,
or `etg usage` to inspect the current Entigram footprint.
<!-- ENTIGRAM_END -->"""

_MARKER_RE = re.compile(
    rf"{re.escape(ENTIGRAM_START)}.*?{re.escape(ENTIGRAM_END)}",
    flags=re.DOTALL,
)
_TEXT_SCAN_IGNORES = {
    ".git",
    ".etg",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    "build",
    "dist",
}


class WorkspaceLifecycleError(RuntimeError):
    def __init__(self, code: str, message: str, *, details: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.code = code
        self.details = details or {}

    def as_dict(self) -> Dict[str, Any]:
        return {
            "ok": False,
            "error": {
                "code": self.code,
                "message": str(self),
                "details": self.details,
            },
        }


def workspace_state(target_dir: Path) -> str:
    manifest = load_manifest(target_dir)
    lifecycle = manifest.get("lifecycle") or {}
    state = lifecycle.get("state", "active")
    return state if state in {"active", "paused"} else "active"


def is_workspace_paused(target_dir: Path) -> bool:
    try:
        return workspace_state(target_dir) == "paused"
    except WorkspaceLifecycleError:
        return False


def paused_error() -> Dict[str, Any]:
    return {
        "ok": False,
        "error": {
            "code": "WORKSPACE_PAUSED",
            "message": "Entigram workspace governance is paused.",
            "details": {
                "allowed_commands": ["etg usage", "etg resume", "etg eject"],
                "resume_command": "etg resume",
            },
        },
    }


def paused_hydration_vector(*, compact: bool = False) -> str:
    payload = {
        "ENTIGRAM_BOOT_VECTOR": {
            "workspace_state": "paused",
            "ok": False,
            "error": paused_error()["error"],
        }
    }
    encoded = (
        json.dumps(payload, separators=(",", ":"), sort_keys=True)
        if compact
        else json.dumps(payload, indent=2, sort_keys=True)
    )
    return (
        "--- ENTIGRAM HYDRATION SEQUENCE ---\n"
        f"{encoded}\n"
        "--- SEQUENCE COMPLETE ---"
    )


def load_manifest(target_dir: Path) -> Dict[str, Any]:
    path = _manifest_path(target_dir)
    if not path.is_file():
        raise WorkspaceLifecycleError(
            "NOT_ENTIGRAM_WORKSPACE",
            f"Not an Entigram workspace (missing {path}).",
        )
    try:
        manifest = yaml.safe_load(path.read_text()) or {}
    except Exception as exc:
        raise WorkspaceLifecycleError(
            "INVALID_WORKSPACE_MANIFEST",
            f"Unable to read workspace manifest: {exc}",
        ) from exc
    if not isinstance(manifest, dict):
        raise WorkspaceLifecycleError(
            "INVALID_WORKSPACE_MANIFEST",
            "Workspace manifest must be a YAML object.",
        )
    return manifest


def instruction_blocks(target_dir: Path) -> List[Dict[str, Any]]:
    root = Path(target_dir).expanduser().resolve()
    blocks = []
    for relative in INSTRUCTION_FILES:
        path = root / relative
        if not path.is_file():
            continue
        matches = _MARKER_RE.findall(path.read_text())
        if matches:
            blocks.append(
                {
                    "path": relative,
                    "blocks": matches,
                    "characters": sum(len(block) for block in matches),
                }
            )
    return blocks


def pause_workspace(target_dir: Path, *, reason: Optional[str] = None) -> Dict[str, Any]:
    root = Path(target_dir).expanduser().resolve()
    manifest_path = _manifest_path(root)
    manifest = load_manifest(root)
    if workspace_state(root) == "paused":
        return {
            "ok": True,
            "changed": False,
            "state": "paused",
            "backup": PAUSE_BACKUP_PATH,
        }

    backup_path = root / PAUSE_BACKUP_PATH
    if backup_path.exists():
        raise WorkspaceLifecycleError(
            "STALE_PAUSE_BACKUP",
            (
                f"Refusing to overwrite existing pause backup at {backup_path}. "
                "Run `etg resume` to recover the interrupted lifecycle transition."
            ),
        )

    original_manifest = manifest_path.read_text()
    policy_path = root / ".etg" / "agent_policy.md"
    policy_existed = policy_path.is_file()
    original_policy = policy_path.read_text() if policy_existed else ""
    file_backups = []
    writes = []

    for relative in INSTRUCTION_FILES:
        path = root / relative
        if not path.is_file():
            continue
        original = path.read_text()
        original_blocks = _MARKER_RE.findall(original)
        if not original_blocks:
            continue
        paused_blocks = [PAUSED_INSTRUCTION_BLOCK for _ in original_blocks]
        paused_content = _replace_marker_blocks(original, paused_blocks)
        file_backups.append(
            {
                "path": relative,
                "original_content": original,
                "original_blocks": original_blocks,
                "paused_blocks": paused_blocks,
            }
        )
        writes.append((path, paused_content))

    paused_at = _utc_now()
    backup = {
        "version": 1,
        "paused_at": paused_at,
        "reason": reason,
        "manifest": {
            "path": ".etg/entigram.yaml",
            "original_content": original_manifest,
        },
        "policy": {
            "path": ".etg/agent_policy.md",
            "existed": policy_existed,
            "original_content": original_policy,
            "paused_content": PAUSED_POLICY,
        },
        "instruction_files": file_backups,
    }

    lifecycle = {
        "state": "paused",
        "paused_at": paused_at,
    }
    if reason:
        lifecycle["reason"] = reason
    paused_manifest = dict(manifest)
    paused_manifest["lifecycle"] = lifecycle

    originals = {path: path.read_text() if path.exists() else None for path, _ in writes}
    originals[policy_path] = original_policy if policy_existed else None
    try:
        _atomic_write_text(backup_path, json.dumps(backup, indent=2, sort_keys=True) + "\n", mode=0o600)
        _atomic_write_text(policy_path, PAUSED_POLICY)
        for path, content in writes:
            _atomic_write_text(path, content)
        _atomic_write_text(
            manifest_path,
            yaml.safe_dump(paused_manifest, default_flow_style=False, sort_keys=False),
        )
    except Exception as exc:
        for path, content in originals.items():
            _restore_path(path, content)
        backup_path.unlink(missing_ok=True)
        raise WorkspaceLifecycleError(
            "PAUSE_FAILED",
            f"Unable to pause workspace: {exc}",
        ) from exc

    return {
        "ok": True,
        "changed": True,
        "state": "paused",
        "paused_at": paused_at,
        "reason": reason,
        "backup": PAUSE_BACKUP_PATH,
        "compacted_instruction_files": [item["path"] for item in file_backups],
    }


def resume_workspace(target_dir: Path, *, force: bool = False) -> Dict[str, Any]:
    root = Path(target_dir).expanduser().resolve()
    state = workspace_state(root)
    backup_path = root / PAUSE_BACKUP_PATH
    if state != "paused" and not backup_path.exists():
        return {"ok": True, "changed": False, "state": "active"}

    if not backup_path.is_file():
        raise WorkspaceLifecycleError(
            "PAUSE_BACKUP_MISSING",
            f"Cannot resume without {backup_path}.",
        )
    try:
        backup = json.loads(backup_path.read_text())
    except Exception as exc:
        raise WorkspaceLifecycleError(
            "PAUSE_BACKUP_INVALID",
            f"Cannot read pause backup: {exc}",
        ) from exc
    if backup.get("version") != 1:
        raise WorkspaceLifecycleError(
            "PAUSE_BACKUP_INVALID",
            "Unsupported pause backup version.",
        )

    conflicts = _resume_conflicts(root, backup)
    conflict_archive = None
    if conflicts and not force:
        raise WorkspaceLifecycleError(
            "PAUSED_CONTEXT_CHANGED",
            "Entigram-owned paused content changed; resume refused without --force.",
            details={"paths": conflicts},
        )
    if conflicts:
        conflict_archive = _archive_resume_conflicts(root, conflicts)

    policy = backup["policy"]
    policy_path = root / policy["path"]
    targets = [policy_path]
    targets.extend(root / item["path"] for item in backup.get("instruction_files", []))
    manifest_path = root / backup["manifest"]["path"]
    targets.append(manifest_path)
    originals = {path: path.read_text() if path.exists() else None for path in targets}

    try:
        if policy.get("existed"):
            _atomic_write_text(policy_path, policy.get("original_content", ""))
        else:
            policy_path.unlink(missing_ok=True)

        for item in backup.get("instruction_files", []):
            path = root / item["path"]
            current = path.read_text() if path.exists() else ""
            current_blocks = _MARKER_RE.findall(current)
            original_blocks = item.get("original_blocks", [])
            if current_blocks:
                restored = _replace_marker_blocks(
                    current,
                    original_blocks,
                    remove_extra=True,
                )
                if len(current_blocks) < len(original_blocks):
                    restored = _append_blocks(restored, original_blocks[len(current_blocks):])
            elif original_blocks:
                restored = (
                    item.get("original_content", "")
                    if not path.exists()
                    else _append_blocks(current, original_blocks)
                )
            else:
                restored = current
            _atomic_write_text(path, restored)

        _atomic_write_text(manifest_path, backup["manifest"]["original_content"])
        backup_path.unlink()
    except Exception as exc:
        for path, content in originals.items():
            _restore_path(path, content)
        raise WorkspaceLifecycleError(
            "RESUME_FAILED",
            f"Unable to resume workspace: {exc}",
        ) from exc

    result = {
        "ok": True,
        "changed": True,
        "state": "active",
        "recovered_interrupted_transition": state != "paused",
        "restored_instruction_files": [
            item["path"] for item in backup.get("instruction_files", [])
        ],
        "next_command": "hydrate",
    }
    if conflict_archive is not None:
        result["conflict_archive"] = conflict_archive.relative_to(root).as_posix()
    return result


def plan_eject(target_dir: Path, *, archive: Optional[Path] = None) -> Dict[str, Any]:
    root = Path(target_dir).expanduser().resolve()
    load_manifest(root)
    archive_path = _eject_archive_path(root, archive)
    blocks = instruction_blocks(root)
    return {
        "ok": True,
        "dry_run": True,
        "workspace": str(root),
        "archive": str(archive_path),
        "archive_includes": [".etg", "entigram-eject-manifest.json"],
        "instruction_files": [item["path"] for item in blocks],
        "preserved_project_artifacts": _preserved_project_artifacts(root),
        "will_remove": [".etg"],
    }


def eject_workspace(target_dir: Path, *, archive: Optional[Path] = None) -> Dict[str, Any]:
    root = Path(target_dir).expanduser().resolve()
    plan = plan_eject(root, archive=archive)
    archive_path = Path(plan["archive"])
    entigram_dir = root / ".etg"
    eject_manifest = {
        "version": 1,
        "workspace": str(root),
        "ejected_at": _utc_now(),
        "instruction_files": plan["instruction_files"],
        "preserved_project_artifacts": plan["preserved_project_artifacts"],
        "archive_warning": (
            "This archive may contain private signing keys and local governance evidence."
        ),
    }

    _create_eject_archive(entigram_dir, archive_path, eject_manifest)
    _validate_eject_archive(archive_path)
    os.chmod(archive_path, 0o600)

    instruction_originals: Dict[Path, str] = {}
    instruction_updates: Dict[Path, Optional[str]] = {}
    for relative in INSTRUCTION_FILES:
        path = root / relative
        if not path.is_file():
            continue
        original = path.read_text()
        if not _MARKER_RE.search(original):
            continue
        remaining = _MARKER_RE.sub("", original)
        instruction_originals[path] = original
        instruction_updates[path] = remaining if remaining.strip() else None

    detached_dir = root / f".etg.ejecting-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    if detached_dir.exists():
        raise WorkspaceLifecycleError(
            "EJECT_STAGING_EXISTS",
            f"Refusing to overwrite eject staging path {detached_dir}.",
        )

    try:
        for path, content in instruction_updates.items():
            if content is None:
                path.unlink()
            else:
                _atomic_write_text(path, content)
        entigram_dir.rename(detached_dir)
        try:
            from entigram.project_history import remove_project_from_history

            remove_project_from_history(str(root))
        except Exception:
            pass
        shutil.rmtree(detached_dir)
    except Exception as exc:
        if detached_dir.exists() and not entigram_dir.exists():
            detached_dir.rename(entigram_dir)
        for path, content in instruction_originals.items():
            _atomic_write_text(path, content)
        raise WorkspaceLifecycleError(
            "EJECT_FAILED",
            f"Archive was preserved, but workspace detach failed: {exc}",
            details={"archive": str(archive_path)},
        ) from exc

    return {
        "ok": True,
        "dry_run": False,
        "state": "ejected",
        "archive": str(archive_path),
        "archive_mode": "0600",
        "removed_instruction_files": [
            path.relative_to(root).as_posix()
            for path, content in instruction_updates.items()
            if content is None
        ],
        "updated_instruction_files": [
            path.relative_to(root).as_posix()
            for path, content in instruction_updates.items()
            if content is not None
        ],
        "preserved_project_artifacts": plan["preserved_project_artifacts"],
        "residual_references": _residual_entigram_references(root, archive_path),
        "reenroll_command": "etg init",
    }


def _manifest_path(target_dir: Path) -> Path:
    return Path(target_dir).expanduser().resolve() / ".etg" / "entigram.yaml"


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _atomic_write_text(path: Path, content: str, *, mode: Optional[int] = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    existing_mode = None
    if path.exists():
        existing_mode = path.stat().st_mode & 0o777
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_path, mode if mode is not None else (existing_mode or 0o644))
        os.replace(temp_path, path)
    finally:
        temp_path.unlink(missing_ok=True)


def _restore_path(path: Path, content: Optional[str]) -> None:
    if content is None:
        path.unlink(missing_ok=True)
    else:
        _atomic_write_text(path, content)


def _replace_marker_blocks(
    content: str,
    replacements: Iterable[str],
    *,
    remove_extra: bool = False,
) -> str:
    iterator = iter(replacements)

    def replace(match):
        try:
            return next(iterator)
        except StopIteration:
            return "" if remove_extra else match.group(0)

    return _MARKER_RE.sub(replace, content)


def _append_blocks(content: str, blocks: Iterable[str]) -> str:
    additions = list(blocks)
    if not additions:
        return content
    separator = "\n\n" if content.strip() else ""
    return content.rstrip() + separator + "\n\n".join(additions) + "\n"


def _resume_conflicts(root: Path, backup: Dict[str, Any]) -> List[str]:
    conflicts = []
    policy = backup["policy"]
    policy_path = root / policy["path"]
    current_policy = policy_path.read_text() if policy_path.exists() else None
    known_policy_values = {policy.get("paused_content")}
    known_policy_values.add(policy.get("original_content") if policy.get("existed") else None)
    if current_policy not in known_policy_values:
        conflicts.append(policy["path"])

    for item in backup.get("instruction_files", []):
        path = root / item["path"]
        if not path.is_file():
            conflicts.append(item["path"])
            continue
        current_blocks = tuple(_MARKER_RE.findall(path.read_text()))
        if current_blocks not in {
            tuple(item.get("paused_blocks", [])),
            tuple(item.get("original_blocks", [])),
        }:
            conflicts.append(item["path"])
    return sorted(set(conflicts))


def _archive_resume_conflicts(root: Path, paths: List[str]) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    conflict_root = root / ".etg" / "lifecycle" / "conflicts" / timestamp
    for relative in paths:
        source = root / relative
        destination = conflict_root / relative
        if source.is_file():
            _atomic_write_text(destination, source.read_text(), mode=0o600)
        else:
            _atomic_write_text(destination.with_suffix(destination.suffix + ".missing"), "", mode=0o600)
    return conflict_root


def _eject_archive_path(root: Path, archive: Optional[Path]) -> Path:
    if archive is None:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        path = root / f"entigram-eject-{timestamp}.tar.gz"
    else:
        candidate = Path(archive).expanduser()
        path = candidate.resolve() if candidate.is_absolute() else (Path.cwd() / candidate).resolve()
    try:
        path.relative_to(root / ".etg")
    except ValueError:
        pass
    else:
        raise WorkspaceLifecycleError(
            "INVALID_ARCHIVE_PATH",
            "Eject archive must be outside the workspace .etg directory.",
        )
    if path.exists():
        raise WorkspaceLifecycleError(
            "ARCHIVE_EXISTS",
            f"Refusing to overwrite existing archive {path}.",
        )
    return path


def _create_eject_archive(
    entigram_dir: Path,
    archive_path: Path,
    manifest: Dict[str, Any],
) -> None:
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(prefix="entigram-eject-") as temp_dir:
            staged_entigram = Path(temp_dir) / ".etg"
            shutil.copytree(entigram_dir, staged_entigram, symlinks=True)
            _refresh_staged_sqlite_snapshots(entigram_dir, staged_entigram)
            with tarfile.open(archive_path, mode="x:gz") as tar:
                tar.add(staged_entigram, arcname=".etg", recursive=True)
                encoded = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
                info = tarfile.TarInfo("entigram-eject-manifest.json")
                info.size = len(encoded)
                info.mode = 0o600
                info.mtime = int(datetime.now(timezone.utc).timestamp())
                tar.addfile(info, io.BytesIO(encoded))
    except Exception as exc:
        archive_path.unlink(missing_ok=True)
        raise WorkspaceLifecycleError(
            "ARCHIVE_CREATE_FAILED",
            f"Unable to create eject archive: {exc}",
        ) from exc


def _refresh_staged_sqlite_snapshots(source_root: Path, staged_root: Path) -> None:
    for source in source_root.rglob("*.db"):
        if not source.is_file() or source.is_symlink():
            continue
        destination = staged_root / source.relative_to(source_root)
        source_conn = None
        destination_conn = None
        try:
            destination.unlink(missing_ok=True)
            source_conn = sqlite3.connect(
                f"{source.resolve().as_uri()}?mode=ro",
                uri=True,
                timeout=10.0,
            )
            destination_conn = sqlite3.connect(str(destination), timeout=10.0)
            source_conn.backup(destination_conn)
        except sqlite3.DatabaseError:
            if not destination.exists():
                shutil.copy2(source, destination)
        finally:
            if destination_conn is not None:
                destination_conn.close()
            if source_conn is not None:
                source_conn.close()
    for pattern in ("*.db-wal", "*.db-shm", "*.db-journal"):
        for sidecar in staged_root.rglob(pattern):
            sidecar.unlink(missing_ok=True)


def _validate_eject_archive(archive_path: Path) -> None:
    try:
        with tarfile.open(archive_path, mode="r:gz") as tar:
            names = set(tar.getnames())
            required = {".etg/entigram.yaml", "entigram-eject-manifest.json"}
            missing = sorted(required - names)
            if missing:
                raise ValueError(f"archive is missing: {', '.join(missing)}")
            manifest_file = tar.extractfile("entigram-eject-manifest.json")
            if manifest_file is None:
                raise ValueError("eject manifest is unreadable")
            json.loads(manifest_file.read())
    except Exception as exc:
        archive_path.unlink(missing_ok=True)
        raise WorkspaceLifecycleError(
            "ARCHIVE_VALIDATION_FAILED",
            f"Eject archive validation failed: {exc}",
        ) from exc


def _preserved_project_artifacts(root: Path) -> List[str]:
    candidates = [
        "schema.lds",
        "draft_schema.lds",
        "schema.ttl",
        "ontology.ttl",
    ]
    return [relative for relative in candidates if (root / relative).exists()]


def _residual_entigram_references(root: Path, archive_path: Path) -> List[str]:
    results = []
    for path in root.rglob("*"):
        if not path.is_file() or path == archive_path:
            continue
        relative = path.relative_to(root)
        if any(part in _TEXT_SCAN_IGNORES for part in relative.parts):
            continue
        try:
            if path.stat().st_size > 1024 * 1024:
                continue
            data = path.read_bytes()
            if b"\0" in data:
                continue
            text = data.decode("utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if ".etg" in text or "Entigram" in text:
            results.append(relative.as_posix())
    return sorted(results)
