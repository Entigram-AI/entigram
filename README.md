<p align="center">
  <img src="entigram/ui/media/logo.svg" alt="Entigram" width="520">
</p>

# Entigram: The Semantic Governance Layer for Enterprise Agents

<!-- mcp-name: io.github.entigram-ai/entigram -->

**Entigram** is a schema-first control plane for enterprise agents that grounds agent behavior in verified domain models, approved semantic alignments, and auditable state transitions.

It provides the infrastructure to build **constrained autonomy**, ensuring that agents operate across fragmented enterprise systems without inventing fields, joins, entities, or state transitions.

## 🎯 The Entigram Thesis

Enterprise agent adoption fails when agents lack trustworthy domain context and enforceable schema boundaries. Entigram provides governed interfaces between participating agents and enterprise state.

> **Defensible Grounding:** Entigram's governed CLI and MCP interfaces reject unsupported concepts and unverified mappings before they enter participating operational workflows.

## 🛠️ Key Capabilities

- **Domain Boundaries (Schema):** Give agents explicit Entigram Schemas instead of relying on vague natural-language context.
- **Closed-World Reasoning:** Automatically reject or quarantine unknown entities, attributes, and relationships.
- **Verified Semantic Alignments:** Enable cross-domain data federation using approved mappings instead of fuzzy LLM guesses.
- **Deterministic Conflict Handling:** Transform contradictory agent states into auditable ledger entries for human or policy-driven resolution.
- **Expectation Guard:** Convert modeled expectations, implementation rules, and validation checks into a runnable pre-handoff agent gate.
- **Agent Hydration:** Boot agents with exact project state, schemas, alignments, and settled decisions.
- **Auditability:** Store every alignment and decision in a local SQLite ledger for full provenance and governance.
- **Transparent Usage:** Estimate Entigram-owned context and observed CLI/MCP traffic without retaining prompts or responses.
- **Reversible Enrollment:** Pause, resume, or archive-and-detach workspace governance without deleting project artifacts.

## Core Workflow

1. **Model** the entities, attributes, and relationships agents are allowed to know.
2. **Gate** every proposed alignment, conflict, and state transition through MCP/CLI tools.
3. **Audit** accepted work with ledger evidence, delivery snapshots, and tamper-evident bundles.

Entigram is not a universal agent sandbox. Its guarantees apply when operations
flow through the governed CLI/MCP surfaces; direct filesystem, database, shell,
and network access must still be controlled by the host platform. See the
[architecture and trust boundaries](spec/ENTIGRAM_ARCHITECTURE.md).

## 🚀 Quickstart

Install Entigram with your preferred Python tool:

```bash
pipx install entigram-ai
```

For source checkouts, use the repository virtual environment or run the module
form from the environment where Entigram's dependencies are installed.

### 1. Initialize a Governance Workspace

```bash
etg init --dir my-governed-agent
cd my-governed-agent
```

If `etg` is not on `PATH`:

```bash
python3 -m entigram.cli_runner.etg_cli init --dir my-governed-agent
```

### 2. Define your Schema Contracts (Schema)

Create a `schema.lds` to define the entities and relationships your agents are allowed to "know."

```bash
ENTITY: Supplier
ATTRIBUTES:
  - .id (UUID)
  - name (String)
  - tax_id (String)
```

### 3. Hydrate the Agent

Start every agent session by aligning the agent with the local workspace state:

```bash
hydrate
```

Equivalent fallbacks:

```bash
etg hydrate
python3 -m entigram.cli_runner.etg_cli hydrate
```

Before risky implementation, schema, ontology, package, or release changes:

```bash
etg broker preflight --file <path>
etg broker impact --file <path>
```

Before handoff:

```bash
etg broker handoff
etg broker status
```

`broker status` must report `Delivery status: current`.

Inspect Entigram's estimated share of a session:

```bash
etg usage
etg usage --total-tokens 50000
```

Temporarily compact and disable workspace governance:

```bash
etg pause --reason "Working without governed context"
etg resume
```

Archive and detach Entigram while preserving project schemas and code:

```bash
etg eject --dry-run
etg eject
```

Workspace `pause` and `resume` are separate from `etg broker hibernate` and
`etg broker resume`, which checkpoint one agent near a token or time limit.

### 4. Run the Immutable Gate over MCP

