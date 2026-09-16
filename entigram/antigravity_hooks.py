"""Antigravity lifecycle hooks for Entigram-governed workspaces.

The hook protocol is intentionally small: Antigravity invokes this module with
event JSON on stdin and expects a JSON decision on stdout. The hooks establish a
workspace session before the first model invocation, gate write-capable tools on
that session, track observed writes, and request one final handoff pass when an
agent stops with uncommissioned work.
"""

import hashlib
import json
import os
import re
import shlex
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from .governance.warden import Warden
from .workspace_lifecycle import (
    WorkspaceLifecycleError,
    active_change_status,
    ensure_active_change_baseline,
    is_workspace_paused,
    load_manifest,
    paused_change_status,
    workspace_state,
)
from .task_context import task_context_is_ready, task_context_status, task_prepare_required


ANTIGRAVITY_HOOK_NAME = "entigram-session-gate"
ANTIGRAVITY_HOOK_PATH = ".agents/hooks.json"
ANTIGRAVITY_SESSION_PATH = ".etg/lifecycle/antigravity-sessions.json"
WRITE_CAPABLE_TOOLS = {
    "write_to_file",
    "replace_file_content",
    "multi_replace_file_content",
    "run_command",
    "generate_image",
}


def install_antigravity_hooks(target_dir: Path) -> Dict[str, Any]:
    """Merge Entigram's namespaced Antigravity hook entry into a workspace."""
    root = Path(target_dir).expanduser().resolve()
    hooks_path = root / ANTIGRAVITY_HOOK_PATH
    hooks = _load_hook_config(hooks_path)
    previous = hooks.get(ANTIGRAVITY_HOOK_NAME)
    hooks[ANTIGRAVITY_HOOK_NAME] = _hook_definition(root)
    _write_json(hooks_path, hooks)
    return {
        "installed": True,
        "path": ANTIGRAVITY_HOOK_PATH,
        "changed": previous != hooks[ANTIGRAVITY_HOOK_NAME],
    }


def remove_antigravity_hooks(target_dir: Path) -> Dict[str, Any]:
    """Remove only Entigram's hook entry, leaving all other hooks untouched."""
    root = Path(target_dir).expanduser().resolve()
    hooks_path = root / ANTIGRAVITY_HOOK_PATH
    if not hooks_path.is_file():
        return {"removed": False, "path": ANTIGRAVITY_HOOK_PATH}
    hooks = _load_hook_config(hooks_path)
    removed = hooks.pop(ANTIGRAVITY_HOOK_NAME, None) is not None
    if removed:
        if hooks:
            _write_json(hooks_path, hooks)
        else:
            hooks_path.unlink()
    return {
        "removed": removed,
        "path": ANTIGRAVITY_HOOK_PATH,
        "removed_empty_config": removed and not hooks,
    }


def handle_antigravity_hook(
    target_dir: Path,
    event: str,
    payload: Optional[Dict[str, Any]] = None,
    *,
    runtime: str = "antigravity",
) -> Dict[str, Any]:
    """Return the JSON result required by one native lifecycle event."""
    root = Path(target_dir).expanduser().resolve()
    data = payload if isinstance(payload, dict) else {}
    try:
        from .workspace_lifecycle import active_agent_adapter_status

        enforcement = active_agent_adapter_status(root, agent=runtime)
        if not enforcement["ok"]:
            reason = (
                "Entigram workspace-agent enforcement is required. "
                + enforcement.get("next_action", "Declare and configure this agent.")
            )
            if event == "pre-invocation":
                return {"injectSteps": [{"ephemeralMessage": reason}]}
            return {"decision": "deny", "reason": reason}
        if event == "pre-invocation":
            return _pre_invocation(root, data, runtime=runtime)
        if event == "pre-tool-use":
            return _pre_tool_use(root, data, runtime=runtime)
        if event == "post-tool-use":
            return _post_tool_use(root, data, runtime=runtime)
        if event == "stop":
            return _stop(root, data, runtime=runtime)
        return {"decision": "deny", "reason": f"Unsupported Entigram hook event: {event}"}
    except (WorkspaceLifecycleError, OSError, ValueError) as exc:
        return _hook_error(event, str(exc))


