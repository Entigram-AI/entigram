# Data Privacy Assessment

Use this package to perform a local, offline review of data inventories and
privacy-control evidence. It reports likely personal-data fields, encryption
coverage, and retention-policy coverage.

## Directives

- Treat field-name detection as heuristic triage, not proof that a field is or
  is not personal data.
- Require operators to review classifications and attach evidence references.
- Do not read, export, or transmit field values; the adapter accepts metadata
  only.
- Do not claim PCI DSS, SOC 2, GDPR, or other regulatory compliance from an
  assessment result.
- Keep the assessment offline and suitable for air-gapped CI environments.
- Custom rules may strengthen the review but cannot weaken the default checks.

## Capability

`pii-detection/v1`, `encryption-audit/v1`, `retention-policy-check/v1`

## Usage

```bash
etg assess \
  --adapter data-privacy-assessment \
  --adapter-module ./assessment_adapter.py \
  --allow-executable-adapter \
  --subject-type data-privacy-profile \
  --subject local-profile \
  --input-json privacy-profile.json \
  --json
```

The input contains metadata about data assets and fields, not data values.
