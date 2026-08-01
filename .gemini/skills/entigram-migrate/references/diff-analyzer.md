# Diff Analyzer Reference Checklist

## Objective
Compare old vs new Entigram LDS (`*.lds`) schema definitions and TTL (`*.ttl`) ontology files to identify all additions, modifications, and breaking changes.

## Scope
- LDS files: `*.lds`, `schema.lds`, `draft_schema.lds`
- TTL files: `*.ttl`, `schema.ttl`, `draft_schema.ttl`

## Detection Rules & Risk Categories

### 🔴 Critical Breaking Changes (P0 - High Risk)
- **Removed Entity / Class**: An entity in `.lds` or `owl:Class` in `.ttl` was deleted.
- **Removed Field / Attribute**: An attribute from an `ENTITY` or `owl:DatatypeProperty` was deleted.
- **Type Incompatibility**: Data type changed in an incompatible way (e.g., `Decimal` -> `Integer`, `Date` -> `Integer`, `xsd:dateTime` -> `xsd:boolean`).
- **Cardinality Restriction**: Relationship changed from `(MANY)` to `(1)`, or optional `[MAY]` changed to required `[MUST]`.
- **Domain / Range Shift**: TTL `rdfs:domain` or `rdfs:range` changed to an incompatible class or datatype.

### 🟠 High Severity Changes (P1 - Medium Risk)
- **Renamed Entity or Attribute**: Field/Entity renamed without explicit mapping alias (causes query breakdown if unmapped).
- **Mandatory Field Added**: A new attribute with `[MUST]` constraint added without default value specification.
- **Relationship Type Modification**: Relationship semantics modified (e.g., composition vs aggregation changes).

### 🟡 Medium Severity Changes (P2 - Low Risk)
- **New Optional Attributes**: Attribute added with `[MAY]` or nullable constraint.
- **New Optional Relationship**: Added new `[MAY]` relationship between existing or new entities.
- **Enum Value Additions**: Additional values added to string type constraints or comments.

### 🟢 Low Severity / Non-Breaking (P3 - Informational)
- **New Entity / Class Added**: Brand new entity or class created with no impact on existing schemas.
- **Metadata / Documentation Updates**: Comments, `rdfs:label`, `rdfs:comment`, or formatting updates.

## Required Output Format for Agent

```markdown
## Diff Analyzer Results

### Breaking Changes (P0 / P1)
| Target File | Change Type | Element | Description | Risk Level |
|-------------|-------------|---------|-------------|------------|
| schema.lds  | Removed Field | `Strategic_Goal.statement` | Field deleted from entity | P0 |

### Non-Breaking Changes (P2 / P3)
| Target File | Change Type | Element | Description | Risk Level |
|-------------|-------------|---------|-------------|------------|
| schema.lds  | Added Attribute | `KPI.baseline` | Optional Decimal field added | P2 |

### Summary Matrix
- Total Breaking Changes: N
- Total Non-Breaking Changes: N
- High-Risk Entities Affected: [Entity1, Entity2]
```