def _hook_definition(root: Path) -> Dict[str, Any]:
    command_prefix = f"etg antigravity-hook --dir {shlex.quote(str(root))} --event"
    return {
        "PreInvocation": [
            {
                "type": "command",
                "command": f"{command_prefix} pre-invocation",
                "timeout": 15,
            }
        ],
        "PreToolUse": [
            {
                "matcher": "write_to_file|replace_file_content|multi_replace_file_content|run_command|generate_image",
                "hooks": [
                    {
                        "type": "command",
                        "command": f"{command_prefix} pre-tool-use",
                        "timeout": 10,
                    }
                ],
            }
        ],
        "PostToolUse": [
            {
                "matcher": "write_to_file|replace_file_content|multi_replace_file_content|run_command|generate_image",
                "hooks": [
                    {
                        "type": "command",
                        "command": f"{command_prefix} post-tool-use",
                        "timeout": 10,
                    }
                ],
            }
        ],
        "Stop": [
            {
                "type": "command",
                "command": f"{command_prefix} stop",
                "timeout": 15,
            }
        ],
    }


def _pre_invocation(
    root: Path,
    payload: Dict[str, Any],
    *,
    runtime: str = "antigravity",
) -> Dict[str, Any]:
    conversation_id = _conversation_id(payload)
    manifest = load_manifest(root)
    state = workspace_state(root)
    if state == "active":
        ensure_active_change_baseline(root)
    fingerprint = _session_fingerprint(root, manifest, state)
    session = _session_record(root, conversation_id)
    if session is not None and session.get("fingerprint") == fingerprint:
        return {"injectSteps": []}

    record = {
        "started_at": _utc_now(),
        "workspace_state": state,
        "fingerprint": fingerprint,
        "writes_observed": 0,
        "stop_reminded": False,
        "runtime": runtime,
    }
    _update_session(root, conversation_id, record)

    if state == "paused":
        status = paused_change_status(root)
        budget = status["budget"]
        return {
            "injectSteps": [
                {
                    "ephemeralMessage": (
                        "Entigram is paused. The workspace has used "
                        f"{budget['changed_files']}/{budget['max_changed_files']} "
                        "paused changes. Do not make another change after the budget "
                        "is exhausted; run `etg resume` and `hydrate` first."
                    )
                }
            ]
        }

    check_in = active_change_status(root)
    schema_paths = [
        str((root / path).resolve())
        for path in manifest.get("schema_paths", ["schema.lds"])
        if (root / path).is_file()
    ]
    policy_path = root / ".etg" / "agent_policy.md"
    steps: List[Dict[str, Any]] = []
    if policy_path.is_file():
        steps.append(
            {
                "userMessage": (
                    "Entigram governance policy for this workspace:\n\n"
                    f"{policy_path.read_text(encoding='utf-8')}"
                )
            }
        )
    for schema_path in schema_paths:
        schema_file = Path(schema_path)
        steps.append(
            {
                "userMessage": (
                    f"Entigram authoritative schema ({schema_file.name}):\n\n"
                    f"{schema_file.read_text(encoding='utf-8')}"
                )
            }
        )
    if task_prepare_required(manifest):
        task_status = task_context_status(root, manifest)
        steps.append(
            {
                "ephemeralMessage": (
                    "This workspace requires task preparation before governed writes. "
                    "Run `etg task prepare --id <id> --description-file <file>` first. "
                    f"Current task context: {task_status['status']}."
                )
            }
        )
    budget = check_in["budget"]
    warn_text = ""
    if budget.get("warning"):
        warn_text = f" (Warning: {budget.get('warn_changed_files', 5)}-file threshold reached)"
    steps.append(
        {
            "ephemeralMessage": (
                "Entigram session gate is active. Policy and authoritative schemas were "
                "loaded before this turn. The workspace has used "
                f"{budget['changed_files']}/"
                f"{budget['max_changed_files']} changed files since the "
                f"last Entigram check-in{warn_text}. Use preflight and impact before risky changes; "
                "run handoff and status before finishing."
            )
        }
    )
    return {"injectSteps": steps}


