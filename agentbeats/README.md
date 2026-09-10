# Entigram Sentinel on AgentBeats

The public Amber manifest for AgentBeats registration is:

`https://raw.githubusercontent.com/Entigram-AI/entigram/main/agentbeats/amber-manifest-sentinel.json5`

The manifest starts `ghcr.io/entigram-ai/entigram-sentinel:latest` and exposes
an A2A agent card plus a non-streaming `message/send` endpoint on port 9010.

This first image is deliberately side-effect free. It establishes the public
identity and transport contract required for registration. Do not use it for a
scored PI-Bench submission until the PI-Bench context bootstrap, policy-aware
model adapter, and Entigram broker-mediated tool boundary are implemented.
