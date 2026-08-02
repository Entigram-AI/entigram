
<!-- ENTIGRAM_START -->
# Entigram Agent Context
You are a governed agent operating within the **Entigram Semantic Governance Layer**.

## Canonical Governance Policy
Read and follow `.etg/agent_policy.md` before changing this repository. If this
file conflicts with `.etg/agent_policy.md`, the canonical policy wins.

## Workspace Context
- **Manifest:** Read `.etg/entigram.yaml` for project metadata and active packages.
- **Packages:** SupplyChain
- **Decisions Ledger:** Contradictions must be resolved via the human tie-breaker ledger at `.etg/state.db`.

## Primary Directives
1. **Schema-First Control:** You operate under a closed-world assumption defined by the Entigram Schema in `schema.lds`. Never generate code or ontologies before the Schema is established.
2. **Schema changes:** Treat `schema.lds` and `draft_schema.lds` as governed
   contracts. Change them only when the task requires schema modeling, and
   follow `.etg/agent_policy.md` for the required lock and handoff flow.
3. **Portable Broker Flow:** Use the current Entigram CLI defaults:
   - **Hydrate First:** `hydrate`
   - **Before Risky Changes:** `etg broker preflight --file <path>` and `etg broker impact --file <path>`
   - **Before Handoff:** `etg broker handoff` and `etg broker status`
   - **Required Final State:** `Delivery status: current`

4. **Domain Isolation:** Treat external systems as black boxes. Prevent unsupported concepts from entering the workflow.
5. **Decisions:** If you encounter a state conflict, propose a resolution via the Broker and wait for human approval in the auditable ledger.
6. **Expectation Guard Pre-Handoff Gate:** If you changed implementation behavior, run the full pre-handoff gate in `.etg/agent_policy.md`. The final `etg broker status` must report `Delivery status: current`.

## Active Package Instructions
- **Package Skills:** You MUST read the `SKILL.md` file for each active package to understand your specific roles and protocols.
<!-- ENTIGRAM_END -->
