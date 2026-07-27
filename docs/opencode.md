# OpenCode Integration

OpenCode is the recommended general-purpose coding agent surface for Entigram
workspaces. Entigram should provide the governance layer underneath the agent:
hydration, closed-world schemas, MCP tools, broker gates, delivery snapshots,
and audit evidence.

This keeps Entigram out of agent-specific launch plumbing. OpenCode handles the
coding UI, model/provider selection, Cloudflare setup, and MCP client behavior.
Entigram exposes a local MCP server and workspace standard that OpenCode can
use.

## Recommended Path

1. Install Entigram.

```bash
pipx install entigram-ai
```

2. Initialize or enter a governed workspace.

```bash
etg init --dir my-governed-agent
cd my-governed-agent
hydrate
```

3. Copy the example OpenCode config.

```bash
cp /path/to/entigram/.opencode.example.jsonc .opencode.jsonc
```

4. Start OpenCode from the workspace root.

```bash
opencode
```

5. In OpenCode, ask it to use the Entigram MCP tools when changing governed
schema, ontology, package, or implementation behavior.

```text
Hydrate first, then use the entigram MCP server before risky changes. Before
handoff, run etg broker handoff and etg broker status.
```

## Local Entigram MCP

Use Entigram as a local MCP server in `.opencode.jsonc`:

```jsonc
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "entigram": {
      "type": "local",
      "command": ["etg", "serve", "--dir", "."],
      "enabled": true
    }
  }
}
```

This gives OpenCode access to Entigram's governed tools while keeping the
workspace local-first. The MCP server reads `.etg/entigram.yaml`, exposes only
authoritative `schema_paths`, and writes governed proposals to `.etg/state.db`.

## Cloudflare

For Cloudflare projects, use Cloudflare's OpenCode setup instead of Entigram's
Ollama compatibility proxy. Cloudflare documents OpenCode with Cloudflare
Skills, remote MCP servers, Wrangler, and Workers AI model provider setup.

Recommended Cloudflare posture:

- Use OpenCode's Cloudflare Workers AI provider or Cloudflare AI Gateway for
  model access.
- Add Cloudflare Skills when working on Workers, D1, R2, Durable Objects,
  Workers AI, Vectorize, or Cloudflare deployments.
- Enable Cloudflare remote MCP servers only when needed. MCP servers add tool
  context, so avoid enabling the full Cloudflare catalog for ordinary Entigram
  governance work.
- Keep Entigram's local MCP server enabled for schema, alignment, conflict, and
  handoff governance.

The example config keeps Cloudflare MCP servers disabled by default so users opt
in deliberately.

## Advanced Proxy Path

`etg cloudflare-ollama-proxy` and `etg cloudflare-claude` remain available for
advanced users who specifically need an Ollama-compatible bridge into
Cloudflare Workers AI or a Claude Code launch shim.

Do not use those commands as the default Entigram onboarding path. They are
experimental compatibility tools, not the strategic Entigram integration layer.

The preferred integration boundary is:

```text
OpenCode coding agent
  -> Entigram local MCP governance
  -> broker gates and audit ledger
  -> optional Cloudflare MCP/Workers AI for Cloudflare projects
```

