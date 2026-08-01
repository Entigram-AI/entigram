# Test Impact Analyzer Checklist

## Test Coverage & Mapping
- [ ] Locate test suites corresponding to target module (e.g. `tests/`, `__tests__/`, `*_test.py`, `*.spec.ts`)
- [ ] Map modified functions/classes/interfaces to existing test cases
- [ ] Identify tests requiring updates due to signature changes or module extractions

## Refactoring Safety Audit
- [ ] **Missing Test Coverage**: Identify key paths lacking test coverage *before* refactoring begins
- [ ] **Characterization Tests**: Recommend characterization tests to lock down legacy behavior
- [ ] **Mocking & Stub Alignment**: Check if test mocks directly depend on internal private methods being moved

## Execution & Risk Mitigation
- [ ] Provide step-by-step test execution commands (`pytest`, `npm test`, etc.)
- [ ] Categorize test impact level per refactoring proposal:
  - **Low Impact**: Pure internal extraction; public signature unchanged; existing tests pass without edits
  - **Medium Impact**: Function signatures updated; test fixtures or test call-sites require minor edits
  - **High Impact**: Architecture split; unit tests need restructuring or new test suites created
