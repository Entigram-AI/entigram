# Ontology Validator Checklist

## Scope
Files matching: `*.ttl`, `*.rdf`, `*.owl`, `schema.ttl`, `entigram/ontology_compiler/**`

## P0 — Critical (Must Fix / Hydration Blocker)
- [ ] Turtle/RDF syntax error (missing period, unclosed quote, invalid token) blocking compiler execution
- [ ] Circular class hierarchy (`rdfs:subClassOf` loop) causing taxonomy cycles
- [ ] Undeclared namespace prefix used in entity or predicate terms

## P1 — High (Should Fix)
- [ ] Property domain (`rdfs:domain`) or range (`rdfs:range`) referencing non-existent class
- [ ] Disjoint class violation where an instance claims membership in mutually exclusive classes
- [ ] Severe misalignment between Turtle ontology concepts and LDS schema entity models
- [ ] Invalid cardinality restrictions or malformed OWL class definitions

## P2 — Medium (Fix or Track)
- [ ] Missing `rdfs:label` or `rdfs:comment` annotations on ontology classes/properties
- [ ] Use of deprecated ontology terms without explicit `@deprecated` annotation
- [ ] Namespace URI inconsistency or conflicting prefix alias across TTL files
- [ ] Ontology version IRI missing or out of sync with workspace manifest

## P3 — Low (Optional)
- [ ] Suboptimal prefix declaration order in Turtle file header
- [ ] Missing subject/predicate predicate alignment formatting
- [ ] Unused namespace prefix declarations in header
