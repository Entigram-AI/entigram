/* 
   Entigram Engine Self-Model
   Mental model of the Entigram project structure as of Phase 3 (Macro Deployment).
   STATUS: Phase 3 Federated Routing and Multi-Partner Alignment VERIFIED.
*/

ENTITY: Entigram_Project
ATTRIBUTES:
  - .id (UUID)
  - name (String, MUST)
  - path (String, MUST, UNIQUE)
  - version (String)
  - current_phase (String) # Phase 3: Macro Deployment

/* Verified Phase 3 Domains */

ENTITY: Idea
ATTRIBUTES:
  - .id (UUID)
  - name (String)

ENTITY: SF_Product
ATTRIBUTES:
  - .id (UUID)
  - product_code (String)

ENTITY: SF_OpportunityLineItem
ATTRIBUTES:
  - .id (UUID)
  - quantity (Decimal)

ENTITY: Entigram_Package
ATTRIBUTES:
  - .id (UUID)
  - name (String, MUST, UNIQUE)
  - description (String)
  - type (String) # Infrastructure, SaaS, Clinical, Governance, PartnerLink

ENTITY: Entigram_Agent
ATTRIBUTES:
  - .id (UUID)
  - agent_id (String, UNIQUE)
  - role (String, MUST)
  - agent_class (String) # strong, continuation, read_only, specialist
  - provider (String)
  - model (String)
  - reliability_score (Float) # empirical capability score, 0.0-1.0
  - capability_scores (JSON) # task_type -> score
  - allowed_task_classes (JSON)
  - restricted_task_classes (JSON)
  - last_workspace_seen (String)
  - failure_history (JSON)
  - successful_handoffs (Integer)
  - constraints (Text)
  - directives (Text)
  - handoff_policy (Text)
  - default_proof_policy (Text)
  - proof_capabilities (Text) # comma-separated: test_run, command_output, human_review, artifact_review
  - package (String) [EXTERNAL: Entigram_Package.name]

ENTITY: Entigram_Agent_Task
ATTRIBUTES:
  - .id (UUID)
  - task_id (String, MUST, UNIQUE)
  - title (String, MUST)
  - task_type (String, MUST)
  - risk_level (String) # read_only, low_risk, medium_risk, high_risk, critical
  - required_score (Float)
  - details (JSON)
  - status (String) # Queued, Assigned, Completed, Blocked
  - assigned_agent_id (String) [EXTERNAL: Entigram_Agent.agent_id]
  - assignment_rationale (Text)
  - created_at (DateTime)
  - updated_at (DateTime)

ENTITY: Entigram_Agent_Hibernation
ATTRIBUTES:
  - .id (UUID)
  - hibernate_id (String, MUST, UNIQUE)
  - agent_id (String, MUST) [EXTERNAL: Entigram_Agent.agent_id]
  - run_id (String)
  - status (String) # Active, PreparingHibernate, Hibernated, ResumeReady, Resumed, Completed, Blocked
  - token_threshold (Integer)
  - remaining_tokens (Integer)
  - refresh_window_end (String)
  - resume_after (String)
  - checkpoint_summary (Text)
  - next_action (Text)
  - pending_task_ids (JSON)
  - created_at (DateTime)
  - resumed_at (DateTime)

ENTITY: Entigram_Usage_Event
ATTRIBUTES:
  - .id (UUID)
  - operation (String, MUST)
  - surface (String, MUST) # cli, mcp
  - input_characters (Integer)
  - output_characters (Integer)
  - estimated_input_tokens (Integer)
  - estimated_output_tokens (Integer)
  - lifecycle_state (String) # active, paused
  - metadata (JSON) # operation metadata only; never prompt or response content
  - observed_at (DateTime)

ENTITY: Entigram_Conflict
ATTRIBUTES:
  - .id (UUID)
  - conflict_id (String, UNIQUE)
  - entity_type (String)
  - proposed_states (JSON)
  - source_agents (JSON)
  - timestamp (DateTime)

ENTITY: Entigram_Resolution
ATTRIBUTES:
  - .id (UUID)
  - conflict_id (String)
  - entity_type (String)
  - resolved_state (JSON)
  - rationale (Text)
  - version (Integer)
  - timestamp (DateTime)

