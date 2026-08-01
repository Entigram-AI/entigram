# Fix Proposer Checklist & Guideline

## Objective
Formulate actionable, code-level fix options to remediate the incident, ranking solutions by safety, confidence score, and implementation complexity.

## Fix Synthesis Checklist
- [ ] **Option 1: Minimal Immediate Hotfix**:
  - Focus: Fastest resolution to restore system stability with minimal code modifications.
  - Scope: Targeted guardrails, defensive null checks, or fallback handlers.
- [ ] **Option 2: Comprehensive Structural Fix**:
  - Focus: Addresses the root cause permanently without technical debt.
  - Scope: Refactoring logic, correcting schema alignment, updating broken method signatures.
- [ ] **Option 3: Workaround / Defensive Mitigation**:
  - Focus: Non-intrusive bypass if primary fix requires extensive testing or data migration.
- [ ] **Confidence Score Calculation (0-100%)**:
  - *Factors*: Root cause certainty, risk of regression, test coverage, blast radius scope.
- [ ] **Code Diff Generation**: Provide precise unified diffs or code snippets (````python```` or ````diff````) for each proposal.
- [ ] **Side-Effect & Regression Risk**: Outline potential side-effects for each proposed fix.

## Confidence & Safety Matrix
| Fix Option | Type | Confidence Score | Safety Rating | Blast Radius Risk | Recommended Verification |
|------------|------|------------------|---------------|-------------------|--------------------------|
| Proposal A | Hotfix (Guardrail) | 90% | High | Low | Run unit tests for module X |
| Proposal B | Structural Fix | 75% | Medium | Medium | Full regression test suite |

## Output Structure
1. **Fix Proposals (Ranked by Safety)**:
   - Proposal Title & Strategy
   - Confidence Score (%) & Rationale
   - Concrete Code Diff / Before-After Code
   - Verification Steps & Test Commands
2. **Comparison Matrix**: Comparative evaluation of all proposed options.
