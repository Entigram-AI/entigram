# Model Reviewer Checklist

## Scope
Files matching: `*.lds`, `schema.lds`, `entigram/schema_compiler/**`, `migrations/**`

## P0 — Critical (blocks merge)
- [ ] Required field removed without migration
- [ ] Entity deleted that is referenced by other entities
- [ ] Foreign key points to non-existent entity
- [ ] Schema compiler crashes on the changed schema (`python -m entigram.schema_compiler.main`)
- [ ] Circular dependencies introduced between entities

## P1 — High (should fix)
- [ ] Enum values removed instead of deprecated (breaks existing data)
- [ ] Relationship cardinality changed without migration (e.g. `one` → `many`)
- [ ] Missing `NOT NULL` or default on new required field
- [ ] Graph builder edges don't match declared schema relationships
- [ ] Entity naming violates convention (must be PascalCase)

## P2 — Medium (fix or follow-up)
- [ ] Field naming violates convention (must be snake_case)
- [ ] Missing docstring/comment on new entity or field
- [ ] Orphaned entity (defined but never referenced from any other entity)
- [ ] Schema linter warnings not addressed
- [ ] Index missing on foreign key field

## P3 — Low (optional)
- [ ] Entity could be split (SRP for data models)
- [ ] Redundant fields that could be computed
- [ ] Inconsistent field ordering across similar entities
- [ ] Missing version annotation on schema changes
