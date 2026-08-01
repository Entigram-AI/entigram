---
name: entigram-hydrate
description: >
  Multi-agent workspace onboarding and compliance check for Entigram projects.
  Launches 5 specialized agents in parallel (dependency-auditor, config-validator,
  schema-linter, ontology-validator, posture-assessor) with P0-P3 severity scoring.
  Produces a local hydration health report artifact — does NOT post to GitHub.
---

# Entigram Multi-Agent Hydration & Compliance Check

## Overview

A **local workspace onboarding and compliance** tool that launches 5 specialized agents in parallel to inspect dependency security, configuration integrity, schema definitions, ontology consistency, and technology posture.

This skill produces a **local hydration health report** artifact only. It does NOT interact with git, GitHub, or any external service. All agents read workspace files directly from disk.

## When to Use

- "Hydrate workspace" / "run workspace onboarding"
- "Check workspace health" / "run hydration check"
- "Validate configuration and schemas" / "run etg hydrate"
- Onboarding a fresh Entigram project repository
- Pre-flight compliance audit before running production workloads

## The 5 Specialized Agents

| Agent | Focus Area | Scope / Target Files | Reference Checklist |
|-------|-----------|----------------------|---------------------|
| **dependency-auditor** | Security vulnerabilities & EOL packages | Lockfiles (`requirements.txt`, `package-lock.json`, `go.sum`, `Cargo.lock`, `pyproject.toml`) | [dependency-auditor.md](references/dependency-auditor.md) |
| **config-validator** | Config integrity & capability contracts | `.etg/entigram.yaml`, `entigram.yaml` | [config-validator.md](references/config-validator.md) |
| **schema-linter** | LDS schema syntax & entity models | `*.lds` files, schema compiler | [schema-linter.md](references/schema-linter.md) |
| **ontology-validator** | Turtle/OWL ontology syntax & taxonomy | `*.ttl` files, ontology compiler | [ontology-validator.md](references/ontology-validator.md) |
| **posture-assessor** | System posture & tech advisories | `etg assess` output, technology stack advisories | [posture-assessor.md](references/posture-assessor.md) |

## Severity Levels

All agents evaluate findings using a consistent **P0–P3 severity scale**:

| Level | Name | Meaning | Confidence |
|-------|------|---------|------------|
| **P0** | Critical | Hydration Blocker — invalid config, high CVEs, broken syntax causing compilation failures | 90–100% |
| **P1** | High | High Priority — dependency vulnerabilities, contract mismatches, schema/ontology errors | 80–100% |
| **P2** | Medium | Warning / Track — missing recommended fields, deprecated dependency/term usage | 80–100% |
| **P3** | Low | Advisory — minor style/formatting issues, non-critical posture suggestions | 80–100% |

**Confidence threshold: 80%+.** Suppress findings below 80% confidence.

## Workflow

### 1. Determine Scope

Inspect the current workspace directory to identify relevant target files:
- **Lockfiles:** Locate `requirements.txt`, `package-lock.json`, `go.sum`, `Cargo.lock`, `pyproject.toml`, `poetry.lock`, `Pipfile.lock`
- **Configuration:** Locate `.etg/entigram.yaml` or `entigram.yaml`
- **Schemas:** Find all `*.lds` logical schema definition files
- **Ontologies:** Find all `*.ttl` Turtle ontology definition files
- **Posture:** Determine posture assessment flags and `etg assess` execution parameters

### 2. Launch 5 Agents in Parallel

Use `invoke_subagent` with the `research` type to launch all 5 agents concurrently. Each agent receives:
- The list of relevant workspace files to inspect directly
- Instructions to read workspace files directly from disk (local only)
- The agent's specific reference checklist
- Instructions to format findings according to the structured agent output specification

### 3. Agent Output Specification

Each agent produces findings in this markdown structure:

