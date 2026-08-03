# Entigram Discoverability

This page is the index for agents, MCP hosts, package catalogs, and developers
trying to understand or install Entigram.

## Discovery surfaces

| Surface | Audience | Canonical entry point |
| --- | --- | --- |
| Repository instructions | Coding agents | [`AGENTS.md`](../AGENTS.md), then [`.etg/agent_policy.md`](../.etg/agent_policy.md) |
| Agent Skill | Skill-aware agent hosts | [`skills/entigram-workspace/SKILL.md`](../skills/entigram-workspace/SKILL.md) |
| MCP server metadata | MCP registries and clients | [`server.json`](../server.json) |
| MCP capability contract | MCP clients and tool authors | [`docs/mcp-tools.md`](mcp-tools.md) |
| Workspace contract | Implementers and integrators | [`docs/workspace-standard.md`](workspace-standard.md) |
| Signed package catalog | Package discovery and trust review | [`community-packages/standard_package_catalog.json`](../community-packages/standard_package_catalog.json) |

## Recommended agent path

For a repository checkout, read `AGENTS.md`, load `.etg/agent_policy.md`, and
run `hydrate`. For an MCP connection, call `etg_get_capabilities` first, then
`etg_get_workspace_context`, `etg_get_schemas`, or `etg_get_impact` as needed.
Agents should branch on the stable JSON `error.code` field and should not infer
authorization from a successful read-only response.

The local MCP server is started with:

```bash
pipx install entigram-ai
etg serve --transport stdio
```

The server also supports loopback-only SSE for local development. It does not
currently provide an authenticated public endpoint, so this repository does
not publish an A2A Agent Card or a remote MCP URL.

## MCP Registry publication

`server.json` describes the PyPI package `entigram-ai` and must stay versioned
with the package release. The published package README must contain the exact
ownership marker below:

```html
<!-- mcp-name: io.github.entigram-ai/entigram -->
```

Before publication:

1. Build and publish the matching `entigram-ai` version to PyPI.
2. Confirm the package README contains the matching `mcp-name` marker.
3. Install the MCP publisher using the official registry instructions.
4. Authenticate with the Entigram-AI GitHub organization and publish from the
   repository root, where `server.json` is located.
5. Verify the resulting entry in the official registry and downstream catalogs.

The official registry is a metadata source for downstream aggregators; it is
not a substitute for the local workspace contract or the signed Entigram
package catalog. Entigram complements MCP hosts and orchestration frameworks;
it does not replace them.

## Boundaries

- `AGENTS.md` routes an agent to local instructions; it is not a package
  registry.
- `SKILL.md` provides progressive-disclosure instructions; it does not grant
  filesystem or network permissions.
- MCP exposes governed operations; the host still controls transport,
  credentials, approval, and direct tool access.
- Entigram package manifests and signatures describe installed package content;
  clients must still evaluate publisher trust and execution isolation.
