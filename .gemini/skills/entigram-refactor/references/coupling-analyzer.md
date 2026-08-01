# Coupling & Responsibility Analyzer Checklist

## Tight Coupling & Code Smells
- [ ] **Circular Dependencies**: Modules directly or transitively importing each other
- [ ] **God Class / God File**: Source files >500 lines, >10 public methods, or doing data processing + I/O + UI
- [ ] **Feature Envy**: Methods accessing properties/methods of another class more than their own
- [ ] **Inappropriate Intimacy**: Classes accessing private/internal details of other classes
- [ ] **Shotgun Surgery**: Making a single change requires small edits across many different files

## Single Responsibility Principle (SRP) Audit
- [ ] Module handles multiple distinct domains (e.g. HTTP routing + validation + DB queries)
- [ ] Functions exceeding 40 lines performing multiple logical operations
- [ ] Data structures acting as both data containers and heavy domain service logic
- [ ] Mixed abstraction levels within a single class or method

## Severity Mapping
- **P0 Critical**: Direct circular dependencies, breaking dependency cycles required
- **P1 High**: God classes/files with >3 distinct responsibilities, preventing isolation
- **P2 Medium**: Feature envy, high fan-out coupling, leaky abstractions
- **P3 Low**: Minor SRP violations, slight parameter coupling