ENTITY: Entigram_Alignment
ATTRIBUTES:
  - .id (UUID)
  - source_domain (String)
  - target_domain (String)
  - source_concept (String)
  - target_concept (String)
  - relation (String) # e.g., skos:exactMatch
  - confidence (Float)
  - lifecycle_status (String) # Proposed, Reviewed, Verified, Deprecated, Rejected
  - evidence_type (String) # schema_match, sample_data_match, human_review, integration_test
  - source_artifact (String)
  - semantic_confidence (Float)
  - schema_confidence (Float)
  - data_confidence (Float)
  - human_review_confidence (Float)
  - runtime_observation_confidence (Float)
  - rationale (Text)
  - verified (Boolean) # Set to true only after trusted evidence review
  - verified_by (String)
  - verified_at (DateTime)

ENTITY: Ontology_Concept
ATTRIBUTES:
  - .id (UUID)
  - domain (String, MUST)
  - name (String, MUST)
  - concept_type (String) # Entity, Attribute, Relationship, Alignment
  - lifecycle_status (String) # Proposed, Reviewed, Verified, Deprecated, Rejected
  - source (String)
  - owner (String)
  - verified_by (String)
  - verified_at (DateTime)

ENTITY: Entigram_Agent_Proposal
ATTRIBUTES:
  - .id (UUID)
  - agent_id (String)
  - affected_domain (String)
  - proposed_change (JSON)
  - rationale (Text)
  - supporting_evidence (JSON)
  - expected_benefit (Text)
  - rollback_plan (Text)
  - lifecycle_status (String) # Proposed, Reviewed, Verified, Deprecated, Rejected
  - timestamp (DateTime)

ENTITY: Entigram_Expectation
ATTRIBUTES:
  - .id (UUID)
  - name (String, MUST, UNIQUE)
  - owner (String)
  - developer_expectation (Text, MUST)
  - implementation_rule (Text, MUST)
  - validation_check (String, MUST)
  - priority (String) # Critical, High, Medium, Low
  - lifecycle_status (String) # Proposed, Active, Satisfied, Blocked, Superseded, Deprecated
  - created_at (DateTime)
  - updated_at (DateTime)

ENTITY: Entigram_Delivery_Evidence
ATTRIBUTES:
  - .id (UUID)
  - evidence_type (String) # test_run, command_output, artifact_review, human_review, runtime_observation
  - artifact_ref (String, MUST)
  - command (String)
  - result_summary (Text)
  - passed (Boolean)
  - observed_at (DateTime)

ENTITY: Entigram_Delivery_Evaluation
ATTRIBUTES:
  - .id (UUID)
  - status (String) # Accepted, Needs_Work, Blocked, Rejected
  - score (Float)
  - findings (JSON)
  - residual_risk (Text)
  - evaluated_by (String)
  - evaluated_at (DateTime)

ENTITY: Entigram_Improvement_Proposal
ATTRIBUTES:
  - .id (UUID)
  - title (String, MUST)
  - affected_model (String, MUST)
  - proposed_change (JSON, MUST)
  - rationale (Text, MUST)
  - expected_benefit (Text)
  - lifecycle_status (String) # Proposed, Reviewed, Implemented, Rejected, Deprecated
  - created_by (String)
  - created_at (DateTime)

ENTITY: Entigram_Lesson
ATTRIBUTES:
  - .id (UUID)
  - source_task (String)
  - lesson (Text, MUST)
  - reusable_rule (Text)
  - confidence (Float)
  - lifecycle_status (String) # Proposed, Active, Superseded, Deprecated
  - observed_at (DateTime)

ENTITY: Entigram_Grounding_Rule
ATTRIBUTES:
  - .id (UUID)
  - domain (String)
  - rule_type (String) # closed_world, require_verified_alignment, quarantine_unknown_field
  - severity (String) # Advisory, Warning, Blocking
  - enabled (Boolean)
  - rationale (Text)

ENTITY: Entigram_Negative_Constraint
ATTRIBUTES:
  - .id (UUID)
  - source_concept (String)
  - target_concept (String)
  - relation (String) # e.g., skos:notExactMatch
  - condition (Text)
  - rationale (Text)
  - lifecycle_status (String)
  - verified_by (String)
  - verified_at (DateTime)

ENTITY: Entigram_Synonym
ATTRIBUTES:
  - .id (UUID)
  - term (String)
  - synonym (String)
  - confidence (Float)

