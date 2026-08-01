# Dependency Mapper Checklist

## Core Objectives
- [ ] Map all `import`, `require`, or module dependency statements within the target scope
- [ ] Construct call graphs between major functions, classes, and sub-modules
- [ ] Identify entry points (public APIs, handlers, CLI commands, exported interfaces)
- [ ] Identify core internal business logic and internal helper utilities
- [ ] Detect external boundary dependencies (database access, APIs, file I/O)

## Topology & Metrics Analysis
- [ ] **Fan-in**: High number of dependants relying on a single module (critical node)
- [ ] **Fan-out**: High number of outgoing dependencies from a single module (high complexity node)
- [ ] **Layering Violations**: Low-level utilities calling high-level domain logic
- [ ] **Hidden Dependencies**: Reliance on global variables, environment states, or monkey-patching

## Output Deliverables
- [ ] Visual dependency graph formatted as a `mermaid` flowchart (`graph TD` or `graph LR`)
- [ ] Table of public entry points vs. internal helper components
- [ ] Architectural boundary report highlighting potential module splits
