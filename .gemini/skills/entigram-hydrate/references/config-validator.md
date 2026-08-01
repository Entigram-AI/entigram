# Config Validator Checklist

## Scope
Files matching: `.etg/entigram.yaml`, `entigram.yaml`, `.etg/config.yaml`

## P0 — Critical (Must Fix / Hydration Blocker)
- [ ] Syntax or YAML parse error in `.etg/entigram.yaml`
- [ ] Missing mandatory top-level sections (`version`, `workspace_id`, `capabilities`)
- [ ] Capability contracts referencing undefined capabilities or malformed contract signatures

## P1 — High (Should Fix)
- [ ] Schema violation in capability contract definitions (invalid protocol, missing input/output schema)
- [ ] Missing required metadata fields (`name`, `owner`, `environment`, `compliance_tier`)
- [ ] Conflicting workspace IDs or duplicate capability name declarations
- [ ] Incompatible configuration version specified for current CLI tooling

## P2 — Medium (Fix or Track)
- [ ] Omission of recommended configuration blocks (`telemetry`, `defaults`, `logging`)
- [ ] Deprecated configuration key usage (legacy v1 config keys)
- [ ] Unresolved environment variable placeholders without fallback values
- [ ] Invalid URI format for external service or registry endpoints

## P3 — Low (Optional)
- [ ] Naming convention mismatch in config keys (e.g. mixed camelCase and snake_case)
- [ ] Redundant configuration values matching systemic defaults
- [ ] Missing inline comments explaining custom capability flags
