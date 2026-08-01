---
name: entigram-threat-model
description: >
  STRIDE threat modeling tool for Entigram projects.
  Launches 5 specialized agents in parallel (architecture-mapper, stride-analyzer,
  control-mapper, gap-identifier, report-writer) to map system architecture (Mermaid DFD),
  analyze STRIDE threats across Entigram trust boundaries (broker decisions, agent registration,
  MCP serve, assessment adapters), map security controls, identify unmitigated gaps, and write a
  structured threat model artifact.
---

# Entigram STRIDE Threat Model Generator

## Overview

A **local multi-agent security threat modeling** tool that analyzes Entigram architectures using the **STRIDE** methodology (Spoofing, Tampering, Repudiation, Information Disclosure, Denial of Service, Elevation of Privilege).

It launches 5 specialized agents concurrently to inspect schema definitions (`*.lds`), ontology graphs (`*.ttl`), configuration manifests (`.etg/entigram.yaml`), and codebase implementation files (`src/`, `lib/`, `pkg/`).

This skill produces a comprehensive **threat model artifact** (`threat_model.md`) containing:
1. A **Mermaid Data Flow Diagram (DFD)** highlighting components and trust boundaries.
2. A detailed **STRIDE Threat Inventory Table**.
3. A **Security Control Mapping** (auth, encryption, validation, sentinel, broker governance).
4. A **Gap Analysis & Risk Remediation Plan** with P0–P3 severity scoring.

All operations are strictly **local-only**. No git commands, external API requests, or GitHub interactions are performed.

---

## When to Use

- "Run threat model" / "generate STRIDE threat model"
- "Analyze security threats in entigram" / "run entigram threat model"
- "Audit trust boundaries" / "threat model broker decisions and MCP serve"
- Pre-deployment security assessments for new Entigram services or capability additions
- Security posture verification for agent registration and assessment adapter interfaces

---

## Entigram Core Trust Boundaries

The threat model explicitly focuses on four critical Entigram trust boundaries:

| Trust Boundary | Description | Key Assets & Vectors |
|----------------|-------------|----------------------|
| **Broker Decisions** | Policy evaluation and action authorization boundary between incoming request payloads and system execution engine. | Decision rules, capability tokens, action dispatch, policy evaluation engine. |
| **Agent Registration** | Identity, authentication, and handshake boundary for new or joining agents. | Agent identity certs/tokens, registration payloads, scope metadata, capability claims. |
| **MCP Serve** | Model Context Protocol boundary exposing tool execution and context endpoints to external agents/LLMs. | Tool dispatchers, prompt context injection, parameter validation, schema serialization. |
| **Assessment Adapters** | Evaluation harness and plugin execution boundary interfacing with external metrics or test environments. | Adapter sandboxing, telemetry collection, test execution contexts, adapter result payloads. |

---

## The 5 Parallel Subagents

| Subagent | Role & Focus | Scope / Input Files | Reference Guide |
|----------|--------------|---------------------|-----------------|
| **architecture-mapper** | Extracts components, processes, data stores, external entities, and trust boundaries; constructs Mermaid DFD. | `*.lds`, `*.ttl`, `.etg/entigram.yaml`, `src/`, `lib/`, `pkg/` | [architecture-mapper.md](references/architecture-mapper.md) |
| **stride-analyzer** | Applies STRIDE threat categories to each component and trust boundary identified in the DFD. | Mermaid DFD, component definitions, trust boundary definitions | [stride-analyzer.md](references/stride-analyzer.md) |
| **control-mapper** | Maps existing security controls (auth, encryption, validation, sentinel, broker governance) to identified threats. | Security middleware, auth modules, schema validators, sentinel policies, broker guards | [control-mapper.md](references/control-mapper.md) |
| **gap-identifier** | Identifies unmitigated STRIDE threats, calculates residual risk, and formulates prioritized control recommendations. | Threat-Control Matrix, security coverage maps | [gap-identifier.md](references/gap-identifier.md) |
| **report-writer** | Synthesizes subagent findings into a final structured Threat Model Document artifact (`threat_model.md`). | Output reports from agents 1–4 | [report-writer.md](references/report-writer.md) |

---

## Severity Ratings & Thresholds

All threats and security gaps are assigned a severity rating based on impact and likelihood:

| Level | Name | Impact Criteria | Confidence Threshold |
|-------|------|-----------------|----------------------|
| **P0** | Critical | Unmitigated elevation of privilege, broker governance bypass, unauthorized agent execution, remote code execution. | 80%–100% |
| **P1** | High | Unauthenticated agent spoofing across trust boundary, unencrypted sensitive payload transmission, unvalidated MCP parameter injection. | 80%–100% |
| **P2** | Medium | Missing repudiation audit trails for broker decisions, unthrottled MCP tool invocation (Denial of Service risk). | 80%–100% |
| **P3** | Low | Minor information disclosure in verbose telemetry logs, sub-optimal secret storage formatting. | 80%–100% |

> [!NOTE]
> Findings with confidence scores below 80% are suppressed to minimize false positives.

---

## Execution Workflow

### Step 1: Architectural Discovery & Target Inspection

Scan the target workspace to discover structural and code files:
- **Schemas:** Scan for `*.lds` (Logical Schema Definitions).
- **Ontologies:** Scan for `*.ttl` (Turtle/OWL ontology files).
- **Configuration:** Inspect `.etg/entigram.yaml` and `entigram.yaml`.
- **Codebase:** Locate implementation files for broker decisions, agent registration, MCP serve endpoints, and assessment adapters under `src/`, `lib/`, `pkg/`, or `cmd/`.

