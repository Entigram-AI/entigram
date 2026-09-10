# Entigram Sentinel on AgentBeats

The public Amber manifest for AgentBeats registration is:

`https://raw.githubusercontent.com/Entigram-AI/entigram/main/agentbeats/amber-manifest-sentinel.json5`

The manifest starts `ghcr.io/entigram-ai/entigram-sentinel:latest` and exposes
an A2A agent card plus a non-streaming `message/send` endpoint on port 9010.

For PI-Bench, Sentinel advertises the policy-bootstrap extension. The green
agent supplies policy/task context and declared tool schemas once, and Sentinel
caches only that supplied context for the session. It calls the configured
OpenAI Responses model (default `gpt-5.2`) and returns only tool calls contained
in the declared contract, with a normalized Entigram decision event for every
proposal. The container also retains a Cloudflare Responses fallback for
self-hosted deployments that explicitly set `ENTIGRAM_SENTINEL_PROVIDER=cloudflare`.

The PI-Bench green agent remains the external action executor. The current
participant therefore proves proposal-time contract mediation; it does not
claim external-action prevention until a broker-backed executor boundary is
also deployed.
