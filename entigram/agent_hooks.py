"""Provider adapters for Entigram's shared agent lifecycle gate.

The admission decisions, directory-change budget, Warden check, and handoff
prompt live in the existing Entigram lifecycle implementation. This module
projects that single gate onto the native hook protocols exposed by Codex and
Claude Code, and manages their project-local configuration without replacing
user-owned hooks.
"""

import json
import os
import shlex
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from .antigravity_hooks import (
    ANTIGRAVITY_HOOK_NAME,
    handle_antigravity_hook,
    install_antigravity_hooks,
    remove_antigravity_hooks,
)
from .workspace_lifecycle import (
    SUPPORTED_AGENT_RUNTIMES,
    WorkspaceLifecycleError,
    normalize_agent_runtime,
)


CODEX_HOOK_PATH = ".codex/hooks.json"
CLAUDE_HOOK_PATH = ".claude/settings.json"
CODEX_RUNTIME = "codex"
CLAUDE_RUNTIME = "claude"
ANTIGRAVITY_RUNTIME = "antigravity"
ALL_RUNTIMES = SUPPORTED_AGENT_RUNTIMES


def install_agent_hooks(
    target_dir: Path,
    *,
    engine: str = "all",
) -> Dict[str, Any]:
    """Install every requested native adapter plus the portable Git backstop."""
    root = Path(target_dir).expanduser().resolve()
    runtimes = _requested_runtimes(engine)
    installed: Dict[str, Any] = {}
    if ANTIGRAVITY_RUNTIME in runtimes:
        installed[ANTIGRAVITY_RUNTIME] = install_antigravity_hooks(root)
    if CODEX_RUNTIME in runtimes:
        installed[CODEX_RUNTIME] = _install_codex_hooks(root)
    if CLAUDE_RUNTIME in runtimes:
        installed[CLAUDE_RUNTIME] = _install_claude_hooks(root)

    from .workspace_lifecycle import install_workspace_git_checkin_guard

    return {
        "ok": True,
        "workspace": str(root),
        "runtimes": installed,
        "git_checkin_guard": install_workspace_git_checkin_guard(root),
    }


def remove_agent_hooks(target_dir: Path) -> Dict[str, Any]:
    """Remove Entigram-owned adapter entries without removing user hooks."""
    root = Path(target_dir).expanduser().resolve()
    from .workspace_lifecycle import remove_workspace_git_checkin_guard

    return {
        "ok": True,
        "runtimes": {
            ANTIGRAVITY_RUNTIME: remove_antigravity_hooks(root),
            CODEX_RUNTIME: _remove_runtime_hooks(root, CODEX_RUNTIME, CODEX_HOOK_PATH),
            CLAUDE_RUNTIME: _remove_runtime_hooks(root, CLAUDE_RUNTIME, CLAUDE_HOOK_PATH),
        },
        "git_checkin_guard": remove_workspace_git_checkin_guard(root),
    }


def agent_hook_status(
    target_dir: Path, *, include_git_checkin_guard: bool = True
) -> Dict[str, Any]:
    """Report installed adapters and the portable enforcement backstop."""
    root = Path(target_dir).expanduser().resolve()
    antigravity = _load_json_config(root / ".agents" / "hooks.json")
    codex = _load_json_config(root / CODEX_HOOK_PATH)
    claude = _load_json_config(root / CLAUDE_HOOK_PATH)
    result = {
        "ok": True,
        "workspace": str(root),
        "runtimes": {
            ANTIGRAVITY_RUNTIME: ANTIGRAVITY_HOOK_NAME in antigravity,
            CODEX_RUNTIME: _contains_runtime_hook(codex, CODEX_RUNTIME),
            CLAUDE_RUNTIME: _contains_runtime_hook(claude, CLAUDE_RUNTIME),
        },
    }
    if include_git_checkin_guard:
        from .workspace_lifecycle import workspace_git_checkin_guard_status

        result["git_checkin_guard"] = workspace_git_checkin_guard_status(root)
    return result


