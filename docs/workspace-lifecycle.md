# Workspace Usage and Lifecycle

Entigram workspaces expose provider-neutral usage estimates and reversible
lifecycle controls. These commands govern the workspace integration itself;
they do not manage model-provider billing or pause a running agent process.

## Usage

```bash
etg usage
etg usage --total-tokens 50000
etg usage --json
```

`etg usage` reports two distinct measures:

- static footprint: agent policy, marked instruction blocks, MCP declarations,
  and compact/default/full hydration vectors
- observed traffic: aggregate input and output counts for short-lived Entigram
  CLI operations and MCP tool calls

The estimator is `heuristic_chars_div_4_v1`, defined as
`ceil(characters / 4)`. It is a provider-neutral comparison measure, not a bill.
When `--total-tokens` is supplied, Entigram divides the current hydration
session's observed estimate by that total.

The latest recorded `hydrate` or `boot` event starts the current Entigram
session. Events before that boundary remain visible in the all-time total.
Long-running UIs, proxies, agent launchers, and MCP server transports are
excluded. Individual MCP tool calls are included.

The local `usage_events` ledger table stores operation names, surfaces,
character counts, estimated token counts, lifecycle state, timestamps, and
small operational metadata. It never stores prompt, argument, response, stdout,
or stderr content.

## Pause

```bash
etg pause
etg pause --reason "Temporarily working without governed context"
```

Pause is a workspace governance control. It:

1. backs up the exact Entigram policy and marked instruction blocks under
   `.etg/lifecycle/pause-backup.json`
2. replaces that owned context with a small paused notice
3. sets `lifecycle.state: paused` in `.etg/entigram.yaml`
4. blocks governance CLI and MCP operations with `WORKSPACE_PAUSED`

While paused, `hydrate` returns only a small paused envelope. It does not load
schema or delivery state. The available commands are `etg usage`, `etg resume`,
and `etg eject`; read-only `etg config --list` also remains available.

## Resume

```bash
etg resume
```

Resume restores the exact policy and Entigram-owned instruction blocks while
preserving user edits outside those blocks. If an owned paused block changed,
resume refuses to overwrite it.

```bash
etg resume --force
```

Forced resume first preserves conflicting content under
`.etg/lifecycle/conflicts/<timestamp>/`, then restores the owned context. Run
`hydrate` after a successful resume.

Workspace resume is distinct from `etg broker resume`, which reads a durable
checkpoint for one hibernated agent.

## Eject

Inspect the operation first:

```bash
etg eject --dry-run
```

Detach interactively:

```bash
etg eject
```

Automation must confirm explicitly:

```bash
etg eject --yes
```

Eject creates and validates `entigram-eject-<UTC timestamp>.tar.gz`, sets its
mode to `0600`, removes Entigram marker blocks from known instruction files,
and removes `.etg`. It preserves `schema.lds`, draft schemas, ontologies,
application code, and unmarked instruction content.

The archive contains the complete `.etg` directory and an eject manifest. It
may contain private signing keys and local governance evidence, so treat it as
sensitive. Eject does not uninstall the global CLI or rewrite user-owned agent
configuration. Re-enroll a workspace with `etg init`; `etg resume` does not
restore an eject archive.
