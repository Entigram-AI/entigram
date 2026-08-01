---
name: github-pr-review
description: >
  Review a GitHub PR and create a pending (draft) review using multi-agent analysis.
  Launches 7 specialized agents in parallel (SOLID, Security, Performance, Error Handling,
  Boundaries, Model Integrity, Ontology Consistency) with P0-P3 severity scoring.
  The draft review is only visible to you until submitted from the GitHub UI.
---

# GitHub PR Review

## Overview

Review a GitHub pull request and create a **pending (draft) review** using `gh api`.
The draft review is only visible to the authenticated user until they submit it from
the GitHub UI, where they can edit comments and choose the event type.

**Multi-Agent Review:** Launch 7 specialized agents in parallel for comprehensive PR
analysis with consistent P0-P3 severity labeling.

## Prerequisites

**Check gh CLI is installed before starting:**

```bash
gh --version
```

If not installed, stop and tell the user:
```
The GitHub CLI (gh) is required. Install: brew install gh && gh auth login
```

## PR Resolution

1. If a PR number is provided — use it directly.
2. If no PR number — resolve from the current branch:
   ```bash
   gh pr view --json number --jq '.number'
   ```

## The 7 Specialized Agents

| Agent | Focus Area | Examples |
|-------|-----------|----------|
| **solid-reviewer** | SOLID Principles + Architecture | SRP violations, god classes, OCP violations |
| **security-reviewer** | Security Vulnerabilities | SQL injection, XSS, IDOR, hardcoded secrets, CWE refs |
| **performance-reviewer** | Performance Issues | N+1 queries, O(n²) algorithms, unbounded alloc |
| **error-handling-reviewer** | Error Handling | Swallowed exceptions, missing boundaries, bare excepts |
| **boundary-reviewer** | Boundary Conditions | Null deref, empty arrays, off-by-one, TOCTOU |
| **model-reviewer** | Schema & Model Integrity | LDS schema changes, entity relationships, migration safety |
| **ontology-reviewer** | Ontology Consistency | TTL/RDF changes, namespace integrity, class hierarchy |

### Model Reviewer (Entigram-specific)

This agent reviews changes to schema and model files:
- **Files to watch:** `*.lds`, `schema.lds`, `entigram/schema_compiler/**`, migration files
- **Checks:**
  - Entity definitions follow LDS naming conventions (PascalCase entities, snake_case fields)
  - Relationship cardinality is explicit and valid
  - Required fields have defaults or are non-nullable
  - Schema changes are backward-compatible (no dropped required fields without migration)
  - Foreign key references point to existing entities
  - Enum values are not removed (only appended) unless a migration exists
  - The schema compiler (`entigram/schema_compiler/`) can still parse the changed schema
  - Graph builder relationships match declared entity edges
  - No orphaned entities (defined but never referenced)

### Ontology Reviewer (Entigram-specific)

This agent reviews changes to ontology and taxonomy files:
- **Files to watch:** `*.ttl`, `*.rdf`, `*.owl`, `entigram/ontology_compiler/**`
- **Checks:**
  - TTL syntax is valid (proper prefix declarations, semicolons, periods)
  - Namespace URIs are consistent and don't conflict
  - Class hierarchy (`rdfs:subClassOf`) forms a valid DAG (no cycles)
  - Property domains and ranges reference defined classes
  - `owl:equivalentClass` and `owl:sameAs` are used correctly
  - Ontology compiler (`entigram/ontology_compiler/`) can still process changes
  - No dangling references (properties referencing undefined classes)
  - Labels and comments are present for new classes/properties
  - Deprecation annotations are used instead of deletion

## Severity Levels

All agents use a consistent **P0-P3 severity system**:

| Level | Name | Action | Confidence |
|-------|------|--------|------------|
| **P0** | Critical | Must fix, blocks merge | 90-100% |
| **P1** | High | Should fix before merge | 80-100% |
| **P2** | Medium | Fix or create follow-up | 80-100% |
| **P3** | Low | Optional improvement | 80-100% |

