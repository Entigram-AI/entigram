# Gemini Agent Registry integration

This guide exposes Entigram as a governed MCP server to Gemini Enterprise while
preserving Entigram's local-first default. It also provides the same remote MCP
endpoint shape for ChatGPT/Codex and Claude.

## Architecture

```text
Gemini Enterprise agent
  -> Agent Gateway (identity, egress policy, observability)
  -> authenticated HTTPS reverse proxy
  -> Entigram Streamable HTTP /mcp
  -> one tenant/workspace's schema and decision ledger
```

Entigram validates semantic concepts, alignments, conflicts, and delivery
evidence. Google Cloud remains responsible for agent identity, network policy,
and runtime isolation. Do not expose a developer workstation's `.etg/state.db`
or run an arbitrary workspace as a shared remote service.

## Before registering

An operator, not an agent, must supply these organization-specific values:

| Value | Purpose |
| --- | --- |
| Google Cloud project and region | Must match the Gemini Enterprise app, Agent Registry, and Agent Gateway placement. |
| HTTPS endpoint | Final public URL, normally `https://governance.example.com/mcp`. |
| OAuth/OIDC issuer and scopes | Authenticates calls and restricts the server to the intended agent/user principals. |
| Workspace or tenant mapping | Ensures each connection resolves to only one authorized Entigram ledger and schema contract. |
| Gateway policy | Allows only the Gemini agent identities and explicitly approved Entigram tools. |

Use least privilege. Grant read tools separately from ledger-writing tools, and
require a host approval workflow for `etg_propose_alignment` and
`etg_log_conflict`.

## Deploy the endpoint

Package Entigram in your approved runtime with its governed workspace mounted
read/write only where the ledger needs it. Put TLS and OAuth/OIDC validation at
your organization-approved gateway or reverse proxy. The container entrypoint
is:

```bash
etg serve \
  --dir /srv/entigram-workspace \
  --transport streamable-http \
  --host 0.0.0.0 \
  --port "$PORT" \
  --mcp-path /mcp \
  --allow-remote-streamable-http
```

`--allow-remote-streamable-http` only acknowledges that a non-loopback bind is
intentional. It does not add credentials or authorization. Do not use it on an
Internet-reachable process without the authenticated gateway/proxy in front of
it. The HTTP service is stateless so it can run behind a managed platform, but
each replica must access the correct isolated workspace/ledger.

Smoke-test the deployed service through the same gateway path Gemini will use.
Confirm `etg_get_capabilities` and `etg_get_workspace_context` succeed before
enabling write tools.

## Add Entigram to Agent Registry

1. In Google Cloud, create or select the Agent Registry associated with the
   Agent Gateway for the Gemini Enterprise app's region.
2. Register the HTTPS Streamable HTTP endpoint as an MCP server. Set the
   interface URL to the exact final endpoint, such as
   `https://governance.example.com/mcp`; do not register an internal, tunnel, or
   redirect URL.
3. Configure OAuth 2.0 using the organization-owned authorization URL, token
   URL, client credentials, and the minimum scopes required by the Entigram
   tools. Gemini Enterprise uses its documented OAuth redirect URI.
4. Associate the registered MCP server with the Agent Gateway and apply allow
   policies for the intended Gemini agent identities. Start with read-only
   Entigram tools.
5. Add the registered MCP server to the Gemini Enterprise app, then authorize
   individual tools in the agent or workflow configuration.
6. Run an end-to-end test: call `etg_get_capabilities`, then
   `etg_get_workspace_context`; test a rejected unknown concept before
   permitting any ledger-writing tool.

Google’s documented Agent Registry import flow requires the registry, gateway,
and Gemini Enterprise app to be regionally compatible. It also notes that a
direct Gemini connector path can bypass Agent Gateway enforcement, which is why
this guide uses the registered, gateway-routed path.

## Helping users onboard

Give customers a short intake checklist rather than asking them to paste
credentials into an agent chat:

1. Collect their project/region, final HTTPS endpoint, identity provider,
   intended Gemini app, and workspace-to-tenant mapping through their approved
   security process.
2. Deploy and test the endpoint in their environment; Entigram support should
   verify tool discovery only, never request their OAuth client secret.
3. Walk the customer administrator through the six registration steps above.
4. Enable read-only tools first. Add proposal/conflict tools only after an
   owner has approved the workflow and reviewed the ledger evidence.
5. Record the endpoint, scopes, gateway policy, owning team, and rollback
   contact as deployment evidence in the customer's change process.

## Other MCP hosts

The same HTTPS `/mcp` endpoint can be connected to ChatGPT/Codex or Claude as
a remote MCP server. Those hosts have their own connection and approval
controls; Entigram's semantic rules do not change. For developer workstations,
prefer the existing local server instead:

```bash
etg serve --transport stdio
```

Never share a remote endpoint across tenants merely because the MCP protocol is
the same. Connection configuration is not authorization to cross a workspace
or ledger boundary.
