---
name: entigram-review
description: >
  Multi-agent workspace code review for Entigram projects. Launches 7 specialized
  agents in parallel (SOLID, Security, Performance, Error Handling, Boundaries,
  Model Integrity, Ontology Consistency) with P0-P3 severity scoring. Produces a
  local findings report — does NOT post to GitHub. Users who need PR-based reviews
  can use external tools (e.g. moltenbits/claude-review, aidankinzett/claude-git-pr-skill).
---

# Entigram Multi-Agent Code Review

## Overview

A **local workspace review** tool that launches 7 specialized agents in parallel to
analyze code quality, security, performance, error handling, boundary conditions,
schema integrity, and ontology consistency.

This skill produces a **local findings report** only. It does NOT interact with git,
GitHub, or any external service. Users who need PR-based reviews should use dedicated
PR review tools.

## When to Use

- "Review this code" / "review the workspace"
- "Run a code review" / "check for issues"
- Before opening a PR (pre-flight check)
- After making significant changes to assessment, schema, or ontology files
- As part of `etg assess` quality gates

## The 7 Specialized Agents

| Agent | Focus Area | Reference Checklist |
|-------|-----------|---------------------|
| **solid-reviewer** | SOLID Principles + Architecture | [solid-reviewer.md](references/solid-reviewer.md) |
| **security-reviewer** | Security Vulnerabilities (CWE-mapped) | [security-reviewer.md](references/security-reviewer.md) |
| **performance-reviewer** | Performance Issues | [performance-reviewer.md](references/performance-reviewer.md) |
| **error-handling-reviewer** | Error Handling | [error-handling-reviewer.md](references/error-handling-reviewer.md) |
| **boundary-reviewer** | Boundary Conditions | [boundary-reviewer.md](references/boundary-reviewer.md) |
| **model-reviewer** | LDS Schema & Model Integrity | [model-reviewer.md](references/model-reviewer.md) |
| **ontology-reviewer** | TTL/RDF/OWL Ontology Consistency | [ontology-reviewer.md](references/ontology-reviewer.md) |

## Severity Levels

All agents use a consistent **P0-P3 severity system**:

| Level | Name | Meaning | Confidence |
|-------|------|---------|------------|
| **P0** | Critical | Must fix — crashes, data loss, security flaw | 90-100% |
| **P1** | High | Should fix — bugs, uncaught errors, bad architecture | 80-100% |
| **P2** | Medium | Fix or track — code smells, coupling, missing guards | 80-100% |
| **P3** | Low | Optional — style, naming, minor improvements | 80-100% |

**Confidence threshold: 80+.** Suppress findings below 80% confidence.

## Workflow

### 1. Determine scope

Ask the user or infer what to review:
- **Specific files:** Review only the named files
- **A directory:** Review all source files under the directory
- **Changed files:** If the user says "review my changes", use `git diff --name-only` to scope
- **Full workspace:** Review all key source files

### 2. Launch 7 agents in parallel

Use `invoke_subagent` with the `research` type to launch all 7 agents concurrently.
Each agent receives:
- The list of files to review
- Instructions to read the files directly (NOT from a diff)
- The agent's specific reference checklist
- Instructions to output findings in the structured format below

### 3. Agent output format

Each agent must produce findings in this format:

```markdown
## [Agent Name] Review

### Critical (P0) - Must Fix
- **[file.py:42]** Issue description
  - Confidence: 95%
  - Fix: Suggested remediation

### High (P1) - Should Fix
...

### Medium (P2) - Fix or Follow-up
...

### Low (P3) - Optional
...
```

If no findings at a level, the agent should say "None found."

### 4. Consolidate findings

After all agents report back:
- Group findings by severity (P0 → P1 → P2 → P3)
- Maintain agent attribution: `(agent-name, severity, confidence%)`
- Deduplicate equivalent findings across agents
- Filter out findings below 80% confidence

### 5. Write local report

Write the consolidated report to an artifact file (`review.md`). Format:

```markdown
# Entigram Code Review

## Summary
| Severity | Count | Source Agents |
|----------|-------|---------------|
| P0 Critical | N | agent-names |
| P1 High | N | agent-names |
| P2 Medium | N | agent-names |
| P3 Low | N | agent-names |
| Clean | — | agent-names |

## 🔴 Critical (P0)
### From [Agent]
- **file:line** — description (agent, P0, confidence%)

## 🟠 High (P1)
...

## 🟡 Medium (P2)
...

## 🟢 Low (P3)
...

## Clean Agents
| Agent | Result |
|-------|--------|
| agent | ✅ No issues. Summary. |
```

### 6. Present to user

Show the user the findings summary and link to the full report artifact.
Do NOT post anything to GitHub or any external service.

## Entigram-Specific Agents

### Model Reviewer
Activated when the review scope includes any of:
- `*.lds` files (schema definitions)
- `entigram/schema_compiler/**`
- Migration files
- Python code that reads/writes/parses schemas

Checks LDS naming conventions, entity relationships, migration safety,
backward compatibility, and schema compiler integrity.

### Ontology Reviewer
Activated when the review scope includes any of:
- `*.ttl`, `*.rdf`, `*.owl` files
- `entigram/ontology_compiler/**`
- Python code that reads/writes/parses ontologies

Checks TTL syntax, namespace integrity, class hierarchy (DAG validation),
property domains/ranges, and deprecation annotations.

## What This Skill Does NOT Do

- ❌ Does NOT post reviews to GitHub
- ❌ Does NOT create pending reviews or PR comments
- ❌ Does NOT require `gh` CLI or any git tooling
- ❌ Does NOT send data to external services

Users who need PR-based reviews should use dedicated tools:
- [moltenbits/claude-review](https://github.com/moltenbits/claude-review) — multi-agent PR reviews
- [aidankinzett/claude-git-pr-skill](https://github.com/aidankinzett/claude-git-pr-skill) — single-agent PR reviews
