# Ontology Reviewer Checklist

## Scope
Files matching: `*.ttl`, `*.rdf`, `*.owl`, `entigram/ontology_compiler/**`

## P0 — Critical (blocks merge)
- [ ] TTL syntax error (missing periods, unbalanced brackets)
- [ ] Namespace URI collision or redefinition
- [ ] Class hierarchy cycle (`rdfs:subClassOf` creates a loop)
- [ ] Ontology compiler crashes on the changed files
- [ ] `owl:sameAs` or `owl:equivalentClass` creates unintended identity collapse

## P1 — High (should fix)
- [ ] Property `rdfs:domain` or `rdfs:range` references undefined class
- [ ] Class deleted that is referenced by properties or other classes
- [ ] Prefix declaration missing for a used namespace
- [ ] Inverse properties not declared symmetrically
- [ ] Disjoint class declaration contradicts existing instance data

## P2 — Medium (fix or follow-up)
- [ ] Missing `rdfs:label` on new class or property
- [ ] Missing `rdfs:comment` on new class or property
- [ ] Deprecated class/property not annotated with `owl:deprecated true`
- [ ] Inconsistent naming convention (URIs should use CamelCase for classes, camelCase for properties)
- [ ] Dangling reference (property references class not defined in any loaded ontology)

## P3 — Low (optional)
- [ ] Verbose Turtle that could use `;` shorthand for same subject
- [ ] Redundant type declarations (already implied by hierarchy)
- [ ] Missing `owl:versionInfo` on ontology changes
- [ ] Could use `rdfs:subPropertyOf` for related properties
- [ ] Comment/label language tags missing (e.g. `@en`)