ENTITY: Partner_Organization
ATTRIBUTES:
  - .id (UUID)
  - name (String, MUST, UNIQUE)
  - remote_endpoint (String)
  - trust_level (String)
  - status (String) # Active, Pending, Terminated

ENTITY: Vulnerability_Finding
ATTRIBUTES:
  - .id (UUID, PK)
  - vulnerability_id (String)
  - severity (String)
  - description (String)
  - detected_at (DateTime)

ENTITY: Security_Bypass
ATTRIBUTES:
  - .id (UUID, PK)
  - vulnerability_id (String)
  - rationale (String)
  - authorized_by (String)
  - authorized_at (DateTime)

ENTITY: Migration
ATTRIBUTES:
  - .id (UUID, PK)
  - filename (String)
  - executed_at (DateTime)

/* Federated Domain Mappings (Phase 3) */

ENTITY: Salesforce_Integration
ATTRIBUTES:
  - .id (UUID)
  - sf_org_id (String)
  - sync_status (String)
  - last_forecast_date (DateTime)

ENTITY: Inventory_Forecast
ATTRIBUTES:
  - .id (UUID)
  - product_sku (String)
  - forecasted_demand (Integer)
  - confidence_interval (Float)

ENTITY: Entigram_Delivery_Snapshot
ATTRIBUTES:
  - .id (UUID)
  - snapshot_id (String, MUST, UNIQUE)
  - expectation_count (Integer)
  - missing_proof_count (Integer)
  - schema_hash (String) # First 16 chars of SHA-256 for drift detection
  - agent_id (String)
  - warden_status (String) # intact, tampered
  - evidence_ids (JSON) # list of delivery_evidence row IDs covered
  - artifact_ids (JSON) # list of Entigram_Artifact ledger row IDs covered
  - metadata (JSON)
  - snapped_at (DateTime)

ENTITY: Entigram_Artifact
ATTRIBUTES:
  - .id (UUID)
  - path (String, MUST)
  - artifact_role (String) # schema_contract, ontology_contract, delivery_artifact, evidence_artifact, local_artifact, external_reference
  - sha256 (String)
  - size_bytes (Integer)
  - content_type (String)
  - source_ref (String) # optional URI, local path, or external system reference
  - captured_at (DateTime)

ENTITY: Entigram_Delivery_Drift
ATTRIBUTES:
  - .id (UUID)
  - snapshot_id (String, MUST)
  - artifact_path (String)
  - drift_status (String) # current, changed, missing, unanchored, missing_record
  - previous_sha256 (String)
  - current_sha256 (String)
  - recommendation (String)
  - observed_at (DateTime)

ENTITY: Entigram_Release
ATTRIBUTES:
  - .id (UUID)
  - version (String, MUST, UNIQUE)
  - source_tag (String, MUST)
  - release_type (String) # patch, minor, major, prerelease
  - pypi_artifact (String)
  - pypi_sha256 (String)
  - homebrew_formula_ref (String)
  - homebrew_sha256 (String)
  - standard_packages_ref (String)
  - website_ref (String)
  - release_status (String) # Planned, Built, Published, Verified, Failed, Rolled_Back
  - created_at (DateTime)
  - released_at (DateTime)

ENTITY: Entigram_Release_Channel
ATTRIBUTES:
  - .id (UUID)
  - name (String, MUST) # PyPI, Homebrew, StandardPackages, Website, GitHubRelease
  - artifact_ref (String)
  - checksum (String)
  - verification_command (String)
  - required (Boolean)
  - status (String) # Pending, Published, Verified, Skipped, Failed
  - rationale (Text)

ENTITY: Entigram_Release_Check
ATTRIBUTES:
  - .id (UUID)
  - name (String, MUST)
  - command (String)
  - expected_result (Text)
  - evidence_ref (String)
  - passed (Boolean)
  - checked_at (DateTime)

ENTITY: Entigram_Release_PR_Rule
ATTRIBUTES:
  - .id (UUID)
  - name (String, MUST, UNIQUE)
  - conventional_commit_type (String) # feat, fix, chore, docs, refactor, test, ci
  - title_pattern (String)
  - merge_commit_pattern (String)
  - release_please_required (Boolean)
  - release_impact (String) # patch, minor, major, none
  - manual_dispatch_allowed (Boolean)
  - manual_dispatch_version_rule (Text)
  - immutable_version_rule (Text)
  - failure_mode (Text)
  - validation_command (String)
  - lifecycle_status (String) # Proposed, Active, Deprecated