def handle_agent_hook(
    target_dir: Path,
    *,
    runtime: str,
    event: str,
    payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Translate a Codex or Claude hook event to Entigram's lifecycle gate."""
    normalized_runtime = _normalize_runtime(runtime)
    if normalized_runtime not in {CODEX_RUNTIME, CLAUDE_RUNTIME}:
        return {"decision": "deny", "reason": f"Unsupported agent runtime: {runtime}"}

    from .workspace_lifecycle import active_agent_adapter_status

    enforcement = active_agent_adapter_status(Path(target_dir), agent=normalized_runtime)
    if not enforcement["ok"]:
        reason = (
            "Entigram workspace-agent enforcement is required. "
            + enforcement.get("next_action", "Declare and configure this agent.")
        )
        if event.strip().lower() == "session-start":
            return _codex_response(
                event,
                {"injectSteps": [{"ephemeralMessage": reason}]},
                target_dir,
            ) if normalized_runtime == CODEX_RUNTIME else _claude_response(
                event,
                {"injectSteps": [{"ephemeralMessage": reason}]},
                target_dir,
            )
        return _codex_response(event, {"decision": "deny", "reason": reason}, target_dir) if normalized_runtime == CODEX_RUNTIME else _claude_response(
            event, {"decision": "deny", "reason": reason}, target_dir
        )

    data = payload if isinstance(payload, dict) else {}
    internal_event, internal_payload = _internal_hook_request(
        normalized_runtime, event, data
    )
    result = handle_antigravity_hook(
        target_dir, internal_event, internal_payload, runtime=normalized_runtime
    )

    if normalized_runtime == CODEX_RUNTIME:
        return _codex_response(event, result, target_dir)
    return _claude_response(event, result, target_dir)


def _requested_runtimes(engine: str) -> Iterable[str]:
    normalized = _normalize_runtime(engine)
    if normalized == "all":
        return ALL_RUNTIMES
    if normalized not in ALL_RUNTIMES:
        raise WorkspaceLifecycleError(
            "UNSUPPORTED_AGENT_ENGINE",
            "Supported lifecycle hook engines are Antigravity, Codex, Claude Code, or all.",
        )
    return (normalized,)


def _normalize_runtime(value: str) -> str:
    normalized = (value or "all").strip().lower()
    return "all" if normalized == "all" else (normalize_agent_runtime(normalized) or normalized)


def _install_codex_hooks(root: Path) -> Dict[str, Any]:
    return _install_runtime_hooks(
        root,
        runtime=CODEX_RUNTIME,
        relative_path=CODEX_HOOK_PATH,
        event_groups={
            "SessionStart": _hook_group(
                root,
                CODEX_RUNTIME,
                "session-start",
                matcher="startup|resume|clear|compact",
                context_limit=0,
            ),
            "PreToolUse": _hook_group(
                root,
                CODEX_RUNTIME,
                "pre-tool-use",
                matcher="Bash|apply_patch|Edit|Write|mcp__.*",
            ),
            "PostToolUse": _hook_group(
                root,
                CODEX_RUNTIME,
                "post-tool-use",
                matcher="Bash|apply_patch|Edit|Write|mcp__.*",
            ),
            "Stop": _hook_group(root, CODEX_RUNTIME, "stop"),
        },
    )


def _install_claude_hooks(root: Path) -> Dict[str, Any]:
    return _install_runtime_hooks(
        root,
        runtime=CLAUDE_RUNTIME,
        relative_path=CLAUDE_HOOK_PATH,
        event_groups={
            "SessionStart": _hook_group(
                root, CLAUDE_RUNTIME, "session-start", matcher="startup|resume|clear|compact"
            ),
            "PreToolUse": _hook_group(
                root,
                CLAUDE_RUNTIME,
                "pre-tool-use",
                matcher="Bash|Edit|Write|MultiEdit|mcp__.*",
            ),
            "PostToolUse": _hook_group(
                root,
                CLAUDE_RUNTIME,
                "post-tool-use",
                matcher="Bash|Edit|Write|MultiEdit|mcp__.*",
            ),
            "Stop": _hook_group(root, CLAUDE_RUNTIME, "stop"),
        },
    )


def _hook_group(
    root: Path,
    runtime: str,
    event: str,
    *,
    matcher: Optional[str] = None,
    context_limit: Optional[int] = None,
) -> Dict[str, Any]:
    handler: Dict[str, Any] = {
        "type": "command",
        "command": _hook_command(root, runtime, event),
        "timeout": 15,
    }
    if context_limit is not None:
        handler["additionalContextLimit"] = context_limit
    group: Dict[str, Any] = {"hooks": [handler]}
    if matcher:
        group["matcher"] = matcher
    return group


def _hook_command(root: Path, runtime: str, event: str) -> str:
    return (
        f"etg agent-hook --dir {shlex.quote(str(root))} "
        f"--runtime {runtime} --event {event}"
    )


def _install_runtime_hooks(
    root: Path,
    *,
    runtime: str,
    relative_path: str,
    event_groups: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    path = root / relative_path
    config = _load_json_config(path)
    hooks = config.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise WorkspaceLifecycleError(
            "INVALID_AGENT_HOOK_CONFIG",
            f"{path} has a non-object hooks field.",
        )
    token = _runtime_command_token(runtime)
    changed = False
    for event, group in event_groups.items():
        existing = hooks.get(event, [])
        if not isinstance(existing, list):
            raise WorkspaceLifecycleError(
                "INVALID_AGENT_HOOK_CONFIG",
                f"{path} has a non-array hooks.{event} field.",
            )
        filtered = _remove_runtime_groups(existing, token)
        updated = filtered + [group]
        changed = changed or updated != existing
        hooks[event] = updated
    if changed or not path.exists():
        _write_json(path, config)
    return {"installed": True, "path": relative_path, "changed": changed}


def _remove_runtime_hooks(root: Path, runtime: str, relative_path: str) -> Dict[str, Any]:
    path = root / relative_path
    if not path.is_file():
        return {"removed": False, "path": relative_path}
    config = _load_json_config(path)
    hooks = config.get("hooks")
    if not isinstance(hooks, dict):
        return {"removed": False, "path": relative_path}
    token = _runtime_command_token(runtime)
    removed = False
    for event in list(hooks):
        groups = hooks[event]
        if not isinstance(groups, list):
            continue
        filtered = _remove_runtime_groups(groups, token)
        if filtered != groups:
            removed = True
        if filtered:
            hooks[event] = filtered
        else:
            hooks.pop(event)
    if not removed:
        return {"removed": False, "path": relative_path}
    if not hooks:
        config.pop("hooks", None)
        if config.get("description", "").startswith("Entigram lifecycle hooks"):
            config.pop("description", None)
        if not config:
            path.unlink()
            return {"removed": True, "path": relative_path, "removed_empty_config": True}
    _write_json(path, config)
    return {"removed": True, "path": relative_path, "removed_empty_config": False}


def _remove_runtime_groups(groups: Iterable[Any], token: str) -> list:
    retained = []
    for group in groups:
        if not isinstance(group, dict):
            retained.append(group)
            continue
        handlers = group.get("hooks")
        if not isinstance(handlers, list):
            retained.append(group)
            continue
        kept_handlers = [
            handler
            for handler in handlers
            if not (isinstance(handler, dict) and token in str(handler.get("command", "")))
        ]
        if kept_handlers:
            copied = dict(group)
            copied["hooks"] = kept_handlers
            retained.append(copied)
    return retained


def _contains_runtime_hook(config: Dict[str, Any], runtime: str) -> bool:
    token = _runtime_command_token(runtime)
    for groups in (config.get("hooks") or {}).values():
        if not isinstance(groups, list):
            continue
        for group in groups:
            for handler in (group.get("hooks", []) if isinstance(group, dict) else []):
                if isinstance(handler, dict) and token in str(handler.get("command", "")):
                    return True
    return False


def _runtime_command_token(runtime: str) -> str:
    return f"--runtime {runtime}"


def _internal_hook_request(
    runtime: str,
    event: str,
    payload: Dict[str, Any],
) -> tuple[str, Dict[str, Any]]:
    session_id = str(payload.get("session_id") or payload.get("sessionId") or "unknown")
    conversation_id = f"{runtime}:{session_id}"
    normalized_event = event.strip().lower()
    if normalized_event == "session-start":
        return "pre-invocation", {"conversationId": conversation_id}
    if normalized_event in {"pre-tool-use", "post-tool-use"}:
        tool_input = payload.get("tool_input") or payload.get("toolInput") or {}
        if not isinstance(tool_input, dict):
            tool_input = {}
        tool_name = str(payload.get("tool_name") or payload.get("toolName") or "")
        command = str(tool_input.get("command", ""))
        return normalized_event, {
            "conversationId": conversation_id,
            "toolCall": {
                "name": "run_command" if tool_name == "Bash" else "write_to_file",
                "args": {"CommandLine": command},
            },
        }
    if normalized_event == "stop":
        return "stop", {"conversationId": conversation_id}
    raise WorkspaceLifecycleError(
        "UNSUPPORTED_AGENT_HOOK_EVENT",
        f"Unsupported {runtime} lifecycle event: {event}",
    )


def _codex_response(event: str, result: Dict[str, Any], target_dir: Path) -> Dict[str, Any]:
    normalized_event = event.strip().lower()
    if normalized_event == "session-start":
        if not result.get("injectSteps"):
            return {}
        return {
            "hookSpecificOutput": {
                "hookEventName": "SessionStart",
                "additionalContext": _startup_context(target_dir, result),
            }
        }
    if normalized_event == "pre-tool-use" and result.get("decision") == "deny":
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": result.get("reason", "Entigram denied this action."),
            }
        }
    if normalized_event == "stop" and result.get("decision") == "continue":
        return {"decision": "block", "reason": result.get("reason", "Run Entigram handoff.")}
    return {}


