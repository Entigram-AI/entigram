# Gap Identifier Reference & Checklist

## Objective

The `gap-identifier` agent evaluates unmitigated and partially mitigated STRIDE threats, calculates residual risk levels, and formulates prioritized security control recommendations.

---

## Gap Evaluation & Residual Risk Scoring

Residual risk is determined by combining the inherent threat severity with the effectiveness of existing security controls:

| Inherent Severity | Existing Control Status | Residual Risk Score | Action Required |
|-------------------|-------------------------|---------------------|-----------------|
| **P0 Critical** | Unmitigated | **P0 Critical** | Immediate blocker — emergency control needed before deployment |
| **P0 Critical** | Partial | **P1 High** | High priority — harden existing control to full mitigation |
| **P1 High** | Unmitigated | **P1 High** | High priority — implement primary control |
| **P1 High** | Partial | **P2 Medium** | Medium priority — improve validation / logging |
| **P2 Medium** | Unmitigated | **P2 Medium** | Medium priority — schedule remediation |
| **P3 Low** | Unmitigated / Partial | **P3 Low** | Low priority — advisory improvement |

---

## Focus Areas for Entigram Gap Analysis

### 1. Broker Decision Gaps
- **Missing Signature Verification:** Can rule manifests be injected or modified without cryptographic detection?
- **Incomplete Policy Auditing:** Are broker decision denials logged with sufficient context (agent ID, requested action, rule matched)?
- **Evaluation Timeouts:** Does policy evaluation have strict deadline timeouts to prevent thread exhaustion?

### 2. Agent Registration Gaps
- **Weak Token Lifetime Controls:** Are capability tokens long-lived without revocation mechanisms?
- **Missing Scope Restrictions:** Can an agent register with blanket permissions exceeding its required operational boundaries?
- **Replay Protection:** Are registration handshakes protected against replay attacks (e.g. nonces, timestamps)?

### 3. MCP Serve Gaps
- **Parameter Injection Vulnerabilities:** Are complex nested JSON tool parameters strictly schema-validated before invocation?
- **Context Pollution:** Can external prompts inject malicious instructions into the MCP context window?
- **Rate Limit Scope:** Is rate-limiting enforced per-agent and per-tool, or globally?

### 4. Assessment Adapter Gaps
- **Insufficient Sandbox Isolation:** Are assessment adapters executed in unprivileged sub-processes or isolated containers?
- **Telemetry Data Tampering:** Can an adapter modify evaluation results prior to storage in the telemetry database?

---

## Recommended Control Format

Every identified gap must include a concrete, actionable recommendation following this structure:

```markdown
### [GAP-01] Unmitigated Policy Injection in Broker Engine
- **STRIDE Threat:** Tampering / Elevation of Privilege
- **Affected Boundary:** Broker Decisions Engine
- **Inherent Severity:** P0 Critical
- **Control Status:** Unmitigated
- **Residual Risk:** P0 Critical (Confidence: 95%)
- **Impact:** An attacker modifying `.etg/entigram.yaml` or policy manifests could grant administrative capabilities to arbitrary agents.
- **Recommended Control:**
  1. Implement SHA-256 HMAC or RSA signature checking on all policy manifests before loading into memory.
  2. Add Sentinel Guard assertion `AssertManifestSigned(manifest)` prior to broker evaluation.
```

---

## Analysis Checklist

- [ ] Evaluated all unmitigated and partially mitigated threats from `control-mapper`
- [ ] Computed residual risk scores using the severity matrix
- [ ] Filtered out recommendations with confidence below 80%
- [ ] Ensured recommendations target specific Entigram trust boundaries (Broker, Agent Reg, MCP, Adapters)
- [ ] Provided concrete implementation steps for P0 and P1 gaps
