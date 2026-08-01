# Naming & Clarity Reviewer Checklist

## Naming Anti-Patterns
- [ ] **Ambiguous / Generic Names**: `data`, `info`, `item`, `temp`, `res`, `handler`, `manager`, `process()`, `do_stuff()`
- [ ] **Deceptive / Misleading Names**: Function named `get_user()` that also updates database records
- [ ] **Cryptic Abbreviations**: `chk_usr_auth_lvl()`, `calc_amt_wt_tax()`
- [ ] **Type Encoding in Names (Hungarian Notation)**: `str_name`, `arr_items`, `dict_config`
- [ ] **Inconsistent Terminology**: Mixing `fetch`, `get`, `retrieve` or `user`/`account`/`client` for the same domain concepts

## Domain Alignment & Clarity Standards
- [ ] Function names use action verbs reflecting exact operation (e.g., `calculate_taxable_income`, `parse_schema_manifest`)
- [ ] Variable names convey business intent and unit/domain semantics (e.g., `timeout_seconds`, `retry_attempts_count`)
- [ ] Class names use clear noun phrases (e.g., `SchemaValidator`, `DependencyGraphBuilder`)
- [ ] Boolean variables/functions use predicate prefixes (`is_valid`, `has_permission`, `can_execute`)

## Output Standard
- [ ] Table of current symbol names vs. proposed refactored names
- [ ] Explicit rationale for each name change based on domain clarity and maintainability