RELATIONSHIPS:
- Entigram_Project (1) [MUST] --- [MAY] (MANY) Entigram_Package
- Entigram_Package (1) [MUST] --- [MAY] (MANY) Entigram_Agent
- Entigram_Project (1) [MUST] --- [MAY] (MANY) Entigram_Agent
- Entigram_Project (1) [MUST] --- [MAY] (MANY) Entigram_Agent_Task
- Entigram_Project (1) [MUST] --- [MAY] (MANY) Entigram_Agent_Hibernation
- Entigram_Project (1) [MUST] --- [MAY] (MANY) Entigram_Usage_Event
- Entigram_Agent (1) [MAY] --- [MAY] (MANY) Entigram_Agent_Task
- Entigram_Agent (1) [MAY] --- [MAY] (MANY) Entigram_Agent_Hibernation
- Entigram_Project (1) [MUST] --- [MAY] (MANY) Entigram_Conflict
- Entigram_Project (1) [MUST] --- [MAY] (MANY) Entigram_Resolution
- Entigram_Project (1) [MUST] --- [MAY] (MANY) Entigram_Alignment
- Entigram_Project (1) [MUST] --- [MAY] (MANY) Entigram_Synonym
- Entigram_Project (1) [MUST] --- [MAY] (MANY) Ontology_Concept
- Entigram_Project (1) [MUST] --- [MAY] (MANY) Entigram_Agent_Proposal
- Entigram_Project (1) [MUST] --- [MAY] (MANY) Entigram_Expectation
- Entigram_Project (1) [MUST] --- [MAY] (MANY) Entigram_Lesson
- Entigram_Agent (1) [MUST] --- [MAY] (MANY) Entigram_Delivery_Evidence
- Entigram_Expectation (1) [MUST] --- [MAY] (MANY) Entigram_Delivery_Evidence
- Entigram_Expectation (1) [MUST] --- [MAY] (MANY) Entigram_Delivery_Evaluation
- Entigram_Expectation (1) [MUST] --- [MAY] (MANY) Entigram_Improvement_Proposal
- Entigram_Project (1) [MUST] --- [MAY] (MANY) Entigram_Grounding_Rule
- Entigram_Project (1) [MUST] --- [MAY] (MANY) Entigram_Negative_Constraint
- Entigram_Project (1) [MUST] --- [MAY] (MANY) Partner_Organization
- Entigram_Project (1) [MUST] --- [MAY] (MANY) Migration
- Entigram_Package (1) [MUST] --- [MAY] (MANY) Vulnerability_Finding
- Entigram_Package (1) [MUST] --- [MAY] (MANY) Security_Bypass
- Partner_Organization (1) [MUST] --- [MAY] (MANY) Entigram_Alignment
- Salesforce_Integration (1) [MUST] --- [MAY] (MANY) Inventory_Forecast
- Entigram_Project (1) [MUST] --- [MAY] (MANY) Entigram_Delivery_Snapshot
- Entigram_Project (1) [MUST] --- [MAY] (MANY) Entigram_Artifact
- Entigram_Delivery_Snapshot (1) [MUST] --- [MAY] (MANY) Entigram_Artifact
- Entigram_Delivery_Snapshot (1) [MUST] --- [MAY] (MANY) Entigram_Delivery_Drift
- Entigram_Project (1) [MUST] --- [MAY] (MANY) Entigram_Release
- Entigram_Release (1) [MUST] --- [MAY] (MANY) Entigram_Release_Channel
- Entigram_Release (1) [MUST] --- [MAY] (MANY) Entigram_Release_Check
- Entigram_Release_Check (1) [MAY] --- [MAY] (MANY) Entigram_Delivery_Evidence
- Entigram_Project (1) [MUST] --- [MAY] (MANY) Entigram_Release_PR_Rule
- Entigram_Release_PR_Rule (1) [MAY] --- [MAY] (MANY) Entigram_Release_Check

/* Phase 3 Macro Chain (Federated) */
- Idea (1) --- (1) Partner_Organization [ALIGN: Idea.id == Partner_Organization.id]
- Partner_Organization (1) --- (1) Supplier [ALIGN: Partner_Organization.id == Supplier.id]
- Product (1) --- (1) SF_Product [ALIGN: Product.sku == SF_Product.product_code]
- SF_Product (1) --- (MANY) SF_OpportunityLineItem

