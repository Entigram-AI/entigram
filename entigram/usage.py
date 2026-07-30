import json
import math
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from entigram.sqlite_ledger.manager import LedgerManager
from entigram.sqlite_ledger.paths import resolve_ledger_path
from entigram.workspace_lifecycle import (
    PAUSE_BACKUP_PATH,
    instruction_blocks,
    load_manifest,
    workspace_state,
)


ESTIMATOR = "heuristic_chars_div_4_v1"
MCP_TOOL_DECLARATIONS = (
    {
        "name": "etg_get_schemas",
        "input": {},
        "description": "Return local LDS schemas and parsed entity boundaries.",
    },
    {
        "name": "etg_get_impact",
        "input": {"file_path": "string"},
        "description": "Analyze the localized context and impact graph for a file.",
    },
    {
        "name": "etg_propose_alignment",
        "input": {"payload": "json-string"},
        "description": "Validate and record a proposed semantic alignment.",
    },
    {
        "name": "etg_log_conflict",
        "input": {"payload": "json-string"},
        "description": "Validate and log a deterministic conflict for human review.",
    },
)


class CountingWriter:
    """Forwards stream writes while retaining only their character count."""

    def __init__(self, stream):
        self.stream = stream
        self.character_count = 0

    def write(self, value):
        self.character_count += len(value)
        return self.stream.write(value)

    def flush(self):
        return self.stream.flush()

    def isatty(self):
        return self.stream.isatty()

    def fileno(self):
        return self.stream.fileno()

    @property
    def encoding(self):
        return getattr(self.stream, "encoding", None)

    def __getattr__(self, name):
        return getattr(self.stream, name)


def estimate_tokens(value: Any) -> int:
    characters = value if isinstance(value, int) else len(str(value))
    return int(math.ceil(max(0, characters) / 4))


def payload_character_count(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, str):
        return len(value)
    try:
        return len(json.dumps(value, separators=(",", ":"), sort_keys=True))
    except (TypeError, ValueError):
        return len(str(value))


