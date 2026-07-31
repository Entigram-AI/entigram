import json

from entigram.mcp_service import EntigramMCPService


def request_human_tiebreaker(
    conflict_id: str,
    entity_type: str,
    conflicting_state: dict,
    rationale: str,
    workspace: str = ".",
):
    """Log a Plaid conflict through Entigram's governed MCP service."""
    agent_id = "edge_plaid"
    service = EntigramMCPService(workspace)
    response = json.loads(
        service.log_conflict(
            json.dumps(
                {
                    "conflict_id": conflict_id,
                    "entity_type": entity_type,
                    "proposed_states": {agent_id: conflicting_state},
                    "agent_id": agent_id,
                }
            )
        )
    )
    if not response.get("ok"):
        raise RuntimeError(f"Entigram rejected conflict {conflict_id}: {response}")
    print(f"[ENTIGRAM LEDGER] Conflict {conflict_id} logged. Awaiting human resolution.")
    return response
