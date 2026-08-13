import json
import sys
import ipaddress
from typing import Optional

from entigram.mcp_service import EntigramMCPService
from entigram.usage import MCP_TOOL_DECLARATIONS


def create_mcp_server(target_dir: str = ".", host: Optional[str] = None, port: Optional[int] = None):
    try:
        from mcp.server import MCPServer
        from mcp.types import ToolAnnotations
    except ImportError as exc:
        raise RuntimeError(
            "The MCP SDK is required for `etg serve`. "
            "Install project dependencies, including `mcp`, and retry."
        ) from exc

    # Host and port are transport settings in MCP SDK v2.  They are retained in
    # this factory's signature for callers of previous releases and applied by
    # run_mcp_server below.
    del host, port
    mcp = MCPServer(
        "entigram",
        title="Entigram workspace governance",
        description="Schema-first semantic governance for local agent workspaces.",
        instructions=(
            "Start with etg_get_workspace_context or etg_get_capabilities. "
            "Use write tools only after reviewing the local schema and impact."
        ),
    )
    service = EntigramMCPService(target_dir)

    @mcp.tool(
        annotations=ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        )
    )
    def etg_get_schemas() -> str:
        """Return local LDS schemas and parsed entity boundaries."""
        try:
            return service.get_schemas()
        except Exception as exc:
            return _tool_error("SCHEMA_DISCOVERY_FAILED", f"Failed to read schemas - {exc}")

    @mcp.tool(
        annotations=ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        )
    )
    def etg_get_impact(file_path: str) -> str:
        """Read the localized context and change-impact graph for a workspace file; does not write workspace state."""
        try:
            return service.get_impact(file_path)
        except Exception as exc:
            return _tool_error("IMPACT_ANALYSIS_FAILED", f"Failed to analyze impact - {exc}")

    @mcp.tool(
        annotations=ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        )
    )
    def etg_get_workspace_context() -> str:
        """Read workspace lifecycle, manifest, schema, delivery, security, and instruction context."""
        try:
            return service.get_workspace_context()
        except Exception as exc:
            return _tool_error("WORKSPACE_CONTEXT_DISCOVERY_FAILED", f"Failed to read workspace context - {exc}")

    @mcp.tool(
        annotations=ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        )
    )
    def etg_get_capabilities() -> str:
        """Read the authoritative Entigram MCP capability catalog and safety boundaries."""
        try:
            return service.get_capabilities()
        except Exception as exc:
            return _tool_error("CAPABILITY_DISCOVERY_FAILED", f"Failed to read capability catalog - {exc}")

    @mcp.tool(
        annotations=ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        )
    )
    def etg_get_assessment_capabilities() -> str:
        """Return non-executable assessment metadata and current capability advisories."""
        try:
            return service.get_assessment_capabilities()
        except Exception as exc:
            return _tool_error(
                "ASSESSMENT_CAPABILITY_DISCOVERY_FAILED",
                f"Failed to inspect assessment capabilities - {exc}",
            )

    @mcp.tool(
        annotations=ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        )
    )
    def etg_assess(payload: str) -> str:
        """Request an assessment; installed executable adapters are disabled by default."""
        try:
            return service.assess(payload)
        except Exception as exc:
            return _tool_error("ASSESSMENT_FAILED", f"Assessment failed - {exc}")

    @mcp.tool(
        annotations=ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=False,
            openWorldHint=False,
        )
    )
    def etg_propose_alignment(payload: str) -> str:
        """Validate and record a proposed semantic alignment."""
        try:
            return service.propose_alignment(payload)
        except Exception as exc:
            return _tool_error("INVALID_SCHEMA_ALIGNMENT", f"Invalid Schema Alignment - {exc}")

    @mcp.tool(
        annotations=ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=True,
            idempotentHint=False,
            openWorldHint=False,
        )
    )
    def etg_log_conflict(payload: str) -> str:
        """Validate and log a deterministic conflict for human review."""
        try:
            return service.log_conflict(payload)
        except Exception as exc:
            return _tool_error("INVALID_CONFLICT", f"Invalid Conflict - {exc}")

    registered_tools = {
        "etg_get_schemas",
        "etg_get_impact",
        "etg_get_workspace_context",
        "etg_get_capabilities",
        "etg_get_assessment_capabilities",
        "etg_assess",
        "etg_propose_alignment",
        "etg_log_conflict",
    }
    declared_tools = {tool["name"] for tool in MCP_TOOL_DECLARATIONS}
    if registered_tools != declared_tools:
        raise RuntimeError("MCP usage declarations do not match registered tools")

    return mcp


def _tool_error(code: str, detail: str) -> str:
    return json.dumps(
        {
            "ok": False,
            "error": {
                "code": code,
                "message": f"Error: {detail}",
                "details": detail,
            },
        },
        sort_keys=True,
    )


def run_mcp_server(
    target_dir: str = ".",
    *,
    transport: str = "stdio",
    host: str = "127.0.0.1",
    port: int = 8080,
):
    if transport not in {"stdio", "sse"}:
        raise ValueError("transport must be 'stdio' or 'sse'")
    if transport == "sse":
        try:
            loopback = ipaddress.ip_address(host).is_loopback
        except ValueError:
            loopback = host.lower() == "localhost"
        if not loopback:
            raise ValueError(
                "SSE transport is restricted to loopback until authenticated remote transport is available"
            )

    try:
        server = create_mcp_server(target_dir)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from exc

    if transport == "sse":
        server.run(transport=transport, host=host, port=port)
    else:
        server.run(transport=transport)