def _pre_tool_use(
    root: Path,
    payload: Dict[str, Any],
    *,
    runtime: str = "antigravity",
) -> Dict[str, Any]:
    tool_call = payload.get("toolCall") or {}
    tool_name = tool_call.get("name")
    command = str((tool_call.get("args") or {}).get("CommandLine", ""))

    if tool_name == "run_command":
        if is_lifecycle_recovery_command(command):
            return {"decision": "allow"}
        if is_read_only_command(command):
            return {"decision": "allow"}

    conversation_id = _conversation_id(payload)
    session = _session_record(root, conversation_id)
    if session is None:
        return {
            "decision": "deny",
            "reason": "Entigram session gate has not hydrated this workspace yet.",
        }

    manifest = load_manifest(root)
    state = workspace_state(root)

    if state == "paused":
        status = paused_change_status(root)
        if status["budget"]["exhausted"]:
            return {
                "decision": "deny",
                "reason": status["next_action"],
            }
        return {"decision": "allow"}

    if not Warden(str(root)).verify_integrity(emit_human=False):
        return {
            "decision": "deny",
            "reason": "Entigram Warden integrity check failed. Restore or authorize the contract change first.",
        }

    if task_prepare_required(manifest) and not task_context_is_ready(root, manifest):
        return {
            "decision": "deny",
            "reason": (
                "Task preparation is required before governed writes. Run "
                "`etg task prepare --id <id> --description-file <file>` first."
            ),
        }

    status = active_change_status(root)
    if status["budget"]["exhausted"]:
        return {
            "decision": "deny",
            "reason": status["next_action"],
        }

    return {"decision": "allow"}


def _post_tool_use(
    root: Path,
    payload: Dict[str, Any],
    *,
    runtime: str = "antigravity",
) -> Dict[str, Any]:
    tool_call = payload.get("toolCall") or {}
    tool_name = tool_call.get("name")
    if tool_name not in WRITE_CAPABLE_TOOLS:
        return {}
    conversation_id = _conversation_id(payload)
    session = _session_record(root, conversation_id)
    if session is None:
        return {}
    session["writes_observed"] = int(session.get("writes_observed", 0)) + 1
    session["last_tool"] = tool_name
    session["last_tool_at"] = _utc_now()
    session["runtime"] = runtime
    _update_session(root, conversation_id, session)
    return {}


def _stop(
    root: Path,
    payload: Dict[str, Any],
    *,
    runtime: str = "antigravity",
) -> Dict[str, Any]:
    conversation_id = _conversation_id(payload)
    session = _session_record(root, conversation_id)
    if not session or session.get("workspace_state") != "active":
        return {}
    if session.get("stop_reminded"):
        return {}

    check_in = active_change_status(root)
    if not check_in["budget"]["changed_files"]:
        return {}

    session["stop_reminded"] = True
    _update_session(root, conversation_id, session)
    return {
        "decision": "continue",
        "reason": (
            "Entigram detected workspace changes since the last check-in "
            f"({check_in['budget']['changed_files']}/"
            f"{check_in['budget']['max_changed_files']} files). Run `etg broker handoff` "
            "and `etg broker status` before ending this session."
        ),
    }


def _hook_error(event: str, message: str) -> Dict[str, Any]:
    if event == "pre-tool-use":
        return {"decision": "deny", "reason": f"Entigram hook error: {message}"}
    if event != "pre-invocation":
        return {}
    return {
        "injectSteps": [
            {"ephemeralMessage": f"Entigram hook error: {message}"}
        ]
    }


_RECOVERY_COMMAND_TOKENS = (
    "hydrate",
    "boot",
    "etg task prepare",
    "task prepare",
    "etg broker preflight",
    "broker preflight",
    "etg broker impact",
    "broker impact",
    "etg broker handoff",
    "broker handoff",
    "etg broker status",
    "broker status",
    "etg pause-status",
    "pause-status",
    "etg pause",
    "etg resume",
    "etg usage",
    "etg eject",
)

