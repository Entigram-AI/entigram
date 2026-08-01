# Report Writer Reference & Checklist

## Objective

The `report-writer` agent aggregates, normalizes, and synthesizes the outputs of `architecture-mapper`, `stride-analyzer`, `control-mapper`, and `gap-identifier` into a clear, structured **Entigram STRIDE Threat Model Report** artifact (`threat_model.md`).

---

## Required Artifact Document Structure

The final document must strictly follow this structure:

````markdown
# Entigram STRIDE Threat Model Report

## Executive Summary
- **Overall Threat Posture:** [CRITICAL / HIGH / MEDIUM / LOW]
- **Target Boundaries Assessed:** Broker Decisions, Agent Registration, MCP Serve, Assessment Adapters
- **Total Threats Identified:** N (P0: X, P1: Y, P2: Z, P3: W)
- **Security Control Coverage:** K% Mitigated

## System Architecture & Data Flow Diagram (DFD)

```mermaid
graph TD
    %% Mermaid Data Flow Diagram output from architecture-mapper
```

## Entigram Trust Boundary Assessment

| Boundary | Description | Key Components | Primary STRIDE Risks |
|----------|-------------|----------------|----------------------|
| Broker Decisions | Authorization & governance perimeter | Policy Engine, Sentinel Guard | Tampering, Elevation of Privilege |
| Agent Registration | Identity & auth perimeter | Registration Handler, Token Issuer | Spoofing, Denial of Service |
| MCP Serve | Model Context Protocol perimeter | MCP Endpoint, Parameter Validator | Information Disclosure, Tampering |
| Assessment Adapters | Test & metrics perimeter | Adapter Runner, Telemetry Store | Privilege Escalation, Tampering |

## STRIDE Threat Inventory & Control Mapping

| Threat ID | Trust Boundary | Affected Component | STRIDE Category | Threat Description | Inherent Severity | Existing Control | Mitigation Status |
|-----------|----------------|--------------------|-----------------|--------------------|-------------------|------------------|-------------------|
| T-001 | Broker Decisions | Policy Engine | Tampering | Unsigned policy manifest loading | P0 Critical | None | Unmitigated |
| T-002 | Agent Registration | Auth Handler | Spoofing | Missing token signature validation | P1 High | JWT Verifier | Mitigated |

## Gap Analysis & Prioritized Remediation Plan

### 🔴 Critical Security Gaps (P0)
*(Immediate blockers requiring emergency remediation)*

- **[T-001] Unsigned policy manifest loading**
  - **Boundary:** Broker Decisions
  - **Category:** Tampering / Elevation of Privilege
  - **Residual Risk:** P0 Critical (95% confidence)
  - **Impact:** Administrative override of broker decisions.
  - **Remediation:** Enforce cryptographic signature verification on rule manifests prior to evaluation.

### 🟠 High Priority Security Gaps (P1)
...

### 🟡 Medium Priority Security Gaps (P2)
...

### 🟢 Low Priority Security Gaps (P3)
...

## Security Control Coverage Matrix

| Control Area | Implemented Controls | Target Boundaries | Coverage Rating |
|--------------|----------------------|-------------------|-----------------|
| Authentication & Identity | Token verification, mTLS | Agent Registration, MCP Serve | 80% |
| Data Protection & Encryption | TLS 1.3, Secret redaction | Assessment Adapters, MCP Serve | 85% |
| Validation & Linting | LDS schema checks, Turtle ontology checks | MCP Serve, Broker Decisions | 75% |
| Sentinel Invariant Guards | Pre/post condition assertions | Broker Decisions | 90% |
| Broker Governance | Decision engine, RBAC rules | Broker Decisions | 85% |
````

---

## Synthesis Rules & Quality Control

1. **Strict Markdown Format:** Use clean GitHub Flavored Markdown.
2. **Mermaid Validation:** Verify that the Mermaid DFD syntax is valid and quote node labels containing special characters.
3. **No Duplicate Threats:** Ensure threat IDs (`T-001`, `T-002`, etc.) are unique and uniformly referenced in all tables.
4. **Confidence Filtering:** Confirm that all included findings meet the ≥80% confidence threshold.
5. **Entigram Focus:** Guarantee that findings highlight the 4 core trust boundaries (Broker Decisions, Agent Registration, MCP Serve, Assessment Adapters).
6. **Local-Only Notice:** Do NOT include any references to external git commits, PR links, or remote deployments.

---

## Output Delivery

Write the complete document to `<appDataDir>/brain/<conversation-id>/threat_model.md` using `write_to_file`.
