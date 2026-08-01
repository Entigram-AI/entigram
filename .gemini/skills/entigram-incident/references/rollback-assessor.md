# Rollback Assessor Checklist & Guideline

## Objective
Evaluate whether rolling back recent code or schema changes is safer and faster than applying a forward-fix, taking into account change recency, data migration dependencies, and blast radius.

## Assessment Checklist
- [ ] **Change Recency & History Analysis**:
  - Inspect recent changes/commits touching the failing code path.
  - Determine when the regression was introduced (e.g., latest commit vs. several releases ago).
- [ ] **Schema & Data Migration Dependency**:
  - Assess if recent changes involved destructive LDS schema or TTL ontology migrations (`*.lds`, `*.ttl`).
  - Determine if rollback requires complex down-migration scripts or data loss risk (e.g., SQLite ledger table alterations).
- [ ] **Rollback Safety Score (Rollback vs. Forward-Fix)**:
  - Calculate Rollback Safety Score (High / Medium / Unsafe).
  - Compare estimated Time-To-Recover (TTR) for Rollback vs. Forward-Fix.
- [ ] **Rollback Execution Steps**:
  - Provide explicit local rollback instructions (e.g., local checkout, file restoration, or schema revert).
- [ ] **Post-Rollback Verification**:
  - List verification commands to validate state after rollback.

## Decision Criteria Matrix
- **Choose Rollback if**:
  - Regression was introduced in the most recent commit/change.
  - No destructive database/schema migrations were executed.
  - Forward-fix risk or TTR is significantly higher than rolling back.
- **Choose Forward-Fix if**:
  - Incident involves legacy code or old commits where rollback would revert valid features.
  - Rollback would cause data loss or break incompatible database states.
  - Minimal hotfix (defensive guardrail) has high confidence (>85%) and low effort.

## Output Structure
1. **Rollback Feasibility Assessment**: Safe / Unsafe / Conditional recommendation.
2. **Recency & Commit Context**: Summary of recent changes touching failing components.
3. **Data Loss & Schema Risk Analysis**: Analysis of database/ontology state changes.
4. **Step-by-Step Rollback Execution Plan**: Concrete local commands and procedures.
