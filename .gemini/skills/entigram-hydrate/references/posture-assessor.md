# Posture Assessor Checklist

## Scope
Workspace posture, `etg assess` output, technology stack advisories, compliance policies

## P0 — Critical (Must Fix / Hydration Blocker)
- [ ] Hardcoded secret, credential, or private key detected in workspace configuration or code
- [ ] Critical posture assessment failure from `etg assess` (unsupported runtime version, zero compliance grade)
- [ ] Execution of unverified or insecure external binary hooks during hydration

## P1 — High (Should Fix)
- [ ] Active high-severity technology advisories for workspace dependencies or platform components
- [ ] Mandatory security control violation (e.g. disabled TLS, unauthenticated RPC endpoint)
- [ ] Non-compliance with core Entigram governance or architectural mandates

## P2 — Medium (Fix or Track)
- [ ] Impending technology deprecation advisories (support ending within 90 days)
- [ ] Missing workspace posture safeguards (e.g., absent health checks, insufficient telemetry gates)
- [ ] Deviations from recommended Entigram federated architecture patterns

## P3 — Low (Optional)
- [ ] General technology stack modernization recommendations
- [ ] Non-critical posture optimization hints
- [ ] Formatting or documentation advisories in workspace policy metadata
