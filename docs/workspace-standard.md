# Entigram Workspace Standard

The Entigram Workspace Standard defines the portable local contract that agents,
CLI tools, MCP clients, package adapters, and audit workflows can rely on in an
initialized Entigram project.

The standard is intentionally local-first. A workspace must be useful without a
hosted service, while still producing artifacts that a hosted registry, signing
authority, or compliance system can verify later.

## Versioning

The workspace standard is versioned by `workspace_schema_version` in
`.etg/entigram.yaml`. Version `1` covers the local manifest, LDS schema paths,
SQLite ledger, hydration summary, broker gate lifecycle, MCP envelope, audit
bundle, and package signing contracts described in this document.

Compatible changes may add optional fields, optional commands, or new package
metadata without changing `workspace_schema_version`. Breaking changes that
alter required files, required manifest keys, response envelopes, checksum
semantics, or delivery status meaning must increment `workspace_schema_version`
and include migration guidance.

## Standard Files

An initialized workspace uses these files and paths:

| Path | Required | Purpose |
| --- | --- | --- |
| `.etg/entigram.yaml` | Yes | Workspace manifest, active schema paths, package list, state ledger path, and integrity checksums. |
| `schema.lds` | Yes | Authoritative closed-world schema contract for entities, attributes, relationships, and expectations. |
| `.etg/state.db` | Yes | Local SQLite ledger for alignments, conflicts, resolutions, delivery evidence, delivery snapshots, and agent state. |
| `.etg/agent_policy.md` | Recommended | Canonical agent instructions for hydration, impact analysis, and handoff. |
| `AGENTS.md` or tool-specific instruction files | Recommended | Thin pointers that direct agents to `.etg/agent_policy.md`. |

Draft files, demos, templates, generated TTL files, and package-local schemas are
not authoritative unless `.etg/entigram.yaml` lists them in `schema_paths`.

## Manifest Contract

`.etg/entigram.yaml` is the workspace manifest. It must remain human-readable
YAML and should include:

```yaml
workspace_schema_version: 1
packages:
  Entigram Schemas: 0.0.1
schema_paths:
  - schema.lds
governed_artifact_globs:
  - "**/*.py"
  - "**/*.ts"
state_ledger: .etg/state.db
status: initialized
```

The manifest may also include schema and ontology checksums written by Warden.
Agents must not edit these checksum values manually. Use the broker handoff flow
or `etg warden lock` when a governed schema or ontology change requires a new
lock. Warden fingerprints every file in `schema_paths`; adding an authoritative
schema requires a new lock before it can authorize alignments or state changes.

`governed_artifact_globs` is optional. It selects project files that delivery
snapshots must anchor and compare in both directions, including newly added
files. Without it, Entigram uses polyglot source and project-configuration
defaults while excluding `.git`, `.etg`, virtual environments, dependency
directories, caches, and build output.

## Schema Contract

`schema.lds` is the authoritative closed-world boundary. Agents may only treat
entities, attributes, relationships, and modeled expectations in the listed LDS
schemas as operational facts.

Unknown entities, invented attributes, unverified alignments, and unsupported
relationships must be rejected or escalated. Discovery commands may produce
draft LDS, but discovery output is a proposal, not an authorized schema.

## Hydration Contract

Every agent session should begin with:

```bash
hydrate
```

If the console script is unavailable, these are equivalent fallbacks:

```bash
etg hydrate
python3 -m entigram.cli_runner.etg_cli hydrate
```

Default hydration returns a concise JSON summary inside the hydration sequence
markers. It includes:

- Entigram package version
- workspace schema version
- active packages
- agent policy path and policy text when present
- authoritative schema path and entity summary
- delivery status and Warden status
- expectation counts
- next recommended broker commands

Agents that need full state can request:

```bash
hydrate --full
```

Automation should branch on the JSON fields, not on surrounding prose.

## Broker Gate Lifecycle

Before risky implementation, schema, ontology, package, or release changes,
agents should inspect impact:

```bash
etg broker preflight --file <path>
etg broker impact --file <path>
```

`preflight` explains the risk class and required governance steps. `impact`
reports modeled expectations, entities, and relationships affected by a file.

Before handoff after source, schema, ontology, package, or release changes, run:

```bash
etg broker handoff
etg broker status
```

`etg broker handoff` is the portable no-Make gate. It runs:

1. `broker guard`
2. `warden lock`
3. `broker deliver`
4. `broker status`

The final status must report:

```text
Delivery status: current
```

Do not run `warden lock` after `broker deliver`. Locking mutates the manifest
and can invalidate the delivery snapshot that was just anchored.

Delivery status compares the complete current governed artifact set with the
snapshot. Changed, removed, renamed, and newly added governed files all require
recommissioning.

## MCP Contract

The MCP server starts from the governed workspace:

```bash
etg serve
```

The default transport is stdio MCP. SSE is available with:

```bash
etg serve --transport sse --host 127.0.0.1 --port 8080
```

MCP tools return JSON strings using a stable envelope:

```json
{"ok":true}
```

Failures include a stable error code:

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

Agents should branch on `error.code`, not prose. The current MCP tools are
documented in [mcp-tools.md](mcp-tools.md).

## Audit Bundle Contract

After a delivery snapshot is current, export a portable signed audit bundle:

```bash
etg broker export-audit --out entigram-audit.json
```

The audit bundle contains delivery status, delivery evidence, anchored
artifacts, alignments, conflicts, and resolutions. Entigram signs the canonical
JSON payload with Ed25519 and includes the public key, key id, and signature.

The default local signing key is:

```text
.etg/audit_ed25519_private.pem
```

Keep private keys out of source control.

## Package Contract

Standard packages extend Entigram without adding heavy cloud, database, or SaaS
dependencies to the core runtime. Packages can contribute source adapters,
schemas, skills, and package metadata.

Package trust is based on deterministic manifests and Ed25519 signatures:

```bash
etg package manifest --package @entigram/postgres
etg package sign --package @entigram/postgres
etg package verify --package @entigram/postgres
etg package sign-catalog --catalog standard_package_catalog.json
etg package verify-catalog --catalog standard_package_catalog.json
```

Local exploration should not require signature management, but CI, registries,
and enterprise workflows can enforce signatures. Entigram pins the official
standard-package signing key. Custom remote registries must declare trusted
publisher key IDs:

```bash
etg registry add \
  --url https://example.com/packages.git \
  --trusted-key-id <ed25519-key-id>
```

This writes a persistent policy to the workspace manifest:

```yaml
registry_trust:
  https://example.com/packages.git:
    require_signature: true
    trusted_key_ids:
      - <ed25519-key-id>
```

Unsigned custom registries require the explicit `--allow-unsigned` option.
Signatures without a configured or built-in trust root prove file consistency,
not publisher identity. A registry package version must not be older than the
version already locked in the workspace manifest.

## Portability Rules

The local workspace must remain useful without a hosted service:

- schemas are local files
- the ledger is local SQLite
- hydration is local CLI output
- MCP gates run locally
- audit bundles are portable JSON
- package manifests and signatures are deterministic

Hosted services may add retention, search, remote signing, registry trust,
policy management, compliance evidence, and team workflows, but they must not
make local governance fake-open.
