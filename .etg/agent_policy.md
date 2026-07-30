# Entigram Agent Policy

This file is the canonical project policy for all agents working in this
repository. Agent-specific instruction files must point here instead of
duplicating handoff rules.

## Boot Sequence

1. Run `hydrate` in the initialized workspace. If the console script is not
   available, run `etg hydrate` or
   `python3 -m entigram.cli_runner.etg_cli hydrate`.
2. Read `.etg/entigram.yaml`, `schema.lds`, and this file.
3. If changing implementation behavior, run impact analysis before editing:
   `etg broker preflight --file <path>` and
   `etg broker impact --file <path>`.

## Governance Rules

- Treat `schema.lds` as the closed-world contract for entities and attributes.
- Use MCP/CLI tools for governed writes; do not bypass ledger APIs with ad hoc
  SQL or direct state mutation.
- Unknown entities, invented attributes, unverified alignments, and schema drift
  must be rejected or escalated to the human operator.
- Resolve conflicts through `.etg/state.db`.
- `etg pause` temporarily compacts Entigram-owned context and blocks workspace
  governance operations. `etg resume` restores that context. These workspace
  commands are separate from `etg broker hibernate` and `etg broker resume`,
  which checkpoint an individual agent.
- `etg eject` must archive and validate `.etg` before detaching Entigram. It
  preserves project schemas, ontologies, application code, and unmarked user
  instructions.

## Pre-Handoff Gate

Before handing work back after source, schema, ontology, package, or release
changes:

1. Run `etg broker handoff` (this automatically runs `broker guard`,
   `warden lock`, `broker deliver`, and `broker status`).
2. Run `etg broker status`.

If this repository provides Make, `make handoff` may wrap the same CLI sequence.

`broker status` must report `Delivery status: current` before handoff.
Do not run `warden lock` after `broker deliver`; `warden lock` mutates
`.etg/entigram.yaml` and immediately invalidates the delivery snapshot.

Commit `.etg/entigram.yaml` only when it changed because a schema or ontology
lock was required.
