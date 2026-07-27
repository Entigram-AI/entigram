# Entigram MCP Tools

Entigram exposes a small MCP surface that acts as the deterministic gate between
agents and governed workspace state. All tool responses are JSON strings.

MCP operates inside the broader
[Entigram Workspace Standard](workspace-standard.md): the server exposes only
authoritative schema paths from `.etg/entigram.yaml`, writes governed proposals
to `.etg/state.db`, and returns machine-readable envelopes that agents can
branch on.

## Response Envelope

Successful responses include `ok: true`.

```json
{"ok":true,"status":"proposed"}
```

Failures include a stable code and a human-readable message.

```json
{
  "ok": false,
  "error": {
    "code": "UNKNOWN_CONCEPT",
    "message": "Error: Invalid Schema Alignment - Entity Ghost not found",
    "details": "Entity Ghost not found"
  }
}
```

Agents should branch on `error.code`, not prose.

## `etg_get_schemas`

Returns the authoritative LDS schemas for the workspace.

Input: none.

Output:

```json
{
  "ok": true,
  "schemas": [
    {
      "path": "schema.lds",
      "entities": {
        "Supplier": {
          "attributes": [
            {"name": "id", "type": "UUID", "pk": true, "constraints": []}
          ],
          "external_ref": null
        }
      },
      "relationships": [],
      "raw": "ENTITY: Supplier ..."
    }
  ]
}
```

Schema scope is closed-world. When `.etg/entigram.yaml` contains
`schema_paths`, only those local `.lds` files are exposed. Paths that escape the
workspace are rejected.

## `etg_propose_alignment`

Validates and records a proposed semantic alignment. Proposals are not trusted
operational facts until later verified.

Input JSON:

```json
{
  "source_domain": "CRM",
  "target_domain": "ERP",
  "source_concept": "Account.owner_name",
  "target_concept": "Supplier.name",
  "confidence": 0.91,
  "relation": "skos:closeMatch",
  "rationale": "Both fields identify the supplier-facing account owner.",
  "source_artifact": "schema-review-2026-06-20"
}
```

Required fields: `source_domain`, `target_domain`, `source_concept`,
`target_concept`, `rationale`.

Rejected conditions include unknown fields, malformed JSON, unknown entities,
unknown attributes, unsafe identifiers, unsupported relations, Warden integrity
failure, and relational precedence violations.

## `etg_log_conflict`

Records deterministic disagreement between agents for review or policy-driven
resolution.

Input JSON:

```json
{
  "conflict_id": "SupplierStatus_001",
  "entity_type": "Supplier",
  "agent_id": "ReconciliationAgent",
  "proposed_states": {
    "ReconciliationAgent": {"name": "Acme Corp"},
    "ERPAgent": {"name": "ACME Corporation"}
  }
}
```

Every attribute in every proposed state must exist on the LDS entity. Unknown
attributes are rejected and are not written to the ledger.

## Local Smoke Test

Run a deterministic proof without configuring an MCP client:

```bash
python3 scripts/demo_immutable_gate.py
```

Expected behavior:

- schema discovery returns only `schema.lds`
- a hallucinated entity returns `UNKNOWN_CONCEPT`
- a valid alignment is written as a proposal
- a conflict is written to the ledger
- `broker deliver` anchors the workspace snapshot

## Signed Audit Export

After a delivery is anchored, export a portable audit bundle:

```bash
etg broker export-audit --out entigram-audit.json
```

The bundle contains the latest delivery status, delivery evidence, anchored
artifacts, alignments, conflicts, and resolutions. Entigram signs the canonical
JSON payload with Ed25519 and includes the public key, key id, and signature in
the bundle. By default, the local private key is stored at
`.etg/audit_ed25519_private.pem`; keep it private and out of source control.
