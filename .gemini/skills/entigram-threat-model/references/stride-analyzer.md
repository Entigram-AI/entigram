# STRIDE Analyzer Reference & Checklist

## Objective

The `stride-analyzer` agent evaluates each architectural component and trust boundary against the six **STRIDE** threat categories:
- **S**poofing
- **T**ampering
- **R**epudiation
- **I**nformation Disclosure
- **D**enial of Service
- **E**levation of Privilege

---

## STRIDE Category Breakdown for Entigram Trust Boundaries

### 1. Spoofing (Identity & Authenticity)
- **Agent Registration Boundary:**
  - Forged agent identities during registration handshake.
  - Replay of valid registration payloads across workspaces.
  - Stolen capability tokens or registration certs.
- **MCP Serve Boundary:**
  - Unauthenticated tool execution requests posing as authorized callers.
  - Forged token claims injected into MCP tool request headers.

### 2. Tampering (Data & Policy Integrity)
- **Broker Decisions Boundary:**
  - Tampering with broker policy manifests or decision rules before evaluation.
  - In-flight mutation of capability token scopes or action parameters.
- **Assessment Adapters Boundary:**
  - Modification of adapter test inputs or metric evaluation results.
  - LDS schema or Turtle ontology tampering causing altered runtime execution paths.

### 3. Repudiation (Auditability & Accountability)
- **Broker Decisions Boundary:**
  - Unsigned broker decision logs permitting administrators or agents to deny taking actions.
  - Missing correlation IDs between request dispatch, broker evaluation, and tool execution.
- **Agent Registration Boundary:**
  - Incomplete audit logs of agent lifecycle events (registration, deregistration, scope elevation).

### 4. Information Disclosure (Confidentiality)
- **MCP Serve Boundary:**
  - Leakage of system context, API keys, or database credentials in MCP tool responses.
  - Unfiltered error stack traces returned to callers over MCP endpoints.
- **Assessment Adapters Boundary:**
  - Exposure of proprietary test benchmarks or sensitive telemetry payloads to unauthorized external adapters.

### 5. Denial of Service (Availability)
- **Agent Registration Boundary:**
  - Flood of agent registration requests causing CPU/memory exhaustion.
- **MCP Serve Boundary:**
  - Unthrottled tool execution requests exhausting downstream connection pools or LLM API quotas.
- **Broker Decisions Boundary:**
  - Complex nested policy loops causing infinite evaluation recursion.

### 6. Elevation of Privilege (Authorization & Boundaries)
- **Broker Decisions Boundary:**
  - Bypassing broker policy rules through parameter manipulation or rule logic flaws.
  - Escalating agent scope from read-only tools to system mutation capabilities.
- **Assessment Adapters Boundary:**
  - Sandbox escape by an assessment adapter executing arbitrary shell commands on host system.

---

## Threat Evaluation Matrix & Severity Guidelines

| Category | Example Threat | Default Severity | Key Verification Focus |
|----------|----------------|------------------|------------------------|
| **Spoofing** | Unverified agent identity during registration | **P1 High** | Signature validation, mTLS certs, token verification. |
| **Tampering** | Policy rule injection in broker decision engine | **P0 Critical** | Integrity checks, cryptographic signatures, schema constraints. |
| **Repudiation** | Missing audit logs for broker governance decisions | **P2 Medium** | Structured audit logging, append-only logs, correlation IDs. |
| **Information Disclosure** | Credential leak in MCP tool response | **P1 High** | Payload sanitization, output filtering, secret redaction. |
| **Denial of Service** | Unthrottled tool dispatch over MCP serve | **P2 Medium** | Rate limiting, request timeouts, resource limits. |
| **Elevation of Privilege** | Broker policy bypass via parameter manipulation | **P0 Critical** | Sentinel guard assertions, strict RBAC/ABAC enforcement. |

---

## Analysis Checklist

- [ ] Evaluated all 6 STRIDE categories against the Broker Decisions boundary
- [ ] Evaluated all 6 STRIDE categories against the Agent Registration boundary
- [ ] Evaluated all 6 STRIDE categories against the MCP Serve boundary
- [ ] Evaluated all 6 STRIDE categories against the Assessment Adapters boundary
- [ ] Checked for sandbox escape vectors in external adapter runners
- [ ] Validated confidence levels (suppress findings below 80% confidence)