def _claude_response(event: str, result: Dict[str, Any], target_dir: Path) -> Dict[str, Any]:
    normalized_event = event.strip().lower()
    if normalized_event == "session-start":
        if not result.get("injectSteps"):
            return {}
        return {
            "hookSpecificOutput": {
                "hookEventName": "SessionStart",
                "additionalContext": _startup_context(target_dir, result),
            }
        }
    if normalized_event == "pre-tool-use" and result.get("decision") == "deny":
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": result.get("reason", "Entigram denied this action."),
            }
        }
    if normalized_event == "stop" and result.get("decision") == "continue":
        return {"decision": "block", "reason": result.get("reason", "Run Entigram handoff.")}
    return {}


def _startup_context(target_dir: Path, result: Dict[str, Any]) -> str:
    root = Path(target_dir).expanduser().resolve()
    schema_paths = []
    messages = []
    for step in result.get("injectSteps", []):
        tool_call = step.get("toolCall") if isinstance(step, dict) else None
        if isinstance(tool_call, dict):
            absolute_path = (tool_call.get("args") or {}).get("AbsolutePath")
            if absolute_path and Path(absolute_path).resolve() != root / ".etg" / "agent_policy.md":
                schema_paths.append(Path(absolute_path).resolve())
        if isinstance(step, dict) and step.get("ephemeralMessage"):
            messages.append(str(step["ephemeralMessage"]))
    policy_path = root / ".etg" / "agent_policy.md"
    policy = policy_path.read_text() if policy_path.is_file() else ""
    schemas = []
    for schema_path in schema_paths:
        if schema_path.is_file() and (schema_path == root or root in schema_path.parents):
            schemas.append(
                f"Authoritative schema ({schema_path.relative_to(root)}):\n{schema_path.read_text()}"
            )
    return "\n\n".join(
        [
            "Entigram lifecycle gate is active. Treat this as the startup context for this workspace.",
            f"Canonical policy ({policy_path.relative_to(root)}):",
            policy,
            *schemas,
            *messages,
        ]
    ).strip()


def _load_json_config(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise WorkspaceLifecycleError(
            "INVALID_AGENT_HOOK_CONFIG", f"Unable to read {path}: {exc}"
        ) from exc
    if not isinstance(value, dict):
        raise WorkspaceLifecycleError(
            "INVALID_AGENT_HOOK_CONFIG", f"{path} must contain a JSON object."
        )
    return value


def _write_json(path: Path, value: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    existing_mode = path.stat().st_mode & 0o777 if path.exists() else 0o644
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(json.dumps(value, indent=2, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_path, existing_mode)
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)