Start the local MCP server from the governed workspace:

```bash
etg serve
```

For agent and MCP-host discovery, start with
[`docs/discoverability.md`](docs/discoverability.md). MCP clients should call
`etg_get_capabilities` and `etg_get_workspace_context` before selecting a
governed operation. Agents can also load the reusable
[`entigram-workspace` Agent Skill](skills/entigram-workspace/SKILL.md).

Agents should discover schemas with `etg_get_schemas`, inspect change risk with
`etg_get_impact`, propose alignments with
`etg_propose_alignment`, and record deterministic conflicts with
`etg_log_conflict`. MCP responses use a stable JSON envelope:
```json
{"ok":false,"error":{"code":"UNKNOWN_CONCEPT","message":"Error: Invalid Schema Alignment - Entity Ghost not found","details":"Entity Ghost not found"}}
```

Successful proposals are written to the SQLite ledger configured in
`.etg/entigram.yaml`:
```yaml
schema_paths:
  - schema.lds
state_ledger: .etg/state.db
```

The server treats `schema_paths` as the closed-world boundary. Demo files,
templates, drafts, and unrelated LDS files are not exposed unless explicitly
listed.

### 5. Discover Draft Schemas from External Sources
Discovery is an intake path, not an authorization path. Entigram can inspect
external sources and emit draft LDS, but discovered entities, attributes,
relationships, and alignments must still be reviewed before they become
operational facts.

```bash
etg discover --source sqlite --path legacy.db --metadata
etg discover --source csv --path partner_orders.csv --domain PartnerOrder
etg discover --source json --path accounts.json --report-json
etg discover --adapter-module @entigram/salesforce/source_adapter.py \
  --source salesforce-describe --path http://127.0.0.1:8080/describe
```

Discovery includes an advisory model review. JSON reports include structured
`findings`, and human-readable summaries are emitted when findings are present.
Initial checks flag missing primary keys, FK-like columns without constraints,
multi-entity sources without relationships, composite keys, wide entities,
repeating column groups, JSON blob fields, low-confidence inferred fields, and
low-cardinality strings that may be better modeled as enums or reference data.

The core runtime ships the source-adapter contract and local SQLite/CSV/JSON
adapters. Cloud, SaaS, warehouse, catalog, and domain-specific adapters should
ship as Entigram standard packages that register source adapters with core at
runtime. This keeps Entigram cloud-agnostic while allowing package-level
coverage for AWS, Azure, GCP, Salesforce, OpenAPI, dbt, and other sources.
Database and infrastructure packages can cover PostgreSQL, MySQL/MariaDB,
SQL Server, Oracle, MongoDB, Neo4j, Snowflake, Terraform/OpenTofu, and similar
systems without making those clients core runtime dependencies.

Database standard packages should prefer Docker-hosted client tools over host
binary installation. For example, a PostgreSQL package should run `psql` with
`docker exec` against a `postgres` or `postgis/postgis` container, a MySQL or
MariaDB package should run `mysql` or `mariadb` inside the matching database
container, and SQL Server discovery should run `sqlcmd` from the SQL Server
container image. Host-installed clients are a fallback for operators who
already manage those binaries, not the default Entigram package path.

Standard packages can be signed without changing the install experience for
local exploration. Publishers generate deterministic manifests and Ed25519
signatures, while CI or registry workflows can opt into enforcement:

```bash
etg package sign --package @entigram/postgres --catalog standard_package_catalog.json
etg package verify --package @entigram/postgres
etg package sign-catalog --catalog standard_package_catalog.json
etg package verify-catalog --catalog standard_package_catalog.json
etg package audit --catalog standard_package_catalog.json --verify-signatures
```

If `--key` is omitted, Entigram creates `.etg/package_signing_ed25519_private.pem`
and keeps it out of version control. Package users can still suggest, inspect,
and install packages without managing signing keys.

Community packages are maintained in the public [`community-packages/`](community-packages/)
tree and delivered through the standard package Worker at
`https://api.entigram.ai/v1/registry`. Community package downloads do not
require an Entigram Cloud key. The Worker can still use `ENTIGRAM_TOKEN` for
premium packages that are not part of the public community tree.

Entigram supports multiple package sources. Add a public or private Git
registry to a workspace when a team maintains packages outside the core
repository:

