
<!-- ENTIGRAM_START -->
# Entigram Agent Context
You are an edge-agent operating within a Entigram Federated Architecture.

## Canonical Governance Policy
Read and follow `.etg/agent_policy.md` before changing this repository. If this
file conflicts with `.etg/agent_policy.md`, the canonical policy wins.

## Workspace Context
- **Manifest:** You MUST read `.etg/entigram.yaml` (using your `read_file` tool) to understand project metadata and active packages.
- **Packages:** Entigram Schemas
- **Decisions Ledger:** Contradictions must be resolved via the human tie-breaker ledger at `.etg/state.db`.

## Primary Directives
1. **Schema First:** Never generate code or ontologies before an Entigram Schema is explicitly defined in `schema.lds`.
2. **Persistence:** You MUST maintain the local `schema.lds` and `draft_schema.lds` files. Update them (using your `replace` or `write_file` tools) after EVERY turn where new domain information is established.
3. **Portable Broker Flow:** Use the current Entigram CLI defaults:
   - **Hydrate First:** `hydrate`
   - **Before Risky Changes:** `etg broker preflight --file <path>` and `etg broker impact --file <path>`
   - **Before Handoff:** `etg broker handoff` and `etg broker status`
   - **Required Final State:** `Delivery status: current`

4. **Domain Isolation:** Treat external systems as black boxes.
5. **Schema Contract Enforcement (Execution Mode):** Once a build is finalized, the `schema.lds` and `schema.ttl` files represent the immutable schema contracts of this workspace. You are forbidden from attempting to rewrite or modify these files during data execution or orchestration. Any attempt to drift from the established schema will trigger a `SCHEMA_GUARD_HALT`.
6. **Initialization Step:** As your first action, read the project manifest and the local `schema.lds` to synchronize your mental model with the current authoritative state.
7. **Expectation Guard Pre-Handoff Gate:** If you changed implementation behavior, run the full pre-handoff gate in `.etg/agent_policy.md`. The final `etg broker status` must report `Delivery status: current`.

## Active Package Instructions
- **Schema Modeling:** Read `interview_prompt.md` and begin the domain modeling interview. Record your progress in `schema.lds`. (Note: For Antigravity, ensure all turns are committed to state).
- **Package Skills:** You MUST read the `SKILL.md` file for each active package to understand your specific roles and protocols.
<!-- ENTIGRAM_END -->


