# Entigram Architecture and Trust Boundaries

## Purpose

Entigram is a schema-first semantic governance layer for agent workflows. It
provides governed CLI and MCP interfaces that validate concepts, mappings,
conflicts, and delivery evidence against local contracts. It complements agent
frameworks, IAM, sandboxes, malware scanners, and enterprise data controls; it
does not replace them.

```text
Agent or operator
  -> governed Entigram CLI/MCP interface
    -> Warden + schema catalog + Broker
      -> SQLite decision ledger
      -> adapters/connectors/enterprise systems
```

Entigram's guarantees apply to operations routed through these governed
interfaces. A process with unrestricted filesystem, database, shell, or network
access can bypass Entigram and must be constrained by operating-system,
container, IAM, or agent-runtime controls.

## Components

- **LDS schema compiler:** parses the authoritative files listed in
  `.etg/entigram.yaml` under `schema_paths`.
- **Warden:** fingerprints those schema and ontology contracts and rejects drift.
- **Broker:** validates conflicts, semantic alignments, expectations, and
  delivery evidence. Handoff verifies the existing Warden lock before refreshing
  it; authorized contract changes require an explicit `warden unlock` first.
- **Commissioner and expectation guard:** execute modeled validation checks and
  record evidence before delivery.
- **SQLite ledger:** stores decisions, proposals, verified alignments, agent
  checkpoints, evidence, artifacts, and delivery snapshots in `.etg/state.db`.
- **Federated router:** reads domain databases and permits cross-domain joins
  only through verified, evidence-backed alignments.
- **MCP service:** exposes closed-world schema reads, impact analysis, alignment
  proposals, conflict logging, and assessment posture through structured JSON.
- **Sentinel:** performs limited package and LDS policy linting. It is not a
  malware scanner, dependency audit, or general repository vulnerability scan.
- **Registry and package signing:** verify package contents and signature
  integrity. A package signature is self-asserted until its key is matched to a
  separately governed trusted-publisher registry.

## Data ownership

| Data | Owner and location | Security role |
| --- | --- | --- |
| Schema contract | Workspace, `schema_paths` | Closed-world entity and field boundary |
| Integrity lock | Workspace, `.etg/entigram.yaml` | Detects contract drift |
| Decisions/evidence | Workspace, `.etg/state.db` | Append-oriented governance record |
| Delivery snapshot | Ledger plus artifact hashes | Last known governed delivery |
| Registry cache | User data, `~/.etg/registry_cache` | Untrusted cache until verified |
| Project history | User data, `~/.etg/projects.json` | Convenience metadata only |
| Credentials | User data, `~/.etg/credentials` | Mode `0600`; environment variables preferred |

## Trust boundaries

### Workspace content

Repository text, images, metadata, plugins, and package code are untrusted by
default. Ordinary CLI startup does not execute `.etg/plugins`. Workspace plugins
require the explicit `--enable-workspace-plugins` acknowledgement for each
invocation.

Installed assessment packages are discovered and integrity-checked but are not
executed by MCP. Trusted-publisher enrollment and isolated adapter execution are
future controls. During local development, an operator may review a module and
run it explicitly with both `--adapter-module` and
`--allow-executable-adapter`; this executes Python in the current process.

### External artifacts

Artifact-derived pixels, text, metadata, and model output are data, not
instructions or authorization. Assessment execution success is separate from a
safety decision. Only `decision: allow` permits normal processing; other
decisions preserve isolation and human review. Reputation checks do not prove
that an artifact is safe.

### Network services

MCP SSE, the panel bridge, and the Cloudflare/Ollama proxy are loopback-only
until authenticated remote transports are implemented. The legacy GraphQL
transport also defaults to loopback; a non-loopback bind requires a bearer token
and browser origins are deny-by-default. Reverse proxies do not remove the need
for authentication, authorization, TLS, request limits, and audit logging.

### Model execution

One-shot modeling passes prompts over standard input and selects an engine-
specific headless command. Permissions are not skipped by default. Codex runs in
read-only mode; Claude uses plan mode; Ollama has no Entigram filesystem tool
grant. Output validation determines whether a schema payload can be accepted,
but it is not a sandbox for the model process.

## Governance lifecycle

```text
hydrate
  -> inspect contracts and current delivery
  -> preflight + impact before risky changes
  -> implement and validate
  -> broker handoff
       1. verify the previous Warden fingerprint
       2. run expectation guard
       3. refresh the unchanged/explicitly unlocked contract lock
       4. record delivery snapshot and artifacts
       5. confirm delivery status
```

Schema or ontology evolution is a separate authorization path:

```text
warden check -> warden unlock -> human-authorized contract edit
  -> validation -> broker handoff --accept-contract-change
  -> new fingerprint and delivery
```

Handoff never treats unexpected drift as an authorized contract change.

## Semantic alignment lifecycle

Discovered mappings default to `proposed` and `verified = false`. Operational
routing requires verified lifecycle status, an allowed evidence type, sufficient
confidence, and explicit authorization. Programmatic authorization validates
schema concepts by default; narrowly scoped legacy/test callers must explicitly
opt out.

## Failure model

- Invalid or drifted contracts fail closed with a structured HaltEvent.
- Missing assessment capabilities produce advisories or enforcement blocks
  according to workspace mode.
- Cloud synchronization is currently unavailable and returns failure without
  claiming that local data was uploaded.
- Optional accelerators such as CozoDB may fall back to SQLite routing.
- Project-history persistence is non-critical and cannot block workspace work.
- A failed downstream Homebrew update cannot rerun or duplicate an already
  completed PyPI publication job.

## Scope and non-goals

Entigram does not provide endpoint malware scanning, IAM, secret management,
remote transport authentication, process sandboxing, backup, disaster recovery,
or universal interception of agent tools. Those controls belong to the hosting
platform and security program. Entigram contributes semantic validity,
provenance, fail-closed routing, and auditable decision state when its governed
interfaces are used.

## Domain packaging

The framework supports isolated vendor and business domains, but product/demo
schemas should migrate toward examples or standard packages rather than expand
the core runtime boundary. The root self-model currently contains historical
demonstration domains; new domain-specific functionality should be developed as
packages unless it is required by the governance kernel itself.
