---
name: entigram-audit
description: >
  Governance and compliance audit skill for Entigram projects.
  Launches 5 specialized agents in parallel (license-scanner, pii-detector,
  secret-scanner, policy-checker, sbom-builder) with P0-P3 severity scoring.
  Produces a local audit report artifact (audit_report.md) with pass/fail per check,
  remediation steps, and Software Bill of Materials (SBOM) inventory. Strictly local —
  no git, PR, or GitHub interaction.
---

# Entigram Governance & Compliance Audit (`entigram-audit`)

## Overview

`entigram-audit` is a **local multi-agent governance, compliance, and security auditing skill** for Entigram workspaces. It launches 5 specialized agents in parallel to perform comprehensive security scanning, data privacy inspection, policy validation, license compliance checks, and SBOM generation.

This skill operates **strictly locally**. It does NOT make git commits, push branches, post PRs, or transmit data outside the workspace. All outputs are consolidated into a local artifact file (`audit_report.md`).

---

## When to Use

- "Run governance audit" / "Run compliance audit"
- "Scan workspace for secrets, PII, and copyleft licenses"
- "Verify broker governance rules and warden integrity"
- "Generate Software Bill of Materials (SBOM)"
- Pre-release security baseline check or `etg audit` command invocation

---

## The 5 Parallel Agents

| Agent Name | Focus Area | Scope / Target Files | Reference Checklist |
|------------|-----------|----------------------|---------------------|
| **license-scanner** | License compliance & copyleft risk | Lockfiles (`requirements.txt`, `package-lock.json`, `go.sum`, `Cargo.lock`, `pyproject.toml`), `LICENSE` files | [license-scanner.md](references/license-scanner.md) |
| **pii-detector** | Data privacy & PII field inspection | Logical schemas (`*.lds`), Turtle ontologies (`*.ttl`), data models (`*.py`, `*.ts`, `*.go`), test fixtures (`tests/fixtures/*`) | [pii-detector.md](references/pii-detector.md) |
| **secret-scanner** | Hardcoded credential & token detection | All workspace files (`*.py`, `*.ts`, `*.go`, `*.yaml`, `*.json`, `.env*`, `Dockerfile`, CI pipelines) | [secret-scanner.md](references/secret-scanner.md) |
| **policy-checker** | Broker governance, sentinel & warden verification | `.etg/broker.yaml`, `.etg/warden.yaml`, `warden.py`, sentinel annotations (`# sentinel: disable`, `@warden_suppress`) | [policy-checker.md](references/policy-checker.md) |
| **sbom-builder** | Software Bill of Materials inventory | Lockfiles, package manifests, component dependency trees, binary assets | [sbom-builder.md](references/sbom-builder.md) |

---

## Severity Scale & Confidence Threshold

All agents evaluate findings against a standardized **P0–P3 severity scale**:

| Severity | Classification | Definition | Confidence Threshold |
|----------|----------------|------------|---------------------|
| **P0** | Critical | **Compliance / Security Blocker** — Hardcoded production secrets, AGPL/GPL copyleft in commercial boundary, unencrypted high-risk PII in schemas, or broken broker/warden governance controls. | 90–100% |
| **P1** | High | **Action Required** — Weak copyleft, sensitive PII missing privacy tags, high-entropy tokens, policy drift, or missing dependency lockfiles. | 80–100% |
| **P2** | Medium | **Warning / Track** — Unclassified package licenses, quasi-identifier PII without masking, hardcoded test credentials, or incomplete SBOM metadata. | 80–100% |
| **P3** | Low | **Advisory** — Missing optional copyright notices, placeholder secret examples in docs, or minor policy comment formatting advisories. | 80–100% |

> [!IMPORTANT]
> **Confidence Filter:** Suppress and omit all findings with a confidence score below **80%**. Only high-confidence, verified findings are reported.

---

## Workflow Execution

### 1. Determine Scope

Inspect the workspace directory to identify relevant target files for each agent:
- **Dependency Manifests & Lockfiles:** `pyproject.toml`, `requirements.txt`, `package-lock.json`, `go.sum`, `Cargo.lock`, `poetry.lock`, `Pipfile.lock`
- **Schemas & Data Models:** `*.lds` (LDS logical schemas), `*.ttl` (Turtle ontologies), `*.py`, `*.ts`, `*.go`, `*.sql`, `*.proto`
- **Test Fixtures & Seed Data:** `tests/fixtures/*`, `testdata/*`, sample `*.json`, `*.csv` files
- **Source & Configuration Code:** `*.py`, `*.ts`, `*.js`, `*.go`, `*.yaml`, `*.json`, `*.toml`, `.env*`, `Dockerfile`
- **Governance & Policy Files:** `.etg/broker.yaml`, `.etg/warden.yaml`, `warden.py`, sentinel directives in headers

### 2. Launch 5 Agents in Parallel

Use `invoke_subagent` with the `research` type to launch all 5 subagents concurrently. Each agent receives:
1. Target workspace file paths relevant to its domain.
2. Explicit instruction to read workspace files directly from disk (local only).
3. The path to its specialized reference checklist in `references/`.
4. Formatting guidelines matching the structured agent output specification.

