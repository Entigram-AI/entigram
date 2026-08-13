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

## Active change check-in

```bash
etg change-status
```

Active workspaces receive a five-file change budget by default. Entigram records
the initial directory baseline at `etg init` and refreshes it only after a
successful `etg broker handoff`. At the budget, another write requires:

```bash
etg broker handoff
etg broker status
```

`etg change-status --enforce` exits nonzero when that check-in is required.
The comparison scans governed workspace paths and records only each path's size
and modification time; it does not retain application-file content. It excludes
Git metadata, `.etg`, virtual environments, dependencies, caches, and build
output. This makes modifications by editors or other agents visible even when
they did not use an Entigram command.

The limit is stored in the workspace manifest:

```yaml
lifecycle:
  state: active
  change_budget:
    max_changed_files: 5
```

## Antigravity lifecycle hooks

When initialized with `--engine Antigravity`, Entigram adds only its namespaced
`entigram-session-gate` entry to `.agents/hooks.json`. It does not replace other
workspace hooks. The gate uses Antigravity's `PreInvocation`, `PreToolUse`,
`PostToolUse`, and `Stop` events to:

1. load the policy and authoritative schema before the first model turn, and
   reload them if either changes
2. deny write-capable tools until that session has loaded workspace context
3. rescan directory state before every admitted write and require a broker
   handoff when the active change budget is exhausted
4. request one final handoff when an agent attempts to stop with changes since
   the last accepted check-in

The hooks inspect the workspace only when Antigravity invokes an event; they do
not run a permanent background process or watch paths outside the workspace.
Host-level filesystem controls remain necessary for a hard guarantee against a
process that bypasses the host's tool hooks entirely.

To add the hooks to an existing workspace, run:

```bash
etg config --engine Antigravity
```

## Pause

```bash
etg pause
etg pause --reason "Temporarily working without governed context"
etg pause --max-changed-files 10
```

Pause is a workspace governance control. It:

1. backs up the exact Entigram policy and marked instruction blocks under
   `.etg/lifecycle/pause-backup.json`
2. replaces that owned context with a small paused notice
3. sets `lifecycle.state: paused` in `.etg/entigram.yaml`
4. blocks governance CLI and MCP operations with `WORKSPACE_PAUSED`
5. snapshots user-visible workspace files and starts a five-file paused-change
   budget by default
6. installs a temporary Git pre-commit guard when the workspace has a local
   `.git` directory

While paused, `hydrate` returns only a small paused envelope. It does not load
schema or delivery state. The available commands are `etg usage`,
`etg pause-status`, `etg resume`, and `etg eject`; read-only `etg config --list`
also remains available.

## Paused change budget

```bash
etg pause-status
```

Pause is an intentional escape hatch, not an unbounded governance bypass. The
default budget allows five changed files relative to the pause baseline. At the
budget, Entigram requires a check-in before another change:

```bash
etg resume
hydrate
```

Use `etg pause --max-changed-files N` only when a different bounded window is
appropriate. `etg pause-status --enforce` exits nonzero only after the budget
has been exceeded; the temporary pre-commit guard uses that mode, allowing the
budgeted set of changes but preventing an oversized paused commit. Resume
restores any pre-existing local `pre-commit` hook or removes Entigram's
temporary hook.

Entigram cannot intercept a host agent's direct filesystem writes. The pause
instruction, budget status command, and Git hook make drift visible and prevent
it from being silently committed; host platforms should additionally restrict
direct write access when a stronger guarantee is required.

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
removes only Entigram's named Antigravity hook entry, and removes `.etg`. It
preserves `schema.lds`, draft schemas, ontologies, application code, unmarked
instruction content, and other agent hooks.

The archive contains the complete `.etg` directory and an eject manifest. It
may contain private signing keys and local governance evidence, so treat it as
sensitive. Eject does not uninstall the global CLI or rewrite user-owned agent
configuration. Re-enroll a workspace with `etg init`; `etg resume` does not
restore an eject archive.
