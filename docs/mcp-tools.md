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

When workspace governance is paused, every Entigram MCP tool returns:

```json
{
  "ok": false,
  "error": {
    "code": "WORKSPACE_PAUSED",
    "message": "Entigram workspace governance is paused.",
    "details": {
      "allowed_commands": ["etg usage", "etg resume", "etg eject"],
      "resume_command": "etg resume"
    }
  }
}
```

Resume the workspace with `etg resume` before retrying the tool.

## Discovery tools

MCP clients should call `etg_get_capabilities` first when the available Entigram
surface is unknown. It returns the authoritative tool catalog, input shapes,
read-only classification, ledger-write behavior, and transport boundary.

`etg_get_workspace_context` returns read-only workspace context for bootstrapping:

- lifecycle state and manifest version
- active packages and authoritative schema paths
- schema entity and relationship counts
- canonical policy and instruction-file paths
- current delivery status when available
- the recommended hydration, preflight, impact, and handoff commands

Neither tool changes the workspace or ledger. They complement the CLI hydration
sequence; `hydrate` remains the canonical full agent boot command.

## Safety classification

| Tool | Read-only | Writes `.etg/state.db` | Purpose |
| --- | --- | --- | --- |
| `etg_get_schemas` | Yes | No | Read authoritative LDS schemas. |
| `etg_get_impact` | Yes | No | Analyze file change impact. |
| `etg_get_workspace_context` | Yes | No | Read workspace boot context. |
| `etg_get_capabilities` | Yes | No | Read the MCP capability catalog. |
| `etg_get_assessment_capabilities` | Yes | No | Read signed assessment metadata and advisories. |
| `etg_assess` | Yes | No | Run the fail-closed assessment boundary. |
| `etg_propose_alignment` | No | Yes | Persist a proposed alignment. |
| `etg_log_conflict` | No | Yes | Persist a conflict for review. |

The read/write classification is also returned by `etg_get_capabilities` so
clients can make tool-selection decisions without scraping prose.

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

## `etg_get_assessment_capabilities`

Returns non-executable assessment metadata discovered in valid, signed packages
installed in the workspace. It also returns the current capability-aware
security posture and excluded-package reasons.

Input: none.

Unsigned packages and arbitrary module paths are never loaded through MCP.
Signed installed packages are also not executed: current signatures prove
content integrity, not trusted-publisher identity, and adapters do not yet run
in an isolated process. Their declared capabilities therefore remain missing.

## `etg_assess`

Requests an assessment through the governed MCP surface. Executable installed
adapters currently fail closed with `ASSESSMENT_FAILED` until trusted-publisher
verification and process isolation are available.

Input JSON:

```json
{
  "adapter": "virustotal-hash",
  "subject_type": "sha256",
  "subject": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
  "data": {}
}
```

Unknown fields, malformed subjects, unknown adapters, and executable installed
adapters are rejected. Once an isolated adapter runtime is available, results
will report capabilities actually exercised. When workspace mode is `enforce`,
an assessment remains blocked until all required capabilities are covered.

Assessment execution and safety are separate. `ok: true` means the adapter ran
successfully; it does not mean the subject is safe. Agents must branch on
`decision` and `safe_to_process`. A successful response includes:

```json
{
  "ok": true,
  "decision": "review_required",
  "safe_to_process": false,
  "human_review_required": true,
  "max_severity": "high",
  "reason_codes": ["HIGH_FINDING"],
  "required_capabilities_unassessed": [],
  "recommended_action": "Keep the subject isolated and require human review before any artifact-derived output can trigger a state-changing action.",
  "assessment": {"adapter": "virustotal-hash", "findings": [{"severity": "high"}]},
  "security_posture": {"mode": "advisory"}
}
```

`decision` is `allow`, `review_required`, or `blocked`. `allow` is returned only
when the assessment has no findings and every active required capability was
both available and exercised by that assessment. Missing advisory-mode
capabilities, unassessed required capabilities, and findings of any severity
produce `review_required`; invalid/enforced policy gaps and critical findings
produce `blocked`.

For explicit local package development, the CLI may load a module path:

```bash
etg assess \
  --adapter virustotal-hash \
  --adapter-module ./assessment_adapter.py \
  --allow-executable-adapter \
  --subject-type sha256 \
  --subject <sha256> \
  --json
```

This acknowledgement means the reviewed Python module will execute in the
current CLI process. The MCP tool intentionally has no equivalent module-path
input. A `review_required` CLI result exits with status 3; a blocked result exits
with status 2, so automation cannot mistake either outcome for an allow.

For CLI hash-reputation checks, prefer `--subject-file` over calculating and
passing the digest separately. Entigram confines the path to the workspace,
hashes it locally, and hashes it again after assessment. If the bytes change,
the decision is `blocked` with `SUBJECT_CHANGED_DURING_ASSESSMENT`.

```bash
etg assess \
  --adapter virustotal-hash \
  --adapter-module ./assessment_adapter.py \
  --allow-executable-adapter \
  --subject-type sha256 \
  --subject-file artifacts/inbox/vendor-invoice.png \
  --dir . \
  --json
```

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
