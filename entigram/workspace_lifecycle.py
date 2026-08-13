import io
import hashlib
import json
import os
import re
import shlex
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
PAUSE_BACKUP_VERSION = 2
DEFAULT_PAUSED_CHANGE_BUDGET_FILES = 5
ACTIVE_CHANGE_BASELINE_PATH = ".etg/lifecycle/check-in-baseline.json"
ACTIVE_CHANGE_BASELINE_VERSION = 1
DEFAULT_ACTIVE_CHANGE_BUDGET_FILES = 5
PAUSE_GIT_HOOK_START = "# >>> entigram paused change budget >>>"
PAUSE_GIT_HOOK_END = "# <<< entigram paused change budget <<<"
GIT_CHECKIN_GUARD_START = "# >>> entigram lifecycle check-in >>>"
GIT_CHECKIN_GUARD_END = "# <<< entigram lifecycle check-in <<<"

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


def _paused_policy(max_changed_files: int) -> str:
    return f"""# Entigram Governance Paused

Entigram workspace governance is paused. No schema, ledger, hydration, or
delivery context should be loaded until governance is resumed.

The paused change budget is {max_changed_files} changed file(s). Before making
an additional change after that budget is exhausted, run `etg resume`, then
`hydrate`, and continue through the normal Entigram gates. Run
`etg pause-status` to inspect the current budget. In a local Git repository,
Entigram installs a temporary pre-commit guard that rejects a commit exceeding
this budget.

Allowed commands:

- `etg usage`
- `etg pause-status`
- `etg resume`
- `etg eject`
"""


def _paused_instruction_block(max_changed_files: int) -> str:
    return f"""<!-- ENTIGRAM_START -->
# Entigram Governance Paused

Do not load Entigram workspace context. A paused workspace may change at most
{max_changed_files} file(s) from its pause baseline. Run `etg pause-status`
before starting more work; after the budget is exhausted, run `etg resume` and
`hydrate` before making another change. A local Git pre-commit guard rejects a
commit that exceeds this limit.
<!-- ENTIGRAM_END -->"""


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
                "allowed_commands": [
                    "etg usage",
                    "etg pause-status",
                    "etg resume",
                    "etg eject",
                ],
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


def paused_change_status(target_dir: Path) -> Dict[str, Any]:
    """Report drift against the baseline captured when governance was paused."""
    root = Path(target_dir).expanduser().resolve()
    if workspace_state(root) != "paused":
        raise WorkspaceLifecycleError(
            "WORKSPACE_NOT_PAUSED",
            "Paused change status is available only while workspace governance is paused.",
        )

    backup = _load_pause_backup(root)
    change_budget = backup.get("change_budget")
    if not isinstance(change_budget, dict):
        raise WorkspaceLifecycleError(
            "PAUSE_CHANGE_BUDGET_UNAVAILABLE",
            "This paused workspace has no drift baseline. Resume governance before continuing work.",
            details={"resume_command": "etg resume"},
        )

    baseline = change_budget.get("baseline")
    max_changed_files = change_budget.get("max_changed_files")
    if (
        not isinstance(baseline, dict)
        or isinstance(max_changed_files, bool)
        or not isinstance(max_changed_files, int)
        or max_changed_files < 1
    ):
        raise WorkspaceLifecycleError(
            "PAUSE_CHANGE_BUDGET_INVALID",
            "Paused change-budget metadata is invalid. Resume governance before continuing work.",
            details={"resume_command": "etg resume"},
        )

    current = _workspace_snapshot(root)
    created = sorted(set(current) - set(baseline))
    deleted = sorted(set(baseline) - set(current))
    modified = sorted(
        path
        for path in set(current).intersection(baseline)
        if current[path] != baseline[path]
    )
    changed_files = len(created) + len(deleted) + len(modified)
    exhausted = changed_files >= max_changed_files
    over_budget = changed_files > max_changed_files

    return {
        "ok": True,
        "state": "paused",
        "status": "check_in_required" if exhausted else "within_budget",
        "budget": {
            "max_changed_files": max_changed_files,
            "changed_files": changed_files,
            "remaining_files": max(0, max_changed_files - changed_files),
            "exhausted": exhausted,
            "over_budget": over_budget,
        },
        "changes": {
            "created": created,
            "modified": modified,
            "deleted": deleted,
        },
        "next_action": (
            "Run `etg resume`, then `hydrate`, before making another change."
            if exhausted
            else "Run `etg pause-status` again before starting the next material change."
        ),
    }