### Step 2: Concurrent Subagent Execution

Launch all 5 agents in parallel using `invoke_subagent` with `type: "research"`. Pass the workspace file list and target trust boundaries to each subagent along with their reference checklists:

1. `architecture-mapper`: Analyzes files to identify Data Flow Diagram elements (Processes, Data Stores, External Entities, Trust Boundaries) and builds a Mermaid DFD.
2. `stride-analyzer`: Evaluates the Mermaid DFD and code pathways against Spoofing, Tampering, Repudiation, Information Disclosure, Denial of Service, and Elevation of Privilege.
3. `control-mapper`: Scans the codebase for security controls:
   - **Authentication:** Token verification, mTLS, registration signatures.
   - **Encryption:** TLS in transit, secret management, payload encryption.
   - **Validation:** LDS schema enforcement, Turtle ontology checks, parameter sanitization.
   - **Sentinel:** Invariant checking, sentinel policy assertions.
   - **Broker Governance:** Action authorization, capability token scoping, decision engines.
4. `gap-identifier`: Cross-references identified STRIDE threats against existing controls to detect unmitigated gaps and generate actionable recommendations.
5. `report-writer`: Collects, validates, and synthesizes outputs into a unified document structure.

### Step 3: Subagent Output Specifications

Each agent must output markdown adhering to a standardized format:

```markdown
## [Subagent Name] Report

### Critical (P0) Findings
- **[Component / Boundary]** Description of finding
  - STRIDE Category: [Spoofing/Tampering/etc.]
  - Confidence: 90%
  - Affected Asset: [e.g. Broker Decision Engine]

### High (P1) Findings
...

### Medium (P2) Findings
...

### Low (P3) Findings
...
```

If a subagent finds no issues for a severity level, it explicitly records "None identified."

### Step 4: Consolidation & Matrix Reconciliation

Upon receiving all agent outputs:
1. Reconcile the Mermaid DFD from `architecture-mapper` with threats from `stride-analyzer`.
2. Cross-reference threat IDs across `control-mapper` and `gap-identifier`.
3. Filter out any findings with confidence < 80%.
4. Group all findings by severity (P0 → P1 → P2 → P3).

### Step 5: Generate Threat Model Artifact

Write the finalized report to `threat_model.md` in the active artifact directory (`<appDataDir>/brain/<conversation-id>/threat_model.md`).

The document must strictly follow this structure:

````markdown
# Entigram STRIDE Threat Model Report

## Executive Summary
- **Overall Risk Rating:** [CRITICAL / HIGH / MEDIUM / LOW]
- **Analyzed Trust Boundaries:** Broker Decisions, Agent Registration, MCP Serve, Assessment Adapters
- **Total STRIDE Threats Identified:** N (P0: X, P1: Y, P2: Z, P3: W)
- **Mitigated Threats:** M / N (Coverage: K%)

## System Data Flow Diagram (DFD)

```mermaid
graph TD
    %% Mermaid Data Flow Diagram representation of components and trust boundaries
```

## Entigram Trust Boundary Summary

| Boundary | Components Involved | Primary Assets | Key STRIDE Risks |
|----------|---------------------|----------------|------------------|
| Broker Decisions | ... | ... | ... |
| Agent Registration | ... | ... | ... |
| MCP Serve | ... | ... | ... |
| Assessment Adapters | ... | ... | ... |

## STRIDE Threat Inventory & Control Mapping

| Threat ID | Boundary | Component | STRIDE Category | Threat Description | Severity | Existing Control | Mitigation Status |
|-----------|----------|-----------|-----------------|--------------------|----------|------------------|-------------------|
| T-001 | Broker Decisions | Policy Engine | Tampering | Rule injection via unvalidated manifest | P0 | Sentinel Policy Guard | Partial |
| T-002 | Agent Registration | Auth Endpoint | Spoofing | Unsigned registration payload | P1 | Token Validator | Mitigated |

## Detailed Gap Analysis & Remediation Plan

### 🔴 Critical Gaps (P0)
- **[T-001] Rule injection via unvalidated manifest**
  - **Trust Boundary:** Broker Decisions
  - **STRIDE Category:** Tampering / Elevation of Privilege
  - **Existing Control:** Sentinel Policy Guard (Partial)
  - **Residual Risk:** High risk of unauthorized policy override.
  - **Recommended Mitigation:** Enforce cryptographic signature verification on rule manifests prior to broker evaluation.

### 🟠 High Priority Gaps (P1)
...

### 🟡 Medium Priority Gaps (P2)
...

### 🟢 Low Priority Gaps (P3)
...

## Security Control Coverage Matrix

| Control Category | Implemented Controls | Target Boundaries | Coverage Score |
|------------------|----------------------|-------------------|----------------|
| Authentication & Identity | ... | Agent Registration, MCP Serve | 85% |
| Encryption & Data Protection | ... | Assessment Adapters | 90% |
| Input & Schema Validation | ... | MCP Serve, Broker Decisions | 75% |
| Sentinel Invariant Guards | ... | Broker Decisions | 95% |
| Broker Governance Engine | ... | Broker Decisions | 90% |
````

### Step 6: User Presentation

Provide a concise natural language summary of the findings to the user and supply a clickable markdown link to the `threat_model.md` artifact.

---

## Local-Only Security & Operational Rules

- ❌ Do NOT run git commands (`git diff`, `git commit`, `git push`).
- ❌ Do NOT make external API calls or attempt GitHub PR generation.
- ❌ Do NOT transmit workspace code or telemetry outside the local file system.
- ✅ Read code, schema (`*.lds`), and ontology (`*.ttl`) files directly from local disk.
