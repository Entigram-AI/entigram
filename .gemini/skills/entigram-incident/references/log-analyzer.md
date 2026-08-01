# Log Analyzer Checklist & Guideline

## Objective
Analyze error messages, stack tracebacks, system logs, and diagnostic dumps to identify the precise error pattern, failing component, and underlying root cause signal.

## Inspection & Extraction Checklist
- [ ] **Error Signal Extraction**: Identify exact exception types (e.g., `AttributeError`, `SchemaValidationError`, `SPARQLQuerySyntaxError`, `KeyError`, `DatabaseLockedError`).
- [ ] **Frame Analysis**: Deconstruct stack trace frames from top-level entry point down to leaf node where exception was raised.
- [ ] **State & Context Detection**: Extract local variable values, method signatures, parameter values, or environment flags present in the log snippet.
- [ ] **Pattern Classification**: Match failure signatures against common failure modes:
  - *Data Contract Breakage*: Schema/ontology mismatch, missing fields, type coercion failure.
  - *Null / Undefined Pointer*: Unhandled `None` or null object reference dereference.
  - *Concurrency / Resource Lock*: Database lock timeout, deadlocks, resource exhaustion.
  - *Dependency / Import Failure*: Circular import, missing package, broken module path.
  - *Configuration Failure*: Missing environment variables, invalid config paths, malformed LDS/TTL syntax.
- [ ] **Root Cause Hypothesis**: Formulate a concise root cause statement detailing *why* the failure occurred, not just *where*.

## Output Structure
Each log analysis report should contain:
1. **Primary Exception**: Type, message, and line location (`file:line`).
2. **Deconstructed Traceback**: Sequence of calls leading up to failure.
3. **Error Pattern Classification**: Severity grade (P0 Outage, P1 Degraded, P2 Functional Defect) and category.
4. **Root Cause Analysis (RCA)**: Deep-dive explanation of the underlying failure mechanism.
