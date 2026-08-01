# Control Mapper Reference & Checklist

## Objective

The `control-mapper` agent identifies existing security controls across the Entigram codebase and maps them to the STRIDE threats identified by `stride-analyzer`.

---

## Core Entigram Security Control Categories

### 1. Authentication & Identity Controls
- **Agent Handshake Authentication:** Token signature verification (HMAC, JWT, public key cryptography).
- **Mutual TLS (mTLS):** Certificate validation for inter-service and agent communication.
- **Capability Tokens:** Short-lived tokens defining agent permissions and capabilities.

### 2. Encryption & Data Protection
- **Data in Transit:** TLS 1.3 encryption for MCP endpoints and adapter communication.
- **Data at Rest:** Encrypted telemetry datastores and credential stores.
- **Secret Redaction:** Automated masking of sensitive fields (tokens, passwords, keys) in logs and MCP output.

### 3. Input & Schema Validation
- **Logical Schema Definition (LDS) Validation:** Compiler-enforced entity syntax, field types, and relationship boundaries.
- **Turtle Ontology Validation:** RDF/OWL graph constraint checks prohibiting cyclic or invalid taxonomy structures.
- **MCP Payload Sanitization:** Strict JSON schema validation for all tool arguments.

### 4. Sentinel Invariant Guards
- **Policy Assertions:** Dynamic runtime checks verifying pre-conditions and post-conditions before executing actions.
- **Invariant Rules:** Security invariants preventing state changes that violate system policy.
- **Circuit Breakers:** Automated trip guards blocking suspicious action spikes.

### 5. Broker Governance Engine
- **Decision Engine:** Centralized policy evaluation engine enforcing authorization policies.
- **Role-Based & Attribute-Based Access Control (RBAC/ABAC):** Granular capability scoping per agent role.
- **Decision Logging:** Immutable audit logging of all granted or denied actions.

---

## Control Mapping Matrix Specification

For each identified threat, map the control using this structure:

| Threat ID | Targeted Threat | Control Category | Code Location / Mechanism | Mitigation Status |
|-----------|-----------------|------------------|---------------------------|-------------------|
| T-S01 | Agent Spoofing during Registration | Authentication | `src/auth/token_verifier.go` (JWT signature check) | **Mitigated** |
| T-T01 | Broker Policy Manifest Tampering | Broker Governance | `pkg/broker/policy_engine.go` (Manifest hashing) | **Partial** |
| T-R01 | Unsigned Decision Logs | Broker Governance | `pkg/broker/logger.go` (Standard log formatting) | **Unmitigated** |
| T-I01 | Credential Leakage in MCP Response | Validation & Redaction | `src/mcp/output_sanitizer.py` (Regex redaction) | **Mitigated** |
| T-D01 | MCP Tool Execution Flood | Rate Limiting | `src/mcp/rate_limiter.go` (Token bucket throttle) | **Mitigated** |
| T-E01 | Adapter Privilege Escalation | Sentinel Guards | `pkg/adapter/runner.go` (Process isolation) | **Partial** |

---

## Analysis Checklist

- [ ] Inspected codebase for auth middleware, token verification, and mTLS handlers
- [ ] Inspected codebase for payload encryption, secret redaction, and storage controls
- [ ] Inspected schema compilers (`*.lds`) and ontology linters (`*.ttl`) for validation controls
- [ ] Inspected Sentinel guard implementations for dynamic invariant enforcement
- [ ] Inspected Broker Governance decision rules and logging code
- [ ] Classified each control status as **Mitigated**, **Partial**, or **Unmitigated**
