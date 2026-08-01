# Schema Linter Checklist

## Scope
Files matching: `*.lds`, `schema.lds`, `entigram/schema_compiler/**`

## P0 — Critical (Must Fix / Hydration Blocker)
- [ ] Schema compiler syntax or parse error on any `*.lds` file (`python -m entigram.schema_compiler.main`)
- [ ] Missing primary key or entity identifier on domain entity definition
- [ ] Circular entity inheritance or relationship dependency loops between schemas

## P1 — High (Should Fix)
- [ ] Foreign key or relation reference pointing to a non-existent entity or field
- [ ] Data type incompatibility across foreign key relationship bounds
- [ ] Breaking schema change without backward compatibility annotations or migration spec
- [ ] Duplicate entity name declarations within the same workspace scope

## P2 — Medium (Fix or Track)
- [ ] Entity naming violates convention (must be PascalCase)
- [ ] Field naming violates convention (must be snake_case)
- [ ] Missing documentation annotation (`description`) on entity or public field
- [ ] Orphaned entity defined without any incoming or outgoing relationships
- [ ] Schema linter warnings emitted by compiler check

## P3 — Low (Optional)
- [ ] Suboptimal field ordering across similar entity models
- [ ] Missing index recommendation on high-cardinality foreign key fields
- [ ] Inconsistent whitespace or formatting in `.lds` definition files