_READ_ONLY_BINARIES = {
    "pwd", "ls", "dir", "cat", "head", "tail", "less", "more",
    "grep", "rg", "ag", "find", "fd", "locate", "which", "whereis",
    "type", "file", "awk", "cut", "sort", "uniq", "wc", "diff",
    "cmp", "tr", "echo", "printf", "true", "false", "whoami",
    "id", "uname", "uptime", "date", "env", "printenv", "test",
    "stat", "basename", "dirname", "readlink", "realpath",
}

_SAFE_GIT_SUBCOMMANDS = {
    "status", "log", "diff", "show", "branch", "rev-parse",
    "ls-files", "describe", "check-ignore", "cat-file", "config",
    "tag", "remote", "version", "help",
}


def is_lifecycle_recovery_command(command: str) -> bool:
    if not command or not command.strip():
        return False
    normalized = " ".join(command.strip().split())
    for prefix in (
        "python -m entigram.cli_runner.etg_cli ",
        "python3 -m entigram.cli_runner.etg_cli ",
        ".venv/bin/python -m entigram.cli_runner.etg_cli ",
    ):
        if normalized.startswith(prefix):
            normalized = normalized[len(prefix):].strip()
    if normalized in {"hydrate", "boot"} or normalized.startswith("hydrate ") or normalized.startswith("etg hydrate") or normalized.startswith("boot ") or normalized.startswith("etg boot"):
        return True
    return any(token in normalized for token in _RECOVERY_COMMAND_TOKENS)


def _segment_is_read_only(segment: str) -> bool:
    segment = segment.strip()
    if not segment:
        return True
    if re.search(r">(?!&1|\s*/dev/null)", segment):
        return False
    try:
        tokens = shlex.split(segment)
    except ValueError:
        tokens = segment.split()
    if not tokens:
        return True
    idx = 0
    while idx < len(tokens) and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=.*", tokens[idx]):
        idx += 1
    tokens = tokens[idx:]
    if not tokens:
        return True
    while tokens and tokens[0] in {"env", "sudo", "time", "nohup"}:
        tokens = tokens[1:]
        while tokens and tokens[0].startswith("-"):
            tokens = tokens[1:]
        while tokens and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=.*", tokens[0]):
            tokens = tokens[1:]
    if not tokens:
        return True
    binary = Path(tokens[0]).name.lower()
    if binary in {"python", "python3", "python3.10", "python3.11", "python3.12", "python3.13", "python3.14"}:
        args = tokens[1:]
        if len(args) >= 2 and args[0] == "-m" and args[1] in {"unittest", "pytest"}:
            return True
        return False
    if binary in {"pytest", "trial"}:
        return True
    if binary in {"npm", "pnpm", "yarn"}:
        return len(tokens) > 1 and tokens[1] in {"test", "t", "run"}
    if binary in {"cargo", "go"}:
        return len(tokens) > 1 and tokens[1] == "test"
    if binary in {"run", "run.sh", "run.py"}:
        return len(tokens) > 1 and tokens[1] in {"test", "tests"}
    if binary == "git":
        args = tokens[1:]
        while args and args[0].startswith("-"):
            if args[0] in {"-C", "-c"} and len(args) > 1:
                args = args[2:]
            else:
                args = args[1:]
        if not args:
            return True
        subcmd = args[0].lower()
        if subcmd not in _SAFE_GIT_SUBCOMMANDS:
            return False
        if subcmd == "branch":
            for flag in args[1:]:
                if flag in {"-d", "-D", "-m", "-M", "--delete", "--move"}:
                    return False
        return True
    if binary == "sed":
        for arg in tokens[1:]:
            if arg == "-i" or arg.startswith("-i") or arg.startswith("--in-place"):
                return False
        return True
    return binary in _READ_ONLY_BINARIES


def is_read_only_command(command: str) -> bool:
    if not command or not command.strip():
        return True
    segments = re.split(r";|&&|\|\||\|", command)
    return all(_segment_is_read_only(seg) for seg in segments)


