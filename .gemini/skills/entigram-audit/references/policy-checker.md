# Policy Checker Reference Checklist

## Scope
Scans governance, broker, sentinel, and warden files:
- **Broker Configuration:** `.etg/broker.yaml`, `broker.json`, `entigram.yaml`
- **Warden Integrity Files:** `.etg/warden.yaml`, `warden.py`, `warden_policy.py`
- **Sentinel Directives:** Annotations in code/schemas (`# sentinel: disable`, `@warden_suppress`, `# etg-policy: ignore`)
- **Access Control & Security Rules:** Workspace governance policies, boundary rules, capability assertions.

---

## Severity Scale

### 🔴 P0 — Critical (Governance Blocker)
- [ ] **Broker Governance Bypass:** Configuration or code attempting to bypass broker validation layers or disable central execution policies.
- [ ] **Warden Integrity Violation:** Warden verification hash mismatch, tampered warden rules, or disabled warden assertion guardrails.
- [ ] **Unapproved Sentinel Suppression:** `# sentinel: disable` or `@warden_suppress` directive added without a required tracking ticket ID (e.g. `SEC-1234`) or with an expired suppression window.
- [ ] **Production Policy Enforcement Disabled:** Governance policy turned off or set to permissive in production broker configuration.

### 🟠 P1 — High (Action Required)
- [ ] **Policy Drift:** Workspace broker rules out of sync with global or org-level baseline governance policies.
- [ ] **Missing Mandatory Sentinel Justification:** Sentinel suppression comment present but missing mandatory explanation or owner attribution.
- [ ] **Unrestricted Capability Request:** Broker capability grant requesting elevated system access without least-privilege scoping.

### 🟡 P2 — Medium (Warning / Track)
- [ ] **Deprecated Governance Directive Syntax:** Legacy policy annotation syntax used in broker configuration or source headers.
- [ ] **Missing Default-Deny Fallback:** Access policy configuration lacking explicit default-deny catch-all rule.
- [ ] **Expired Advisory Suppression:** Sentinel suppression past its recommended review date but not yet blocking build.

### 🟢 P3 — Low (Advisory)
- [ ] **Non-Standard Policy Style:** Formatting or indentation inconsistency in `.etg/broker.yaml` or `.etg/warden.yaml`.
- [ ] **Redundant Sentinel Comment:** Sentinel suppression applied to code block where no security policy rule triggers.
