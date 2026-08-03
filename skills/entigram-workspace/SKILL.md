---
name: entigram-workspace
description: Use Entigram to discover and operate within a schema-first governed agent workspace. Read this skill when a repository contains .etg/entigram.yaml, schema.lds, AGENTS.md, or Entigram MCP tools, and before changing governed artifacts.
---

# Entigram Workspace

Use Entigram as the workspace's semantic governance layer. Treat the local
schema and ledger as the source of governed context, not as a replacement for
the host agent's filesystem, shell, identity, or orchestration controls.

## Boot sequence

1. Read `AGENTS.md` and follow its pointer to `.etg/agent_policy.md`.
2. Run `hydrate` before reasoning about workspace state. Use `etg hydrate` or
   `python3 -m entigram.cli_runner.etg_cli hydrate` if the console script is
   unavailable.
3. Treat only the configured `schema_paths` in `.etg/entigram.yaml` as
   authoritative closed-world schema context.
4. Use `etg_get_workspace_context` and `etg_get_capabilities` when Entigram is
   connected through MCP.

## Before changing files

- Run `etg broker preflight --file <path>` for the intended change.
- Run `etg broker impact --file <path>` before risky implementation, schema,
  ontology, package, or release work.
- Do not treat discovery output, proposed alignments, or logged conflicts as
  approved operational facts.

## Before handoff

Run:

```bash
etg broker handoff
etg broker status
```

Handoff is complete only when broker status reports `Delivery status: current`.
Do not invoke state-changing MCP tools without the host workflow's approval.
