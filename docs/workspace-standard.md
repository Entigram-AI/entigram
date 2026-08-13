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
| `.agents/hooks.json` | Antigravity | Entigram's namespaced session gate, merged with any existing Antigravity hooks. |
| `.etg/lifecycle/pause-backup.json` | While paused | Private, exact backup of Entigram-owned context used for reversible resume. |
| `.etg/lifecycle/check-in-baseline.json` | Recommended | Private active-workspace metadata baseline refreshed after a successful handoff. |

Draft files, demos, templates, generated TTL files, and package-local schemas are
not authoritative unless `.etg/entigram.yaml` lists them in `schema_paths`.

## Discoverability Contract

An Entigram repository may expose the following machine-readable and
progressive-disclosure surfaces:

- `AGENTS.md` routes coding agents to `.etg/agent_policy.md`.
- `skills/entigram-workspace/SKILL.md` provides portable Agent Skill metadata
  and boot instructions.
- `server.json` describes the package-backed MCP server for registries and
  clients.
- `etg_get_capabilities` returns the live MCP capability catalog, including
  read-only and ledger-write behavior.
- `etg_get_workspace_context` returns the local boot context without changing
  workspace state.

These surfaces describe how an agent can discover Entigram. They do not grant
permissions, approve proposed state, replace host orchestration, or make direct
filesystem, database, shell, or network access governed. The workspace contract
and stable MCP response envelope remain authoritative.

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
lifecycle:
  state: active
  change_budget:
    max_changed_files: 5
external_artifacts:
  modalities: [image, pdf]
  trust: untrusted
  mode: advisory
  required_capabilities:
    - artifact-reputation/v1
    - visual-prompt-injection-screening/v1
status: initialized
```

The manifest may also include schema and ontology checksums written by Warden.
Agents must not edit these checksum values manually. Use the broker handoff flow
or `etg warden lock` when a governed schema or ontology change requires a new
lock. Warden fingerprints every file in `schema_paths`; adding an authoritative
schema requires a new lock before it can authorize alignments or state changes.

`governed_artifact_globs` is optional. It selects project files that delivery
snapshots must anchor and compare in both directions, including newly added
files. Without it, Entigram uses `git ls-files` to include tracked and untracked
non-ignored workspace files across languages. In a non-Git workspace, Entigram
falls back to polyglot source and project-configuration defaults. Both paths
exclude `.git`, `.etg`, virtual environments, dependency directories, caches,
and build output.

`lifecycle.change_budget.max_changed_files` is optional and defaults to `5`.
It defines the number of changed workspace files permitted after initialization
or the most recent successful broker handoff before Entigram asks the agent to
check in again. It is an admission cadence, not a replacement for Warden,
delivery evidence, or host filesystem permissions.

`external_artifacts` is optional and capability-aware. Its absence emits no
artifact-security warning. When present, `modalities` declares the artifact
types accepted by the workflow, `trust` is `trusted` or `untrusted`, and
`required_capabilities` contains granular versioned identifiers. Modes are
`off`, `advisory`, and explicitly enabled `enforce`; the default is `advisory`.

Hydration compares declared requirements with capabilities that are safe to
execute. Signed installed assessment packages are currently discovered but not
executed: their signatures prove integrity, not trusted-publisher identity, and
there is not yet an isolated adapter runtime. Their declared capabilities remain
missing and produce advisories with free mitigations. Coverage is exact:
`artifact-reputation/v1` does not satisfy visual prompt-injection,
adversarial-image, or media-provenance requirements. Community and third-party
packages may implement the same open capability contract for future isolated
execution.

Installed coverage and an assessment decision are different. Assessment
responses use `ok` only for execution success and return a separate `decision`,
`safe_to_process`, maximum severity, reason codes, unassessed required
capabilities, and recommended action. Agents must not treat `ok: true`, a clean
reputation result, or an installed capability as authorization to process an
untrusted artifact. Only `decision: allow` permits normal processing; other
outcomes preserve isolation and human review.

For CLI SHA-256 assessments, `--subject-file` binds the assessment to a
workspace-local artifact and detects byte changes during the assessment. Agents
should prefer it to a separately calculated digest to reduce time-of-check and
time-of-use ambiguity.

Local adapter development is an explicit operator action. Both
`--adapter-module` and `--allow-executable-adapter` are required, and the module
runs in the current CLI process. MCP never accepts a module path or executes an
installed assessment package in the current release.

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
- assessment mode, missing security capabilities, and risk advisories
- next recommended broker commands

Agents that need full state can request:

```bash
hydrate --full
```

Automation should branch on the JSON fields, not on surrounding prose.

## Workspace Lifecycle Contract

Workspaces without a `lifecycle` block are treated as active for compatibility.
New workspaces declare:

```yaml
lifecycle:
  state: active
```

`etg pause` changes the state to `paused` only after backing up and compacting
Entigram-owned policy and marked agent instruction blocks. While paused,
hydration returns a compact `WORKSPACE_PAUSED` envelope without loading schema
or ledger context. Governed CLI and MCP operations return the same stable error
code. Pause also snapshots user-visible workspace files and starts a bounded
change window (five changed files by default). `etg pause-status` reports drift
from that baseline; after the budget is exhausted, resume and hydrate before
another change. Local Git workspaces receive a temporary pre-commit guard that
rejects a commit exceeding the budget.

`etg resume` restores exact Entigram-owned content. It preserves edits outside
marked blocks and refuses to overwrite changed paused content unless `--force`
is supplied. Forced resume archives the conflict before restoring.

`etg eject` is archive-first. It must validate a complete `.etg` archive and
set mode `0600` before removing Entigram metadata or marked blocks. Project
schemas, ontologies, code, and unmarked user content remain in place. Eject
archives are not resumed directly; `etg init` re-enrolls a workspace.

These commands govern the workspace. `etg broker hibernate` and
`etg broker resume` govern an individual agent checkpoint and are not aliases.

## Usage Accounting Contract

`etg usage` reports the static Entigram context footprint and observed
short-lived CLI/MCP traffic using `heuristic_chars_div_4_v1`:

```text
estimated tokens = ceil(characters / 4)
```

The latest recorded hydration event is the current session boundary. An
optional `--total-tokens` value produces an estimated Entigram percentage for
that session. The estimate is provider-neutral and must not be represented as
provider billing.

Usage events may persist operation names, aggregate character/token counts,
lifecycle state, timestamps, and small operational metadata. Raw arguments,
prompts, responses, stdout, and stderr must not be persisted.

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

Intentional schema and ontology changes have an additional explicit boundary:

```bash
etg warden unlock
# edit and review the governed contract
etg broker handoff --accept-contract-change
```

`etg broker handoff` is the portable no-Make gate. It runs:

1. Verify the existing Warden fingerprint.
2. Run `broker guard`.
3. Compare-and-lock the contract without replacing unexpected drift.
4. Run `broker deliver`.
5. Report `broker status`.

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
and enterprise workflows can enforce signatures.

### Package sources and delivery

The default standard source is the Entigram Cloudflare Worker:
`https://api.entigram.ai/v1/registry`. The Worker serves packages from the
public `Entigram-AI/entigram/community-packages/` tree without a cloud key and
can authorize premium packages separately. The Worker is a distribution
endpoint, not the source-of-truth repository.

Entigram also supports multiple Git package sources. Add them with
`etg registry add --url <git-url>`; a source may expose namespace-qualified
packages at its repository root or beneath `community-packages/`. This lets
users develop packages in their own repositories and share them without
requiring every community package to be merged into Entigram. Package names
must remain namespace-qualified to avoid collisions, and signed manifests
should travel with the package artifact.

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
