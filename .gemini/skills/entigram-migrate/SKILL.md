---
name: entigram-migrate
description: >
  Plans safe schema and ontology migrations for Entigram projects. Automatically detects when
  *.lds (LDS schema format) or *.ttl (TTL ontology format) files are modified or draft schemas
  are created. Launches 4 specialized subagents in parallel (diff-analyzer, migration-planner,
  backward-compat-checker, rollback-planner) to produce a comprehensive, local migration plan
  artifact with executable data transformation scripts, risk assessments, breaking change lists,
  compatibility reports, and rollback scripts — strictly local, no git remote or PR interaction.
---

# Entigram Schema & Ontology Migration Planner (`entigram-migrate`)

## Overview

`entigram-migrate` is a **local multi-agent migration planning skill** for Entigram workspaces.
When LDS schema files (`*.lds`) or TTL ontology files (`*.ttl`) undergo changes, this skill launches 4 specialized agents in parallel to analyze diffs, generate data transformation scripts, verify backward compatibility across graph builders and compilers, and construct robust rollback procedures.

This tool operates **strictly locally**. It does NOT make remote git calls, push branches, or post PRs to GitHub. All outputs are written to a local artifact file (`migration_plan.md`).

---

## When to Use & Automatic Triggers

### Triggers
- Automatic detection when `*.lds` or `*.ttl` files are created, modified, or staged in the workspace (e.g. `draft_schema.lds` vs `schema.lds`).
- Explicit user commands such as:
  - *"Plan schema migration"* / *"Plan ontology migration"*
  - *"Check breaking changes in schema.lds"*
  - *"Migrate from draft_schema.lds to schema.lds"*
  - *"Generate rollback scripts for ontology change"*

---

## The 4 Parallel Agents

| Agent Name | Primary Focus | Key Outputs | Reference Checklist |
|------------|---------------|-------------|---------------------|
| **diff-analyzer** | Schema & Ontology Diff Analysis | Breaking change catalog (removed fields, renamed entities, cardinality shifts, TTL domain/range changes), risk scoring (P0–P3). | [diff-analyzer.md](references/diff-analyzer.md) |
| **migration-planner** | Up-Migration Data Scripts | Data transformation scripts (Python, SQL for SQLite ledger, SPARQL for RDF graph store), field default generators, type conversion logic. | [migration-planner.md](references/migration-planner.md) |
| **backward-compat-checker** | System & Query Verification | Audit of existing queries, consumers, `entigram.schema_compiler`, `entigram.ontology_compiler`, and Graph Builder edge constructors. | [backward-compat-checker.md](references/backward-compat-checker.md) |
| **rollback-planner** | Failure Recovery & Down-Migration | Executable rollback scripts (down-migration SQL/SPARQL/Python), pre-migration snapshot instructions, failure abort sequences. | [rollback-planner.md](references/rollback-planner.md) |

---

## Entigram Formats Focus

### 1. LDS Schema Format (`*.lds`)
Entigram Local Data Schema format structure:
```lds
ENTITY: Strategic_Goal
ATTRIBUTES:
  - id (UUID, PK)
  - statement (String)
  - target_date (Date)
  - priority (Integer)

RELATIONSHIP: Strategic_Goal (1) [MUST] --- [MAY] (MANY) KPI
```

### 2. TTL Ontology Format (`*.ttl`)
W3C Turtle RDF/OWL ontology format structure:
```turtle
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix mk: <http://entigram.ai/ontology/custom#> .

mk:Strategic_Goal a owl:Class ;
    rdfs:label "Strategic_Goal" .

mk:Strategic_Goal_statement a owl:DatatypeProperty ;
    rdfs:domain mk:Strategic_Goal ;
    rdfs:range xsd:string .

mk:relates_Strategic_Goal_to_KPI a owl:ObjectProperty ;
    rdfs:domain mk:Strategic_Goal ;
    rdfs:range mk:KPI .
```

---

## Workflow Execution

### Step 1: Detect Scope & Identify Schema Versions
Determine the source (old) and target (new) files:
- **LDS files:** e.g., `schema.lds` (current) vs `draft_schema.lds` (proposed), or `git diff` / modified lines in `*.lds`.
- **TTL files:** e.g., `schema.ttl` (current) vs `draft_schema.ttl` (proposed), or modified lines in `*.ttl`.

### Step 2: Launch 4 Subagents in Parallel
Invoke all 4 subagents concurrently using `invoke_subagent` with the `research` subagent type:

1. **diff-analyzer**: Pass old vs new `.lds` and `.ttl` content. Request P0–P3 breaking change detection against [diff-analyzer.md](references/diff-analyzer.md).
2. **migration-planner**: Pass schema diff summary. Request up-migration scripts (SQLite ledger SQL/Python + SPARQL graph update) against [migration-planner.md](references/migration-planner.md).
3. **backward-compat-checker**: Pass target schema/ontology files and workspace paths. Request compiler check (`entigram.schema_compiler` / `entigram.ontology_compiler`), Graph Builder edge check, and consumer query audit against [backward-compat-checker.md](references/backward-compat-checker.md).
4. **rollback-planner**: Pass schema diff summary and migration plan. Request down-migration scripts and backup recovery steps against [rollback-planner.md](references/rollback-planner.md).

### Step 3: Consolidate Results into Migration Plan Artifact
Synthesize all 4 subagent outputs into a comprehensive artifact file named `migration_plan.md`.

Structure of `migration_plan.md`:

```markdown
# Entigram Schema & Ontology Migration Plan

## 📊 Executive Summary & Risk Assessment
- **Overall Migration Risk**: 🔴 CRITICAL / 🟠 HIGH / 🟡 MEDIUM / 🟢 LOW
- **Target Files**: `schema.lds`, `schema.ttl`
- **Breaking Changes Count**: N
- **Backward Compatibility**: PASS / WARN / FAIL

---

## 🚨 Breaking Changes & Diff Analysis (diff-analyzer)
| Target File | Change Type | Element | Description | Severity |
|-------------|-------------|---------|-------------|----------|
| `schema.lds` | Removed Field | `Entity.attribute` | Field deleted | P0 |

---

## ⚡ Up-Migration Scripts (migration-planner)

### SQLite Ledger Database Migration (Python / SQL)
```python
# Executable Python script for SQLite ledger data transformation
```

### TTL / RDF Graph Store Migration (SPARQL Update)
```sparql
# Executable SPARQL update query
```

---

## 🛡️ Backward Compatibility Report (backward-compat-checker)
- **LDS Schema Compiler**: ✅ PASS
- **TTL Ontology Compiler**: ✅ PASS
- **Graph Builder Integrations**: ⚠️ WARN (2 edge signatures need update)
- **Downstream Queries**: Audit findings & affected query files.

---

## 🔄 Rollback Plan & Down-Migration Scripts (rollback-planner)

### Pre-Migration Snapshot Checklist
1. Backup SQLite database file.
2. Snapshot `schema.lds` and `schema.ttl`.

### Down-Migration Script (Python / SQL / SPARQL)
```python
# Executable Python script for rolling back data changes
```
```

### Step 4: Present to User
- Provide a clear, concise summary of the migration risk and breaking changes in your response.
- Provide a direct link to the generated local artifact file (`migration_plan.md`).
- Prompt the user to confirm execution of up-migration scripts when ready.

---

## Local-Only Policy

- ❌ Do NOT execute `git push` or interact with GitHub APIs.
- ❌ Do NOT attempt to create GitHub Pull Requests or comments.
- ❌ Do NOT invoke external webhooks or remote CI services.
- ✅ Keep all migration plans, scripts, and logs in local artifact files.