def establish_active_change_baseline(
    target_dir: Path,
    *,
    reason: str,
    snapshot_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Record the directory state accepted at workspace initialization or handoff.

    The active check-in guard intentionally snapshots filesystem metadata rather
    than agent tool calls. That makes edits made by another agent, editor, or
    process visible at the next admission check without persisting file content.
    """
    root = Path(target_dir).expanduser().resolve()
    if workspace_state(root) != "active":
        raise WorkspaceLifecycleError(
            "WORKSPACE_NOT_ACTIVE",
            "An active change baseline can be recorded only after governance is active.",
        )

    max_changed_files = _active_change_budget_limit(root)
    baseline = _workspace_metadata_snapshot(root)
    record = {
        "version": ACTIVE_CHANGE_BASELINE_VERSION,
        "recorded_at": _utc_now(),
        "reason": reason,
        "snapshot_id": snapshot_id,
        "max_changed_files": max_changed_files,
        "baseline": baseline,
    }
    _atomic_write_text(
        root / ACTIVE_CHANGE_BASELINE_PATH,
        json.dumps(record, indent=2, sort_keys=True) + "\n",
        mode=0o600,
    )
    return {
        "ok": True,
        "state": "active",
        "max_changed_files": max_changed_files,
        "baseline_file": ACTIVE_CHANGE_BASELINE_PATH,
        "recorded_at": record["recorded_at"],
        "reason": reason,
        "snapshot_id": snapshot_id,
    }


def ensure_active_change_baseline(target_dir: Path) -> Dict[str, Any]:
    """Create the one-time compatibility baseline required by older workspaces."""
    root = Path(target_dir).expanduser().resolve()
    path = root / ACTIVE_CHANGE_BASELINE_PATH
    if path.is_file():
        return _load_active_change_baseline(root)
    establish_active_change_baseline(root, reason="first_entigram_hook")
    return _load_active_change_baseline(root)


def active_change_status(target_dir: Path) -> Dict[str, Any]:
    """Report governed-file drift since the last accepted active-workspace check-in."""
    root = Path(target_dir).expanduser().resolve()
    if workspace_state(root) != "active":
        raise WorkspaceLifecycleError(
            "WORKSPACE_NOT_ACTIVE",
            "Active change status is unavailable while workspace governance is paused.",
        )

    record = ensure_active_change_baseline(root)
    baseline = record["baseline"]
    current = _workspace_metadata_snapshot(root)
    created = sorted(set(current) - set(baseline))
    deleted = sorted(set(baseline) - set(current))
    modified = sorted(
        path
        for path in set(current).intersection(baseline)
        if current[path] != baseline[path]
    )
    changed_files = len(created) + len(deleted) + len(modified)
    max_changed_files = record["max_changed_files"]
    exhausted = changed_files >= max_changed_files

    return {
        "ok": True,
        "state": "active",
        "status": "check_in_required" if exhausted else "within_budget",
        "budget": {
            "max_changed_files": max_changed_files,
            "changed_files": changed_files,
            "remaining_files": max(0, max_changed_files - changed_files),
            "exhausted": exhausted,
        },
        "changes": {
            "created": created,
            "modified": modified,
            "deleted": deleted,
        },
        "baseline": {
            "recorded_at": record["recorded_at"],
            "reason": record.get("reason"),
            "snapshot_id": record.get("snapshot_id"),
        },
        "next_action": (
            "Run `etg broker handoff` and `etg broker status` before another write."
            if exhausted
            else "Continue through Entigram gates; hand off before the active change budget is exhausted."
        ),
    }


def enforce_active_change_budget(target_dir: Path) -> Dict[str, Any]:
    """Fail closed before another governed write after the active budget is used."""
    status = active_change_status(target_dir)
    if status["budget"]["exhausted"]:
        raise WorkspaceLifecycleError(
            "ACTIVE_CHANGE_CHECK_IN_REQUIRED",
            "Active workspace change budget exhausted. Complete an Entigram handoff before another write.",
            details=status,
        )
    return status


def enforce_paused_change_budget(target_dir: Path) -> Dict[str, Any]:
    """Fail closed when a paused workspace has changed beyond its file budget."""
    status = paused_change_status(target_dir)
    if status["budget"]["over_budget"]:
        raise WorkspaceLifecycleError(
            "PAUSED_CHANGE_BUDGET_EXCEEDED",
            (
                "Paused change budget exceeded. Resume governance and hydrate "
                "before committing more work."
            ),
            details=status,
        )
    return status


def install_workspace_git_checkin_guard(target_dir: Path) -> Dict[str, Any]:
    """Install Entigram's portable commit-time lifecycle backstop.

    Native agent adapters protect supported IDE/CLI agents at tool admission.
    This guard covers every agent that works in a local Git repository: it
    refuses a commit after the workspace's active or paused change budget
    requires an Entigram check-in. It is deliberately a backstop, not a claim
    to observe arbitrary filesystem I/O in real time.
    """
    root = Path(target_dir).expanduser().resolve()
    git_dir = root / ".git"
    if not git_dir.is_dir():
        return {"installed": False, "reason": "not_a_local_git_repository"}

    hook_path = git_dir / "hooks" / "pre-commit"
    original_content = hook_path.read_text() if hook_path.is_file() else None
    if original_content and GIT_CHECKIN_GUARD_START in original_content:
        return {
            "installed": True,
            "changed": False,
            "path": hook_path.relative_to(root).as_posix(),
        }
    original_mode = hook_path.stat().st_mode & 0o777 if hook_path.exists() else None
    _atomic_write_text(
        hook_path,
        _build_workspace_git_checkin_guard(root, original_content),
        mode=original_mode or 0o755,
    )
    return {
        "installed": True,
        "changed": True,
        "path": hook_path.relative_to(root).as_posix(),
    }


def remove_workspace_git_checkin_guard(target_dir: Path) -> Dict[str, Any]:
    """Remove only Entigram's permanent guard, preserving other Git hooks."""
    root = Path(target_dir).expanduser().resolve()
    hook_path = root / ".git" / "hooks" / "pre-commit"
    if not hook_path.is_file():
        return {"removed": False, "path": ".git/hooks/pre-commit"}
    original = hook_path.read_text()
    if GIT_CHECKIN_GUARD_START not in original:
        return {"removed": False, "path": ".git/hooks/pre-commit"}
    updated = re.sub(
        rf"\n?{re.escape(GIT_CHECKIN_GUARD_START)}.*?{re.escape(GIT_CHECKIN_GUARD_END)}(?:\n){{1,2}}",
        "\n",
        original,
        flags=re.DOTALL,
    )
    if updated.strip() in {"", "#!/bin/sh"}:
        hook_path.unlink(missing_ok=True)
        return {
            "removed": True,
            "path": ".git/hooks/pre-commit",
            "removed_empty_hook": True,
        }
    _atomic_write_text(hook_path, updated, mode=hook_path.stat().st_mode & 0o777)
    return {
        "removed": True,
        "path": ".git/hooks/pre-commit",
        "removed_empty_hook": False,
    }


def workspace_git_checkin_guard_status(target_dir: Path) -> Dict[str, Any]:
    root = Path(target_dir).expanduser().resolve()
    hook_path = root / ".git" / "hooks" / "pre-commit"
    return {
        "installed": bool(
            hook_path.is_file() and GIT_CHECKIN_GUARD_START in hook_path.read_text()
        ),
        "path": ".git/hooks/pre-commit",
    }


def _build_workspace_git_checkin_guard(
    root: Path,
    original_content: Optional[str],
) -> str:
    shebang = "#!/bin/sh"
    remainder = original_content or ""
    if original_content and original_content.startswith("#!"):
        shebang, _, remainder = original_content.partition("\n")
    quoted_root = shlex.quote(str(root))
    guard = f"""{GIT_CHECKIN_GUARD_START}
# Portable Entigram backstop for agents without a native lifecycle adapter.
if ! command -v etg >/dev/null 2>&1; then
  echo "Entigram lifecycle guard requires the etg command." >&2
  exit 1
fi
etg change-status --dir {quoted_root} --enforce
entigram_change_status=$?
if [ $entigram_change_status -ne 0 ]; then
  echo "Entigram requires a handoff and current status before this commit." >&2
  exit $entigram_change_status
fi
{GIT_CHECKIN_GUARD_END}
"""
    suffix = remainder.lstrip("\n")
    return f"{shebang}\n{guard}" + (f"\n{suffix}" if suffix else "")


def _load_pause_backup(root: Path) -> Dict[str, Any]:
    backup_path = root / PAUSE_BACKUP_PATH
    if not backup_path.is_file():
        raise WorkspaceLifecycleError(
            "PAUSE_BACKUP_MISSING",
            f"Cannot inspect paused workspace without {backup_path}.",
        )
    try:
        backup = json.loads(backup_path.read_text())
    except Exception as exc:
        raise WorkspaceLifecycleError(
            "PAUSE_BACKUP_INVALID",
            f"Cannot read pause backup: {exc}",
        ) from exc
    if backup.get("version") not in {1, PAUSE_BACKUP_VERSION}:
        raise WorkspaceLifecycleError(
            "PAUSE_BACKUP_INVALID",
            "Unsupported pause backup version.",
        )
    return backup


def _workspace_snapshot(
    root: Path,
    *,
    content_overrides: Optional[Dict[str, str]] = None,
) -> Dict[str, str]:
    """Return a content-addressed snapshot of user-visible workspace files."""
    snapshot: Dict[str, str] = {}
    for directory, subdirectories, filenames in os.walk(root, followlinks=False):
        subdirectories[:] = [
            name for name in subdirectories if name not in _TEXT_SCAN_IGNORES
        ]
        parent = Path(directory)
        for filename in sorted(filenames):
            path = parent / filename
            if path.is_symlink() or not path.is_file():
                continue
            relative = path.relative_to(root)
            digest = hashlib.sha256()
            override = (content_overrides or {}).get(relative.as_posix())
            if override is not None:
                digest.update(override.encode())
            else:
                with path.open("rb") as artifact:
                    for chunk in iter(lambda: artifact.read(1024 * 1024), b""):
                        digest.update(chunk)
            snapshot[relative.as_posix()] = digest.hexdigest()
    return snapshot


def _workspace_metadata_snapshot(root: Path) -> Dict[str, List[int]]:
    """Return lightweight filesystem fingerprints without retaining file content."""
    snapshot: Dict[str, List[int]] = {}
    for directory, subdirectories, filenames in os.walk(root, followlinks=False):
        subdirectories[:] = [
            name for name in subdirectories if name not in _TEXT_SCAN_IGNORES
        ]
        parent = Path(directory)
        for filename in sorted(filenames):
            path = parent / filename
            if path.is_symlink() or not path.is_file():
                continue
            stat = path.stat()
            snapshot[path.relative_to(root).as_posix()] = [
                int(stat.st_size),
                int(stat.st_mtime_ns),
            ]
    return snapshot


def _active_change_budget_limit(root: Path) -> int:
    lifecycle = (load_manifest(root).get("lifecycle") or {})
    change_budget = lifecycle.get("change_budget") or {}
    max_changed_files = change_budget.get(
        "max_changed_files", DEFAULT_ACTIVE_CHANGE_BUDGET_FILES
    )
    if (
        isinstance(max_changed_files, bool)
        or not isinstance(max_changed_files, int)
        or max_changed_files < 1
    ):
        raise WorkspaceLifecycleError(
            "INVALID_ACTIVE_CHANGE_BUDGET",
            "Active change budget must be at least one changed file.",
        )
    return max_changed_files


def _load_active_change_baseline(root: Path) -> Dict[str, Any]:
    path = root / ACTIVE_CHANGE_BASELINE_PATH
    if not path.is_file():
        raise WorkspaceLifecycleError(
            "ACTIVE_CHANGE_BASELINE_MISSING",
            "No active change baseline exists. Hydrate the workspace before another write.",
        )
    try:
        record = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise WorkspaceLifecycleError(
            "ACTIVE_CHANGE_BASELINE_INVALID",
            f"Unable to read active change baseline: {exc}",
        ) from exc
    if (
        not isinstance(record, dict)
        or record.get("version") != ACTIVE_CHANGE_BASELINE_VERSION
        or not isinstance(record.get("baseline"), dict)
        or isinstance(record.get("max_changed_files"), bool)
        or not isinstance(record.get("max_changed_files"), int)
        or record["max_changed_files"] < 1
        or not isinstance(record.get("recorded_at"), str)
    ):
        raise WorkspaceLifecycleError(
            "ACTIVE_CHANGE_BASELINE_INVALID",
            "Active change baseline metadata is invalid. Hydrate the workspace before another write.",
        )
    return record


def _prepare_pause_git_hook(root: Path, max_changed_files: int) -> Dict[str, Any]:
    """Prepare a temporary pre-commit guard without changing the repository yet."""
    git_dir = root / ".git"
    if not git_dir.is_dir():
        return {"installed": False, "reason": "not_a_local_git_repository"}

    hook_path = git_dir / "hooks" / "pre-commit"
    original_content = hook_path.read_text() if hook_path.is_file() else None
    if original_content and (
        PAUSE_GIT_HOOK_START in original_content
        or PAUSE_GIT_HOOK_END in original_content
    ):
        raise WorkspaceLifecycleError(
            "STALE_PAUSE_GIT_HOOK",
            f"Refusing to overwrite an existing Entigram paused hook at {hook_path}.",
        )

    original_mode = hook_path.stat().st_mode & 0o777 if hook_path.exists() else None
    paused_content = _build_pause_git_hook(root, max_changed_files, original_content)
    return {
        "installed": True,
        "path": hook_path.relative_to(root).as_posix(),
        "original_content": original_content,
        "original_mode": original_mode,
        "paused_content": paused_content,
    }


def _build_pause_git_hook(
    root: Path,
    max_changed_files: int,
    original_content: Optional[str],
) -> str:
    shebang = "#!/bin/sh"
    remainder = original_content or ""
    if original_content and original_content.startswith("#!"):
        shebang, _, remainder = original_content.partition("\n")

    quoted_root = shlex.quote(str(root))
    guard = f"""{PAUSE_GIT_HOOK_START}
# Entigram installs this guard only while workspace governance is paused.
if ! command -v etg >/dev/null 2>&1; then
  echo "Entigram paused-workspace guard requires the etg command." >&2
  exit 1
fi
etg pause-status --dir {quoted_root} --enforce
entigram_pause_status=$?
if [ $entigram_pause_status -ne 0 ]; then
  echo "Entigram requires a resume and hydrate check-in before this commit." >&2
  exit $entigram_pause_status
fi
{PAUSE_GIT_HOOK_END}
"""
    suffix = remainder.lstrip("\n")
    return f"{shebang}\n{guard}" + (f"\n{suffix}" if suffix else "")


def _write_pause_git_hook(root: Path, hook: Dict[str, Any]) -> None:
    if not hook.get("installed"):
        return
    path = root / hook["path"]
    _atomic_write_text(path, hook["paused_content"], mode=0o755)


def _restore_pause_git_hook(root: Path, hook: Dict[str, Any]) -> None:
    if not hook.get("installed"):
        return
    path = root / hook["path"]
    original_content = hook.get("original_content")
    if original_content is None:
        path.unlink(missing_ok=True)
        return
    _atomic_write_text(path, original_content, mode=hook.get("original_mode") or 0o755)


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


def pause_workspace(
    target_dir: Path,
    *,
    reason: Optional[str] = None,
    max_changed_files: int = DEFAULT_PAUSED_CHANGE_BUDGET_FILES,
) -> Dict[str, Any]:
    root = Path(target_dir).expanduser().resolve()
    manifest_path = _manifest_path(root)
    manifest = load_manifest(root)
    if (
        isinstance(max_changed_files, bool)
        or not isinstance(max_changed_files, int)
        or max_changed_files < 1
    ):
        raise WorkspaceLifecycleError(
            "INVALID_PAUSE_CHANGE_BUDGET",
            "Paused change budget must be at least one changed file.",
        )
    if workspace_state(root) == "paused":
        change_budget = None
        try:
            change_budget = paused_change_status(root)["budget"]
        except WorkspaceLifecycleError:
            pass
        return {
            "ok": True,
            "changed": False,
            "state": "paused",
            "backup": PAUSE_BACKUP_PATH,
            "change_budget": change_budget,
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
    git_hook = _prepare_pause_git_hook(root, max_changed_files)
    paused_policy = _paused_policy(max_changed_files)
    paused_instruction_block = _paused_instruction_block(max_changed_files)

    for relative in INSTRUCTION_FILES:
        path = root / relative
        if not path.is_file():
            continue
        original = path.read_text()
        original_blocks = _MARKER_RE.findall(original)
        if not original_blocks:
            continue
        paused_blocks = [paused_instruction_block for _ in original_blocks]
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

    baseline = _workspace_snapshot(
        root,
        content_overrides={
            path.relative_to(root).as_posix(): content for path, content in writes
        },
    )

    paused_at = _utc_now()
    backup = {
        "version": PAUSE_BACKUP_VERSION,
        "paused_at": paused_at,
        "reason": reason,
        "change_budget": {
            "max_changed_files": max_changed_files,
            "baseline": baseline,
        },
        "manifest": {
            "path": ".etg/entigram.yaml",
            "original_content": original_manifest,
        },
        "policy": {
            "path": ".etg/agent_policy.md",
            "existed": policy_existed,
            "original_content": original_policy,
            "paused_content": paused_policy,
        },
        "instruction_files": file_backups,
        "git_hook": git_hook,
    }

    lifecycle = dict(manifest.get("lifecycle") or {})
    lifecycle.update({
        "state": "paused",
        "paused_at": paused_at,
    })
    if reason:
        lifecycle["reason"] = reason
    paused_manifest = dict(manifest)
    paused_manifest["lifecycle"] = lifecycle

    originals = {path: path.read_text() if path.exists() else None for path, _ in writes}
    originals[policy_path] = original_policy if policy_existed else None
    if git_hook.get("installed"):
        hook_path = root / git_hook["path"]
        originals[hook_path] = git_hook.get("original_content")
    try:
        _atomic_write_text(backup_path, json.dumps(backup, indent=2, sort_keys=True) + "\n", mode=0o600)
        _atomic_write_text(policy_path, paused_policy)
        for path, content in writes:
            _atomic_write_text(path, content)
        _write_pause_git_hook(root, git_hook)
        _atomic_write_text(
            manifest_path,
            yaml.safe_dump(paused_manifest, default_flow_style=False, sort_keys=False),
        )
    except Exception as exc:
        for path, content in originals.items():
            _restore_path(path, content)
        _restore_pause_git_hook(root, git_hook)
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
        "change_budget": {
            "max_changed_files": max_changed_files,
            "changed_files": 0,
            "remaining_files": max_changed_files,
        },
        "git_pre_commit_guard": bool(git_hook.get("installed")),
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
    backup = _load_pause_backup(root)

    pause_status = None
    if backup.get("version") == PAUSE_BACKUP_VERSION:
        try:
            pause_status = paused_change_status(root)
        except WorkspaceLifecycleError:
            pass

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
    git_hook = backup.get("git_hook") or {}
    if git_hook.get("installed"):
        targets.append(root / git_hook["path"])
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
        _restore_pause_git_hook(root, git_hook)
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
    if pause_status is not None:
        result["paused_change_status"] = pause_status
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
        "hook_configs": [".agents/hooks.json", ".codex/hooks.json", ".claude/settings.json"],
        "preserved_project_artifacts": _preserved_project_artifacts(root),
        "will_remove": [
            ".etg",
            "Entigram entries in native agent hook configuration",
            "Entigram portable Git check-in guard",
        ],
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

    hook_paths = [
        root / ".agents" / "hooks.json",
        root / ".codex" / "hooks.json",
        root / ".claude" / "settings.json",
        root / ".git" / "hooks" / "pre-commit",
    ]
    hook_originals = {
        path: path.read_text() if path.is_file() else None for path in hook_paths
    }
    hook_update = None

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
        from .agent_hooks import remove_agent_hooks

        if workspace_state(root) == "paused":
            _restore_pause_git_hook(root, _load_pause_backup(root).get("git_hook") or {})
        hook_update = remove_agent_hooks(root)
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
        for path, content in hook_originals.items():
            _restore_path(path, content)
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
        "removed_antigravity_hook": bool(
            ((hook_update or {}).get("runtimes") or {})
            .get("antigravity", {})
            .get("removed")
        ),
        "removed_agent_hooks": hook_update or {},
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

    git_hook = backup.get("git_hook") or {}
    if git_hook.get("installed"):
        path = root / git_hook["path"]
        current_content = path.read_text() if path.is_file() else None
        known_hook_values = {
            git_hook.get("paused_content"),
            git_hook.get("original_content"),
        }
        if current_content not in known_hook_values:
            conflicts.append(git_hook["path"])
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