def record_workspace_usage(
    target_dir: Path,
    *,
    operation: str,
    surface: str,
    input_characters: int,
    output_characters: int,
    lifecycle_state: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Optional[int]:
    root = Path(target_dir).expanduser().resolve()
    if not (root / ".etg" / "entigram.yaml").is_file():
        return None
    try:
        state = lifecycle_state or workspace_state(root)
        manager = LedgerManager(str(resolve_ledger_path(str(root))))
        try:
            return manager.record_usage_event(
                operation=operation,
                surface=surface,
                input_characters=input_characters,
                output_characters=output_characters,
                estimated_input_tokens=estimate_tokens(input_characters),
                estimated_output_tokens=estimate_tokens(output_characters),
                lifecycle_state=state,
                metadata=metadata,
            )
        finally:
            manager.close()
    except Exception:
        return None


def build_usage_report(
    target_dir: Path,
    *,
    hydration_vectors: Dict[str, str],
    total_tokens: Optional[int] = None,
) -> Dict[str, Any]:
    root = Path(target_dir).expanduser().resolve()
    load_manifest(root)
    state = workspace_state(root)
    policy_path = root / ".etg" / "agent_policy.md"
    policy_content = policy_path.read_text() if policy_path.is_file() else ""
    blocks = instruction_blocks(root)
    block_characters = sum(item["characters"] for item in blocks)
    declaration_text = json.dumps(
        MCP_TOOL_DECLARATIONS,
        separators=(",", ":"),
        sort_keys=True,
    )

    footprint = {
        "policy": _counted_component(
            len(policy_content),
            paths=[".etg/agent_policy.md"] if policy_path.is_file() else [],
        ),
        "agent_instruction_blocks": _counted_component(
            block_characters,
            paths=[item["path"] for item in blocks],
        ),
        "mcp_tool_declarations": _counted_component(
            len(declaration_text),
            count=len(MCP_TOOL_DECLARATIONS),
        ),
        "hydration_vectors": {
            mode: _counted_component(len(content))
            for mode, content in hydration_vectors.items()
        },
    }
    document_characters = (
        footprint["policy"]["characters"]
        + footprint["agent_instruction_blocks"]["characters"]
        + footprint["mcp_tool_declarations"]["characters"]
    )
    footprint["documents_total"] = _counted_component(document_characters)

    ledger = LedgerManager(str(resolve_ledger_path(str(root))))
    try:
        observed = ledger.get_usage_summary()
    finally:
        ledger.close()

    report = {
        "ok": True,
        "workspace": str(root),
        "lifecycle_state": state,
        "estimator": {
            "name": ESTIMATOR,
            "description": "Estimated tokens equal ceil(characters / 4).",
            "provider_billing": False,
        },
        "footprint": footprint,
        "observed": observed,
        "attribution": None,
        "limitations": [
            "Observed accounting begins when this Entigram version records usage events.",
            "Estimates are provider-neutral and are not billing records.",
            "Long-running agent, proxy, UI, and MCP transport processes are excluded; MCP tool calls are counted.",
            "Usage events store aggregate counts and operation metadata, never prompt or response content.",
        ],
    }
    if total_tokens is not None:
        if total_tokens <= 0:
            raise ValueError("--total-tokens must be greater than zero")
        session_tokens = observed["session"]["estimated_total_tokens"]
        report["attribution"] = {
            "supplied_total_tokens": total_tokens,
            "entigram_session_estimated_tokens": session_tokens,
            "estimated_percent": round((session_tokens / total_tokens) * 100, 2),
        }

    saved = _saved_active_context(root)
    if saved is not None:
        current = (
            footprint["policy"]["estimated_tokens"]
            + footprint["agent_instruction_blocks"]["estimated_tokens"]
        )
        saved["estimated_token_reduction"] = max(
            0,
            saved["estimated_tokens"] - current,
        )
        report["saved_active_context"] = saved
    return report


def format_usage_report(report: Dict[str, Any]) -> str:
    observed = report["observed"]
    footprint = report["footprint"]
    lines = [
        f"Entigram usage ({report['lifecycle_state']})",
        f"Estimator: {report['estimator']['name']} (not provider billing)",
        "",
        "Observed Entigram traffic:",
        (
            "  Current session: "
            f"{observed['session']['estimated_total_tokens']:,} estimated tokens "
            f"across {observed['session']['event_count']} events"
        ),
        (
            "  All time: "
            f"{observed['all_time']['estimated_total_tokens']:,} estimated tokens "
            f"across {observed['all_time']['event_count']} events"
        ),
        "",
        "Static context footprint:",
        f"  Agent policy: {footprint['policy']['estimated_tokens']:,} estimated tokens",
        (
            "  Agent instruction blocks: "
            f"{footprint['agent_instruction_blocks']['estimated_tokens']:,} estimated tokens"
        ),
        (
            "  MCP declarations: "
            f"{footprint['mcp_tool_declarations']['estimated_tokens']:,} estimated tokens"
        ),
    ]
    for mode in ("compact", "default", "full"):
        component = footprint["hydration_vectors"].get(mode)
        if component is not None:
            lines.append(
                f"  Hydration ({mode}): {component['estimated_tokens']:,} estimated tokens"
            )
    attribution = report.get("attribution")
    if attribution:
        lines.extend(
            [
                "",
                (
                    "Estimated session attribution: "
                    f"{attribution['estimated_percent']:.2f}% "
                    f"({attribution['entigram_session_estimated_tokens']:,} / "
                    f"{attribution['supplied_total_tokens']:,} tokens)"
                ),
            ]
        )
    saved = report.get("saved_active_context")
    if saved:
        lines.append(
            "Paused context reduction: "
            f"{saved['estimated_token_reduction']:,} estimated tokens"
        )
    return "\n".join(lines)


def _counted_component(
    characters: int,
    *,
    paths: Optional[Iterable[str]] = None,
    count: Optional[int] = None,
) -> Dict[str, Any]:
    result = {
        "characters": characters,
        "estimated_tokens": estimate_tokens(characters),
    }
    if paths is not None:
        result["paths"] = list(paths)
    if count is not None:
        result["count"] = count
    return result


def _saved_active_context(root: Path) -> Optional[Dict[str, Any]]:
    backup_path = root / PAUSE_BACKUP_PATH
    if not backup_path.is_file():
        return None
    try:
        backup = json.loads(backup_path.read_text())
        policy_characters = len(backup["policy"].get("original_content", ""))
        block_characters = sum(
            len(block)
            for item in backup.get("instruction_files", [])
            for block in item.get("original_blocks", [])
        )
    except (KeyError, TypeError, ValueError, OSError):
        return None
    characters = policy_characters + block_characters
    return {
        "characters": characters,
        "estimated_tokens": estimate_tokens(characters),
        "source": PAUSE_BACKUP_PATH,
    }
