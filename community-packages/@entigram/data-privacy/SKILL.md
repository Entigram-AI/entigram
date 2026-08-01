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

Use this minimal input shape as a starting point. `assets`, `controls`, and
`custom_rules` are lists; asset metadata does not include an `asset_type` key.

```json
{
  "profile_key": "workspace-profile",
  "assets": [
    {
      "name": "customer_records",
      "owner": "data-team",
      "system_ref": "inventory://customer_records",
      "fields": [
        {
          "name": "email",
          "data_type": "string",
          "classification": "personal",
          "pii_category": "contact",
          "encrypted_at_rest": true,
          "encryption_evidence_ref": "evidence://kms/customer_records",
          "retention_days": 365,
          "retention_policy_ref": "policy://customer_records",
          "retention_evidence_ref": "evidence://retention/customer_records",
          "evidence_refs": ["evidence://inventory/customer_records"]
        }
      ]
    }
  ],
  "controls": [
    {
      "control_type": "encryption_at_rest",
      "scope": "customer_records",
      "status": "implemented",
      "evidence_ref": "evidence://kms/customer_records"
    }
  ],
  "custom_rules": []
}
```

The adapter is offline and metadata-only. A successful run may still return
`decision: review_required` when findings need a human decision; `ok: true`
means the assessment executed successfully.