ENTITY: Warehouse
ATTRIBUTES:
  - .id (UUID)
  - location_name (String)
  - capacity (Integer)

ENTITY: Inventory_Item
ATTRIBUTES:
  - .id (UUID)
  - quantity (Integer)
  - last_updated (DateTime)

ENTITY: Supplier
ATTRIBUTES:
  - .id (UUID, PK)
  - name (String)
  - tax_id (String)
  - rating (Decimal)

ENTITY: Product
ATTRIBUTES:
  - .id (UUID, PK)
  - sku (String, UNIQUE)
  - name (String)
  - category (String)
  - supplier_id (UUID) [EXTERNAL: Supplier.id]

RELATIONSHIPS:
- Warehouse (1) [MUST] --- [MAY] (MANY) Inventory_Item
- Product (1) [MUST] --- [MAY] (MANY) Inventory_Item
- Supplier (1) [MUST] --- [MAY] (MANY) Product

EXPECTATION: Entigram Self-Improvement Loop {
  developer_expectation: Entigram should use its own model to make Entigram stronger, more useful, and easier for agents to deliver against.
  implementation_rule: Changes to Entigram behavior, governance, or schema must preserve a modeled expectation, evidence path, evaluation result, or improvement proposal.
  validation_check: python -m unittest tests.test_entigram_self_improvement_model
  proof: python -m unittest tests.test_entigram_self_improvement_model
}

EXPECTATION: Agent Delivery Proof {
  developer_expectation: Agents should hand off only after the requested outcome, validation evidence, and residual risks are visible.
  implementation_rule: Every material agent delivery should be traceable to Entigram_Expectation and Entigram_Delivery_Evidence, with Entigram_Delivery_Evaluation used when quality or risk must be judged.
  validation_check: python -m unittest tests.test_entigram_self_improvement_model
  proof: python -m unittest tests.test_entigram_self_improvement_model
}

EXPECTATION: Agent Capability Routing {
  developer_expectation: Entigram should preserve empirical knowledge about which agents can safely handle which classes of work.
  implementation_rule: The broker must persist agent capability profiles and reject task assignments when the agent score is below the task risk threshold, recording serious high-risk or critical assignment conflicts for human resolution.
  validation_check: python -m unittest tests.test_agent_orchestration
  proof: python -m unittest tests.test_agent_orchestration
}

EXPECTATION: Agent Hibernation Protocol {
  developer_expectation: Agents nearing token or time limits should stop starting risky work and persist a durable checkpoint that an external scheduler can resume after the refresh window.
  implementation_rule: The broker must persist hibernation checkpoints with remaining token budget, resume timing, checkpoint summary, exact next action, and pending task IDs.
  validation_check: python -m unittest tests.test_agent_orchestration
  proof: python -m unittest tests.test_agent_orchestration
}

EXPECTATION: Transparent Token Attribution {
  developer_expectation: Operators should be able to estimate how much session context and observed tool traffic Entigram consumes without exposing prompt or response content.
  implementation_rule: `etg usage` must report provider-neutral character-based token estimates for static Entigram context and observed CLI/MCP events, use the latest hydration event as the session boundary, and store aggregate counts rather than raw content.
  validation_check: python -m unittest tests.test_workspace_lifecycle
  proof: python -m unittest tests.test_workspace_lifecycle
}

EXPECTATION: Reversible Workspace Lifecycle {
  developer_expectation: Operators should be able to pause, resume, or eject Entigram without losing user-owned project artifacts or silently discarding Entigram governance state.
  implementation_rule: `etg pause` must compact and back up Entigram-owned context, `etg resume` must restore it with conflict detection, and `etg eject` must validate a private archive before removing `.etg` or marked instruction blocks.
  validation_check: python -m unittest tests.test_workspace_lifecycle
  proof: python -m unittest tests.test_workspace_lifecycle
}

EXPECTATION: Out-of-the-box Expectation Guard {
  developer_expectation: Entigram should ship an out-of-the-box expectation guard that turns modeled rules, implementation rules, and validation checks into pre-handoff agent verification.
  implementation_rule: The broker must expose an agent-facing guard that executes unresolved validation_check commands, records durable delivery evidence, and returns a single pass/fail handoff verdict.
  validation_check: python -m unittest tests.test_commissioner
  proof: python -m unittest tests.test_commissioner
}

