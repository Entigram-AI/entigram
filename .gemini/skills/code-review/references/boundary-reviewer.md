# Boundary Conditions Reviewer Checklist

## P0 — Critical
- [ ] Null/None dereference on untrusted input (no guard)
- [ ] Array/list index access without bounds check
- [ ] Division by zero possible with user-provided denominator

## P1 — High
- [ ] Off-by-one error in loop bounds or slice indices
- [ ] TOCTOU race condition (check-then-act without lock)
- [ ] Empty collection not handled (first/last element access)
- [ ] Integer overflow/underflow on arithmetic with external values

## P2 — Medium
- [ ] Missing handling for empty string input
- [ ] Unicode edge cases not considered (NFC normalization, emoji)
- [ ] Timezone-unaware datetime comparison
- [ ] Path handling doesn't account for symlinks or relative paths

## P3 — Low
- [ ] Magic numbers without named constants
- [ ] Missing boundary case in unit tests
- [ ] Defensive copy not made for mutable input