```markdown
## [Agent Name] Report

### Critical (P0) - Hydration Blockers
- **[file:line or component]** Issue description
  - Confidence: 95%
  - Remediation: Suggested resolution

### High (P1) - Action Required
...

### Medium (P2) - Warnings & Deprecations
...

### Low (P3) - Advisories
...
```

If an agent finds no issues at a specific level, it reports "None found."

### 4. Consolidate Findings

Upon receiving responses from all 5 agents:
- Group findings strictly by severity level (P0 → P1 → P2 → P3)
- Retain agent attribution: `(agent-name, severity, confidence%)`
- Deduplicate overlapping findings across agents
- Suppress any findings with confidence < 80%
- Calculate overall workspace hydration health score and status

### 5. Write Hydration Health Report Artifact

Write the consolidated findings into an artifact file named `hydration_report.md`. Format:

```markdown
# Entigram Hydration Health Report

## Executive Summary
- **Hydration Status:** [PASSED / PASSED WITH WARNINGS / BLOCKED (P0 FOUND)]
- **Total Findings:** N (P0: X, P1: Y, P2: Z, P3: W)
- **Compliance Threshold:** 80%+ Confidence Applied

| Agent | Status | P0 | P1 | P2 | P3 | Summary |
|-------|--------|----|----|----|----|---------|
| dependency-auditor | ✅ / ⚠️ / ❌ | 0 | 0 | 1 | 0 | Description |
| config-validator | ✅ / ⚠️ / ❌ | 0 | 0 | 0 | 0 | Description |
| schema-linter | ✅ / ⚠️ / ❌ | 0 | 0 | 0 | 0 | Description |
| ontology-validator | ✅ / ⚠️ / ❌ | 0 | 0 | 0 | 0 | Description |
| posture-assessor | ✅ / ⚠️ / ❌ | 0 | 0 | 0 | 0 | Description |

## 🔴 Critical (P0) — Hydration Blockers
*(Must resolve before proceeding)*
### From [Agent Name]
- **[file:line]** Description — `(agent-name, P0, confidence%)`
  - **Remediation:** Resolution instructions

## 🟠 High (P1) — Action Required
...

## 🟡 Medium (P2) — Warnings & Deprecations
...

## 🟢 Low (P3) — Advisories
...

## Clean Agents
| Agent | Result |
|-------|--------|
| agent-name | ✅ Passed all checks cleanly. |
```

### 6. Present to User

Present a concise natural language summary of the hydration report to the user and provide a link to the `hydration_report.md` artifact file. Do NOT perform git operations, create PRs, or send data outside the local system.

## Detailed Agent Responsibilities

### 1. dependency-auditor
Scans workspace lockfiles (`requirements.txt`, `package-lock.json`, `go.sum`, `Cargo.lock`, `pyproject.toml`) directly. Detects known security vulnerabilities (CVEs), hijacked packages, EOL dependencies, and non-deterministic version declarations.

### 2. config-validator
Validates `.etg/entigram.yaml` structure, required metadata fields (`version`, `workspace_id`, `capabilities`), capability contract syntax, and schema compatibility. Ensures environment variables and contract signatures are valid.

### 3. schema-linter
Runs the Entigram schema compiler linter against all `*.lds` files. Verifies entity definition standards, primary keys, relationship references, naming conventions (PascalCase entities, snake_case fields), and backward-compatibility rules.

### 4. ontology-validator
Runs the Entigram ontology compiler against all `*.ttl` Turtle files. Verifies RDF/OWL syntax, namespace declarations (`@prefix`), class taxomic DAGs (detects circular subClassOf loops), and property domain/range constraints.

### 5. posture-assessor
Runs or simulates `etg assess` to evaluate the overall security posture and technical health of the workspace. Surfaces technology advisories, framework deprecations, hardcoded secrets, and compliance policy violations.

## Local-Only Constraints

- ❌ Does NOT interact with git or run git commands
- ❌ Does NOT post reviews or comments to GitHub
- ❌ Does NOT require external network access or GitHub CLI (`gh`)
- ❌ Reads files directly from the local workspace directory