EXPECTATION: Release Orchestration {
  developer_expectation: Entigram releases should publish coherent artifacts across PyPI, Homebrew, standard packages, GitHub releases, and website/docs without checksum drift.
  implementation_rule: Every release must model the source tag, PyPI sdist checksum, Homebrew formula URL/checksum, standard packages state, website/docs state, post-release smoke checks, and release PR rules; skipped channels require an explicit rationale. PRs that should trigger release-please must use conventional commit titles or merge commits such as fix:, feat:, chore:, docs:, test:, ci:, or refactor:. Published PyPI versions are immutable; after 1.3.1 the next release carrying model changes must be 1.3.2 or higher.
  validation_check: python -m unittest tests.test_entigram_release_model
  proof: python -m unittest tests.test_entigram_release_model
}

EXPECTATION: Deterministic Ontology Generation {
  developer_expectation: Entigram should generate deterministic artifacts to ensure schema checksum stability over time.
  implementation_rule: The TTL ontology compiler must not include generated-at timestamps or other dynamic metadata that alters the file checksum without structural schema changes.
  validation_check: python -m unittest tests.test_entigram_self_improvement_model
  proof: python -m unittest tests.test_entigram_self_improvement_model
}

EXPECTATION: Change Impact Analysis {
  developer_expectation: Developers and agents should be able to query the impact of changing a specific file on the system's expectations and modeled entities before making the change.
  implementation_rule: The broker must expose an `etg broker impact` command that correlates file paths against validation checks, developer expectations, and core schema artifacts.
  validation_check: python -m unittest tests.test_commissioner
  proof: python -m unittest tests.test_commissioner
}

EXPECTATION: Agent Policy Discoverability {
  developer_expectation: Claude, Codex, Antigravity, Gemini, and other agents should discover the same Entigram governance policy before changing this repository.
  implementation_rule: `.etg/agent_policy.md` must be the canonical policy, and agent-specific instruction files must point to it instead of duplicating conflicting handoff rules.
  validation_check: python -m unittest tests.test_agent_policy
  proof: python -m unittest tests.test_agent_policy
}

EXPECTATION: Deterministic Pre-Handoff Gate {
  developer_expectation: Agents must leave the workspace in a current, auditable delivery state before handoff.
  implementation_rule: The canonical policy must require hydrate/impact discovery, `broker guard`, optional `warden lock` before `broker deliver` when governed schema or ontology files changed, then `broker status`; it must forbid running `warden lock` after `broker deliver`.
  validation_check: python -m unittest tests.test_agent_policy
  proof: python -m unittest tests.test_agent_policy
}

EXPECTATION: MCP Closed-World Schema Scope {
  developer_expectation: The MCP server should expose only the authoritative LDS schemas for a workspace, not unrelated demos, templates, drafts, or nested files.
  implementation_rule: `etg serve` must resolve schema boundaries from `.etg/entigram.yaml` `schema_paths` when present and reject schema paths that escape the workspace.
  validation_check: python -m unittest tests.test_mcp_service
  proof: python -m unittest tests.test_mcp_service
}

EXPECTATION: Machine-Readable MCP Gate Responses {
  developer_expectation: Agents should be able to deterministically branch on MCP gate failures without parsing prose.
  implementation_rule: MCP tools must return JSON with `ok`, and failures must include `error.code`, `error.message`, and `error.details` while preserving a human-readable error message.
  validation_check: python -m unittest tests.test_mcp_service
  proof: python -m unittest tests.test_mcp_service
}

EXPECTATION: MCP Tool Contract Documentation {
  developer_expectation: Agent maintainers should be able to integrate with Entigram MCP tools without reverse-engineering tests or source code.
  implementation_rule: The repository must ship `docs/mcp-tools.md` with request and response examples for `etg_get_schemas`, `etg_propose_alignment`, and `etg_log_conflict`.
  validation_check: python -m unittest tests.test_entigram_self_improvement_model
  proof: python -m unittest tests.test_entigram_self_improvement_model
}

EXPECTATION: Tamper-Evident Audit Bundles {
  developer_expectation: Operators should be able to export local ledger evidence and delivery state into a portable tamper-evident bundle.
  implementation_rule: The broker must expose `export-audit` and compute a SHA-256 digest over canonical JSON for the exported audit payload.
  validation_check: python -m unittest tests.test_delivery_ledger
  proof: python -m unittest tests.test_delivery_ledger
}