def _is_paused_lifecycle_command(payload: Dict[str, Any]) -> bool:
    command = str(((payload.get("toolCall") or {}).get("args") or {}).get("CommandLine", ""))
    return is_lifecycle_recovery_command(command) or any(
        marker in command
        for marker in ("etg resume", "etg pause-status", "etg usage", "etg eject")
    )


def _is_active_check_in_command(payload: Dict[str, Any]) -> bool:
    command = str(((payload.get("toolCall") or {}).get("args") or {}).get("CommandLine", ""))
    return is_lifecycle_recovery_command(command) or "broker handoff" in command or "broker status" in command


def _is_task_bootstrap_command(payload: Dict[str, Any]) -> bool:
    command = str(((payload.get("toolCall") or {}).get("args") or {}).get("CommandLine", ""))
    return is_lifecycle_recovery_command(command)


def _manifest_semantic_digest(path: Path) -> Optional[str]:
    if not path.is_file():
        return None
    try:
        manifest = yaml.safe_load(path.read_text()) or {}
        if isinstance(manifest, dict):
            semantic = dict(manifest)
            semantic.pop("last_locked", None)
            semantic.pop("last_updated", None)
            payload = json.dumps(semantic, sort_keys=True, separators=(",", ":"))
            return hashlib.sha256(payload.encode("utf-8")).hexdigest()
    except Exception:
        pass
    return _file_digest(path)


def _session_fingerprint(
    root: Path,
    manifest: Dict[str, Any],
    state: str,
) -> Dict[str, Any]:
    schema_paths = manifest.get("schema_paths", ["schema.lds"])
    return {
        "workspace_state": state,
        "manifest_sha256": _manifest_semantic_digest(root / ".etg" / "entigram.yaml"),
        "policy_sha256": _file_digest(root / ".etg" / "agent_policy.md"),
        "schemas": {
            str(path): _file_digest(root / str(path))
            for path in schema_paths
        },
    }


def _file_digest(path: Path) -> Optional[str]:
    if not path.is_file():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _conversation_id(payload: Dict[str, Any]) -> str:
    value = payload.get("conversationId")
    return str(value) if value else "unknown-conversation"


def _session_record(root: Path, conversation_id: str) -> Optional[Dict[str, Any]]:
    sessions = _load_sessions(root)
    value = sessions.get("sessions", {}).get(conversation_id)
    return dict(value) if isinstance(value, dict) else None


def _update_session(root: Path, conversation_id: str, value: Dict[str, Any]) -> None:
    sessions = _load_sessions(root)
    values = sessions.setdefault("sessions", {})
    values[conversation_id] = value
    if len(values) > 100:
        oldest = sorted(values, key=lambda key: values[key].get("started_at", ""))[:-100]
        for key in oldest:
            values.pop(key, None)
    _write_json(root / ANTIGRAVITY_SESSION_PATH, sessions, mode=0o600)


def _load_sessions(root: Path) -> Dict[str, Any]:
    path = root / ANTIGRAVITY_SESSION_PATH
    if not path.is_file():
        return {"version": 1, "sessions": {}}
    try:
        value = json.loads(path.read_text())
    except (OSError, ValueError):
        return {"version": 1, "sessions": {}}
    if not isinstance(value, dict) or not isinstance(value.get("sessions"), dict):
        return {"version": 1, "sessions": {}}
    return value


def _load_hook_config(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise WorkspaceLifecycleError(
            "INVALID_ANTIGRAVITY_HOOK_CONFIG",
            f"Unable to read {path}: {exc}",
        ) from exc
    if not isinstance(value, dict):
        raise WorkspaceLifecycleError(
            "INVALID_ANTIGRAVITY_HOOK_CONFIG",
            f"{path} must contain a JSON object.",
        )
    return value


def _write_json(path: Path, value: Dict[str, Any], *, mode: Optional[int] = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    existing_mode = path.stat().st_mode & 0o777 if path.exists() else 0o644
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(json.dumps(value, indent=2, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_path, mode if mode is not None else existing_mode)
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