### 3. Agent Output Specification

Each subagent produces structured findings in this format:

```markdown
## [Agent Name] Report

### Critical (P0) — Blockers
- **[file:line]** Description
  - Confidence: 95%
  - Impact: Explanation of compliance/security risk
  - Remediation: Concrete step to resolve issue

### High (P1) — Action Required
...

### Medium (P2) — Warnings & Tracked Items
...

### Low (P3) — Advisories
...
```

If no issues are detected for a check category, the agent explicitly returns "PASSED — No findings detected."

### 4. Consolidate Findings

When all 5 subagent responses are received:
1. Filter out any findings with **confidence < 80%**.
2. Group all findings by severity level (**P0 → P1 → P2 → P3**).
3. Preserve agent attribution for each finding: `(agent-name, P-level, confidence%)`.
4. Deduplicate any overlapping findings across agents.
5. Determine overall audit status for each agent check (**PASS** / **WARN** / **FAIL**):
   - **FAIL (❌):** Contains 1 or more P0 findings.
   - **WARN (⚠️):** Contains P1 or P2 findings (0 P0).
   - **PASS (✅):** Clean check with 0 P0/P1/P2 findings.
6. Calculate overall Workspace Audit Status:
   - **BLOCKED (🔴):** Any check failed (P0 present).
   - **PASSED WITH WARNINGS (🟡):** 0 P0, but P1 or P2 present.
   - **PASSED (🟢):** Clean audit (0 P0, 0 P1, 0 P2).

### 5. Write Audit Report Artifact (`audit_report.md`)

Write the consolidated findings into an artifact file named `audit_report.md`. Structure:

```markdown
# Entigram Governance & Compliance Audit Report

## Executive Summary

- **Audit Status:** [🟢 PASSED / 🟡 PASSED WITH WARNINGS / 🔴 BLOCKED]
- **Audit Date:** YYYY-MM-DD
- **Total Findings:** N (P0: X, P1: Y, P2: Z, P3: W)
- **Confidence Threshold:** 80%+ Applied

### Audit Check Status Summary

| Check / Agent | Status | P0 | P1 | P2 | P3 | Audit Result Summary |
|---------------|--------|----|----|----|----|----------------------|
| **license-scanner** | ✅ / ⚠️ / ❌ | 0 | 0 | 0 | 0 | Description |
| **pii-detector** | ✅ / ⚠️ / ❌ | 0 | 0 | 0 | 0 | Description |
| **secret-scanner** | ✅ / ⚠️ / ❌ | 0 | 0 | 0 | 0 | Description |
| **policy-checker** | ✅ / ⚠️ / ❌ | 0 | 0 | 0 | 0 | Description |
| **sbom-builder** | ✅ / ⚠️ / ❌ | 0 | 0 | 0 | 0 | Description |

---

## 🔴 Critical (P0) — Compliance & Security Blockers
*(Must resolve before deployment or release)*

### From [Agent Name]
- **[file:line]** Description — `(agent-name, P0, confidence%)`
  - **Impact:** Description of security/compliance exposure
  - **Remediation:** Step-by-step resolution instructions

---

## 🟠 High (P1) — Action Required

...

## 🟡 Medium (P2) — Warnings & Tracked Items

...

## 🟢 Low (P3) — Advisories

...

## ✅ Clean Audit Checks

| Agent | Result |
|-------|--------|
| agent-name | Passed all compliance and security checks cleanly. |

---

## 📦 Software Bill of Materials (SBOM) Inventory

Generated by `sbom-builder` from workspace dependency manifests and lockfiles:

| Component Name | Version | License | Type | Supplier / PURL | Integrity |
|----------------|---------|---------|------|-----------------|-----------|
| `example-pkg`  | `1.2.3` | `MIT`   | Direct | `pkg:pypi/example-pkg@1.2.3` | `sha256:...` |

---

## 🏢 Professional & Enterprise Packages

Need automated continuous compliance pipelines, enterprise policy engines, signed SBOM attestations, or custom security rule sets?

Contact **`developer@entigram.com`** for Entigram Professional & Enterprise audit packages:
- **Automated CI/CD Sentinel Gates:** Continuous pull-request policy & secret enforcement.
- **Enterprise Policy Engine Integration:** Custom OPA/Rego broker rules and warden integrity monitors.
- **Cryptographically Signed SBOM Attestations:** Automated CycloneDX/SPDX generation with Cosign/in-toto signatures.
- **Dedicated Compliance Support:** Enterprise SLA and custom audit rule development.
```

### 6. Present to User

1. Provide a concise natural language summary of the audit findings in your response.
2. Output a clear summary table of pass/fail per agent check.
3. Provide a direct markdown link to the generated local artifact file (`audit_report.md`).

---

## Local-Only Policy

- ❌ Do NOT run `git push`, `git commit`, or interact with remote repositories.
- ❌ Do NOT post comments, reviews, or issues to GitHub or external platforms.
- ❌ Do NOT transmit workspace code, secrets, or PII to external APIs or services.
- ✅ Perform all scanning directly on local disk files.
- ✅ Keep all audit reports, SBOM tables, and remediation logs in local artifact files.