EXPECTATION: Immutable Gate Smoke Demo {
  developer_expectation: A new evaluator should be able to prove Entigram's model, gate, and audit loop with one local command.
  implementation_rule: `scripts/demo_immutable_gate.py` must demonstrate schema discovery, hallucinated entity rejection, valid alignment proposal, conflict logging, delivery anchoring, and audit bundle hashing.
  validation_check: python -m unittest tests.test_entigram_self_improvement_model
  proof: python -m unittest tests.test_entigram_self_improvement_model
}

EXPECTATION: Headless CLI UI Boundary {
  developer_expectation: Entigram's CLI/MCP install should remain headless by default while giving clear installation guidance for the optional dashboard.
  implementation_rule: Streamlit must be declared as a UI extra and `etg ui` must explain pipx installation/injection when Streamlit is missing.
  validation_check: python -m unittest tests.test_cli_integration
  proof: python -m unittest tests.test_cli_integration
}

EXPECTATION: CLI Version Introspection {
  developer_expectation: Operators should be able to verify the installed Entigram CLI version without inspecting package metadata or Homebrew internals.
  implementation_rule: `etg --version` must print the Entigram package version and exit successfully.
  validation_check: python -m unittest tests.test_cli_integration
  proof: python -m unittest tests.test_cli_integration
}

EXPECTATION: Workspace Manifest Version Clarity {
  developer_expectation: Workspace manifests should not imply that the installed Entigram package is stale when they are only describing workspace metadata.
  implementation_rule: New manifests must use `workspace_schema_version` for manifest schema compatibility, and hydration must report package version separately.
  validation_check: python -m unittest tests.test_hydration
  proof: python -m unittest tests.test_hydration
}

EXPECTATION: Signed Audit Bundles {
  developer_expectation: Exported audit bundles should be identity-authenticated with a local signing key, not only content-addressed.
  implementation_rule: `etg broker export-audit` must sign canonical audit payload JSON with Ed25519 and include the public key, key id, and signature in the bundle.
  validation_check: python -m unittest tests.test_delivery_ledger
  proof: python -m unittest tests.test_delivery_ledger
}

EXPECTATION: Entigram 1.6 Release Note {
  developer_expectation: The 1.6 release should clearly explain the enterprise-facing value of audit bundles, the MCP contract, and the Immutable Gate smoke test.
  implementation_rule: `CHANGELOG.md` must include a concise 1.6.0 release note centered on signed audit bundles, documented MCP tools, and the deterministic smoke demo.
  validation_check: python -m unittest tests.test_entigram_self_improvement_model
  proof: python -m unittest tests.test_entigram_self_improvement_model
}

/* Updated: 2026-05-20 Headless Antigravity engine execution via stdin piping support added */
/* Updated: 2026-06-04 Entigram self-improvement loop modeled with expectations, evidence, evaluations, proposals, and lessons */
/* Updated: 2026-06-04 Commissioner companion features: Entigram_Delivery_Snapshot, Blocked lifecycle, proof_capabilities, improvement proposals */
/* Updated: 2026-06-04 Artifact-centered delivery snapshots for source-control-neutral workspaces */
/* Updated: 2026-06-04 Delivery drift status modeled as a read-only recommission signal */
/* Updated: 2026-06-04 Out-of-the-box expectation guard added for agent pre-handoff verification */
/* Updated: 2026-06-17 Release orchestration modeled for PyPI, Homebrew, standard packages, website/docs, and checksum verification */
/* Updated: 2026-06-18 Release PR governance rules modeled for conventional commits, release-please eligibility, manual dispatch discipline, and immutable published versions */
/* Updated: 2026-06-20 Canonical cross-agent governance policy modeled for discoverability and deterministic handoff */
/* Updated: 2026-06-20 MCP schema scope and machine-readable gate responses modeled */
/* Updated: 2026-06-20 MCP docs, audit bundle export, smoke demo, and UI extra boundary modeled */
/* Updated: 2026-07-30 Transparent usage accounting and reversible workspace lifecycle modeled */
/* Updated: 2026-06-20 CLI versioning, workspace manifest clarity, signed audit bundles, and 1.6 release note modeled */
/* Updated: 2026-07-05 Agent capability routing and token-window hibernation modeled */