**Confidence threshold: 80+.** Findings below 80% confidence are suppressed.

## Suggested Event Type

```
if (any P0 or P1 findings) → REQUEST_CHANGES
else if (any P2 findings)  → COMMENT
else if (any P3 findings)  → APPROVE with notes
else                       → APPROVE (clean PR)
```

The `event` field is **never** included in the JSON payload. The review is always
created as PENDING. The suggested event type is shown in the `.md` summary only.

## Workflow

**REQUIRED STEPS (do not skip any):**

### 1. Get PR details

```bash
# Metadata
gh pr view <PR_NUMBER> --json title,body,author,baseRefName,headRefName,commits

# Latest commit SHA
COMMIT_SHA=$(gh pr view <PR_NUMBER> --json commits --jq '.commits[-1].oid')

# Full diff
gh pr diff <PR_NUMBER>

# Changed files list
gh pr diff <PR_NUMBER> --stat
```

### 2. Launch 7 agents in parallel

Each agent receives the diff and changed file list. Each produces structured findings:

```markdown
## [Agent Name] Review

### Critical (P0) - Must Fix
- **[File:Line]** Issue description
  - Confidence: 95
  - Fix: [Suggestion]

### High (P1) - Should Fix
...
```

Use `invoke_subagent` to launch all 7 agents concurrently with the `research` type.
Each agent's prompt should include:
- The full diff
- The list of changed files
- The agent's specific checklist (from references below)
- Instructions to output findings in the structured format above

### 3. Consolidate findings

Merge all agent outputs:
- Group by severity (P0 → P1 → P2 → P3)
- Maintain agent attribution for each finding
- Deduplicate equivalent findings across agents
- Filter out findings below 80% confidence

### 4. Write output files

Write two files:
- `/tmp/pr-review-<PR_NUMBER>.json` — API payload (no `event` field)
- `/tmp/pr-review-<PR_NUMBER>.md` — human-readable summary

### 5. Post the pending review

**⚠️ CRITICAL: Use JSON payload with `--input`, NOT `-f` array syntax.**

The `-f` flag breaks markdown rendering in code suggestions. Always use:

```bash
# Write JSON payload (no "event" field = PENDING review)
cat > /tmp/pr-review-<PR_NUMBER>.json <<'EOF'
{
  "commit_id": "<COMMIT_SHA>",
  "body": "Multi-agent review: 7 specialized reviewers analyzed this PR.",
  "comments": [
    {
      "path": "src/file.py",
      "line": 42,
      "body": "**[P1 · security-reviewer · 95%]** Potential injection\n\n```suggestion\ncursor.execute(\"SELECT * FROM t WHERE id = ?\", (uid,))\n```"
    }
  ]
}
EOF

# Post as pending review
gh api repos/{owner}/{repo}/pulls/<PR_NUMBER>/reviews --input /tmp/pr-review-<PR_NUMBER>.json
```

### Why JSON over -f syntax

| Aspect | `-f` Array Syntax | JSON `--input` |
|--------|-------------------|----------------|
| Markdown rendering | ❌ Breaks backticks/newlines | ✅ Works correctly |
| Type handling | ❌ Numbers become strings | ✅ Types preserved |
| Multiple comments | ❌ Fragile mixed flags | ✅ Reliable |
| Reusable | ❌ Must recreate | ✅ Save and re-post |

## Agent Attribution Format

Every comment includes attribution:

```
**[P1 · security-reviewer · 95%]** Description of the issue
```

This tells the reviewer:
- **Severity** (P0-P3)
- **Which agent** found it
- **Confidence** percentage

## Offline Mode

If the user requests offline mode or you want to let them review first:
- Write the JSON and MD files but do NOT post
- Tell the user where the files are
- They can review and post later
