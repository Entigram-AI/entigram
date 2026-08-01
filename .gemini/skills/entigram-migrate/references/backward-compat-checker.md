# Backward Compatibility Checker Reference Checklist

## Objective
Verify that existing queries, API consumers, graph builder functions, and Entigram schema/ontology compilers continue to operate seamlessly with the new schema, or flag broken integration touchpoints.

## Inspection Targets

### 1. Schema & Ontology Compilers
- Verify `entigram.schema_compiler` can parse the updated `.lds` schema file without syntax or semantic errors.
- Verify `entigram.ontology_compiler` can compile the updated `.ttl` ontology file without hierarchy cycles (DAG errors) or undefined prefix errors.

### 2. Graph Builder Components
- Check graph builder relationship mapping (e.g. `relates_X_to_Y` predicates in TTL vs `RELATIONSHIP` blocks in LDS).
- Ensure edge creation logic matches updated entity relationship constraints (`[MUST]` vs `[MAY]`, `(1)` vs `(MANY)`).

### 3. Downstream Consumers & Queries
- Audit existing SPARQL queries for references to deleted or renamed properties/classes.
- Audit SQL queries against SQLite ledger database for removed or renamed columns/tables.
- Check Python service functions and CLI commands (`etg assess`, `etg compile`) for schema assumptions.

## Compatibility Assessment Matrix

| Category | Status | Details | Remediations Needed |
|----------|--------|---------|----------------------|
| Schema Compiler | PASS / FAIL | Compiler output analysis | Fix LDS format syntax |
| Ontology Compiler | PASS / FAIL | TTL compilation & DAG check | Fix prefix or class hierarchy |
| Graph Builder | PASS / FAIL / WARN | Edge builder signature match | Update relationship builder |
| SPARQL Queries | PASS / FAIL / WARN | Query compatibility | Add SPARQL query aliases |
| SQLite Ledger Queries | PASS / FAIL / WARN | Direct table/column select checks | Update DAO / query methods |

## Required Output Format for Agent

```markdown
## Backward Compatibility Checker Results

### Verification Summary
- **Overall Status**: COMPATIBLE / COMPATIBLE_WITH_WARNINGS / BREAKING_CHANGES_DETECTED
- **Compiler Compatibility**: PASS
- **Graph Builder Compatibility**: PASS / WARN
- **Downstream Consumer Impact**: N items affected

### Detailed Compatibility Impact Analysis

#### 1. Compilers
- LDS Schema Compiler: Validated successfully.
- TTL Ontology Compiler: Validated successfully.

#### 2. Graph Builder & Edge Constructors
- [Pass/Warn/Fail description]

#### 3. Broken Downstream Touchpoints (if any)
- **File / Query**: `path/to/query.py:45`
  - Issue: References deleted attribute `statement`
  - Recommended Fix: Alias to `goal_statement` or update query string
```
