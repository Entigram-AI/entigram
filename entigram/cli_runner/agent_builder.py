import argparse
import re
from pathlib import Path


_AGENT_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


def _validated_agent_name(agent_name: str) -> str:
    if not isinstance(agent_name, str) or not _AGENT_NAME_RE.fullmatch(agent_name):
        raise ValueError(
            "agent name must start with a lowercase letter and contain only "
            "lowercase letters, numbers, and underscores"
        )
    return agent_name


def generate_agent_boilerplate(agent_name: str, target_dir: str):
    """Scaffold an edge agent that writes conflicts through the MCP service."""
    agent_name = _validated_agent_name(agent_name)
    target_path = Path(target_dir).expanduser().resolve()
    target_path.mkdir(parents=True, exist_ok=True)
    agent_path = target_path / f"{agent_name}_edge"
    agent_path.mkdir(exist_ok=True)

    skill_content = f"""# Edge Agent: {agent_name.capitalize()}
## Role
You are a localized edge-agent responsible for parsing {agent_name} data into the local Entigram Schema.

## Ledger Constraint (CRITICAL)
If you detect a state contradiction between {agent_name} and the local Schema, you MUST halt execution. Do not hallucinate a resolution.
You must invoke the `request_human_tiebreaker` function to log the conflict through Entigram's governed MCP service and await human input.
"""
    (agent_path / "SKILL.md").write_text(skill_content)

    hook_content = f'''import json

from entigram.mcp_service import EntigramMCPService


def request_human_tiebreaker(
    conflict_id: str,
    entity_type: str,
    conflicting_state: dict,
    rationale: str,
    workspace: str = ".",
):
    """Log a conflict through Entigram's governed MCP surface and halt."""
    agent_id = "edge_{agent_name}"
    service = EntigramMCPService(workspace)
    response = json.loads(
        service.log_conflict(
            json.dumps(
                {{
                    "conflict_id": conflict_id,
                    "entity_type": entity_type,
                    "proposed_states": {{agent_id: conflicting_state}},
                    "agent_id": agent_id,
                }}
            )
        )
    )
    if not response.get("ok"):
        raise RuntimeError(f"Entigram rejected conflict {{conflict_id}}: {{response}}")
    print(f"[ENTIGRAM LEDGER] Conflict {{conflict_id}} logged. Awaiting human resolution.")
    return response
'''
    (agent_path / "ledger_hook.py").write_text(hook_content)

    print(f"Successfully bootstrapped {agent_name} agent at {agent_path}")
    print("Boilerplate includes SKILL.md and a governed MCP ledger hook")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Entigram Agent Scaffolder")
    parser.add_argument("name", help="Name of the edge agent (e.g., stripe, salesforce)")
    parser.add_argument("--dir", default="./templates", help="Target directory for the scaffold")
    args = parser.parse_args()
    generate_agent_boilerplate(args.name, args.dir)
