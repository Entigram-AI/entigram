# Agent Orchestration

Entigram records empirical agent capability profiles and uses them to route work
by task risk. This keeps weaker continuation agents useful for safe work while
blocking them from high-risk changes.

## Risk Levels

- `read_only`: inspect files, summarize state, run non-mutating diagnostics
- `low_risk`: docs, formatting, focused tests
- `medium_risk`: isolated implementation changes
- `high_risk`: schema changes, migrations, releases, infrastructure changes
- `critical`: secrets, git history rewriting, branch protection, production deploys

## Register Agents

```bash
etg broker agent-register \
  --agent codex-strong \
  --class strong \
  --score 0.90 \
  --capability schema_change=0.90 \
  --capability release=0.85
```

## Queue And Assign Tasks

```bash
etg broker task-enqueue \
  --id schema-task \
  --title "Update governed schema" \
  --type schema_change \
  --risk high_risk

etg broker task-assign --id schema-task --agent codex-strong
```

Assignments are rejected when the agent's score for the task type is below the
task's required score. High-risk and critical rejections are recorded as
conflicts for human resolution.

## Hibernate Near Token Limits

Agents cannot wake themselves after a token refresh window. Entigram persists
the checkpoint; an external scheduler or the next invoked agent resumes from it.

```bash
etg broker hibernate \
  --agent codex-strong \
  --remaining-tokens 900 \
  --threshold 1200 \
  --resume-after 2026-07-05T18:05:00-05:00 \
  --summary "Focused tests pass; handoff remains." \
  --next-action "Run make handoff." \
  --pending-task schema-task

etg broker resume --agent codex-strong
```

Near the halt window, agents should stop starting risky work and only checkpoint,
summarize, run read-only validation, or hibernate.

## Workspace Lifecycle Is Separate

`etg pause` and `etg resume` compact or restore Entigram governance for the
whole workspace. They do not hibernate or resume an individual agent.

Use broker hibernation for continuity across agent token/time limits. Use
workspace pause when the operator intentionally wants Entigram context and
governance gates disabled until a later session.