```bash
etg registry add --url https://github.com/example/my-entigram-packages.git
etg package install --name @example/my-package
```

A Git package source may place packages at its repository root or under its
own `community-packages/` directory. Package names are namespace-qualified so
the Worker, the core community tree, and user registries can coexist like
Maven repositories. Package source code remains reviewable in its source
repository; the Worker is the delivery and caching layer.

Before returning work to a human reviewer:

```bash
etg broker handoff
etg broker status
```

Export an Ed25519-signed audit bundle:
```bash
etg broker export-audit --out entigram-audit.json
```

The first export creates a local signing key at
`.etg/audit_ed25519_private.pem`. Keep that private key out of source control.

For the complete MCP tool contract, see [`docs/mcp-tools.md`](docs/mcp-tools.md).
For the portable workspace contract, see
[`docs/workspace-standard.md`](docs/workspace-standard.md). A minimal local
example is available in
[`docs/minimal-governed-workspace.md`](docs/minimal-governed-workspace.md).
For the recommended OpenCode setup, see
[`docs/opencode.md`](docs/opencode.md).
For usage accounting and workspace lifecycle behavior, see
[`docs/workspace-lifecycle.md`](docs/workspace-lifecycle.md).

Run the local Immutable Gate smoke demo:
```bash
python3 scripts/demo_immutable_gate.py
```

### Optional Dashboard

`etg ui` requires Streamlit. The CLI/MCP runtime is headless by default.

For pipx:
```bash
pipx install 'entigram-ai[ui]'
```

For an existing pipx install:
```bash
pipx inject entigram-ai streamlit
```

Homebrew installs are optimized for the CLI/MCP path. If `etg ui` reports that
Streamlit is missing, that is expected unless the dashboard dependency has been
installed into the same Python environment.

## 🏗️ How it Fits

Entigram is not an orchestration framework, MCP replacement, graph database, or IAM product. It is the **semantic governance layer** that complements those systems by providing:

1. **Schema Discipline:** Validating agent inputs/outputs against a strict Schema.
2. **Alignment Gates:** Ensuring cross-system joins (e.g., Salesforce Opportunity to Warehouse SKU) use verified mappings.
3. **Decision Ledger:** Providing a persistent, auditable record of state transitions.

```text
Agent framework
  -> Entigram semantic governance
  -> MCP/tools/connectors/databases
  -> enterprise systems
```

| Existing Layer | Examples | Entigram's Role |
| --- | --- | --- |
| Agent orchestration | LangGraph, CrewAI, OpenAI Agents SDK, Microsoft Agent Framework | Validate domain state, mappings, payloads, and handoffs before agents act |
| Tool and data access | MCP, API tools, enterprise connectors | Govern tool schemas and block unsupported concepts or unverified mappings |
| Knowledge and context | RAG, GraphRAG, Neo4j, Stardog, data.world, LlamaIndex | Operationalize only verified concepts, relationships, and alignments |
| Runtime governance | RunAgents, Okta, policy engines, approval systems | Supply semantic policy signals and provenance for tool/action decisions |
| Observability | Tracing, OpenTelemetry, agent logs | Add semantic provenance: schema, alignment, evidence, conflict, and decision IDs |

## 🔒 Operational Principle

Discovery creates proposals, not operational facts.

Agents and routers may suggest alignments from schema similarity, partner data, or field names, but those proposals do not drive cross-domain joins until they are explicitly authorized with trusted evidence.

## 📈 Best-Fit Use Cases

- **Partner Reconciliation:** Normalizing and aligning external supplier data with internal systems.
- **Cross-Domain Integration:** Linking CRM data (Salesforce) to supply-chain or inventory forecasting.
- **Regulated Data Extraction:** Clinical/EHR extraction with strict validation and conflict gates.
- **Governance for Multi-Agent Ops:** Auditing the "handoff" state between different specialized agents.

## ⚖️ License

Entigram Core is Open Source under the Apache License 2.0.
Redistributions must preserve the Apache-2.0 license and the project
[`NOTICE`](NOTICE). Use of the Entigram name, certification language, registry
branding, or hosted-service marks is governed separately by
[`TRADEMARKS.md`](TRADEMARKS.md).

---
*Entigram: Grounding agentic autonomy in enterprise reality.*
