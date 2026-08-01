# SOLID + Architecture Reviewer Checklist

## P0 — Critical
- [ ] Circular dependency between modules/packages
- [ ] God class (>500 lines, >10 public methods, multiple responsibilities)
- [ ] Dependency on concrete implementation where interface should be used

## P1 — High
- [ ] SRP violation — class/function has multiple unrelated responsibilities
- [ ] OCP violation — modification required where extension should suffice
- [ ] LSP violation — subclass breaks parent's behavioral contract
- [ ] Hidden coupling through global state or singletons

## P2 — Medium
- [ ] ISP violation — interface forces implementation of unused methods
- [ ] DIP violation — high-level module depends on low-level details
- [ ] Missing abstraction layer (business logic in handler/controller)
- [ ] Code duplication across files (DRY violation)

## P3 — Low
- [ ] Function too long (>40 lines) — consider extraction
- [ ] Deep nesting (>3 levels) — consider early returns
- [ ] Naming doesn't reflect intent
- [ ] Missing type hints on public API
