# Blast Radius Analyzer Checklist & Guideline

## Objective
Trace the downstream and upstream impact of the incident from the failing component through schema contracts, import graphs, and runtime call flows.

## Impact Tracing Checklist
- [ ] **Direct Impact (Tier 1)**: The module, function, or class where the error occurred.
- [ ] **Inbound Callers (Upstream Tier 2)**: Modules and APIs that invoke the failing component.
- [ ] **Outbound Dependencies (Downstream Tier 2)**: Services, databases, or schemas consumed by the failing component.
- [ ] **Schema & Ontology Coupling**:
  - LDS schema files (`*.lds`) referencing affected entities/attributes.
  - TTL ontology files (`*.ttl`) affected by changed entity URIs, properties, or domain/range constraints.
- [ ] **Data Ledger & Graph Store Impact**: Affected SQLite ledger tables, SPARQL query endpoints, or graph builder edge constructors.
- [ ] **Mermaid Diagram Generation**: Construct a clean, precise visual diagram mapping failure propagation using GitHub-compatible Mermaid (`graph TD`).

## Blast Radius Severity Classification
- 🔴 **Tier 1 - Critical System Outage**: Failure cascades to core compilation, data store, or public API endpoints.
- 🟠 **Tier 2 - Feature Degraded**: Secondary features or non-critical integrations broken; workaround available.
- 🟡 **Tier 3 - Isolated Failure**: Error contained within a single optional module or edge-case input handling.

## Output Structure
1. **Affected Components List**: File paths, classes, and methods impacted.
2. **Schema & Data Model Impact**: Affected `.lds`, `.ttl`, and database schemas.
3. **Mermaid Blast Radius Graph**: Visual flowchart showing failure point and impact radius.
4. **Impact Radius Assessment**: High/Medium/Low summary with blast radius boundary explanation.
