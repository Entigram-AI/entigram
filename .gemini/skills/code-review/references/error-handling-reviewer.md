# Error Handling Reviewer Checklist

## P0 — Critical
- [ ] Exception silently swallowed with no logging (`except: pass`)
- [ ] Missing error boundary around critical operations (data writes, payments)
- [ ] Unhandled promise/async rejection that could crash the process

## P1 — High
- [ ] Bare `except Exception` without logging (should at minimum log)
- [ ] Error response leaks internal details (stack trace, file paths, SQL)
- [ ] Missing rollback/cleanup in error path (partial state left behind)
- [ ] Retry logic without backoff or max attempts (infinite retry)

## P2 — Medium
- [ ] Generic exception type where specific type exists
- [ ] Missing `finally` block for resource cleanup
- [ ] Error logged but not propagated when caller needs to know
- [ ] Inconsistent error response format across endpoints

## P3 — Low
- [ ] Missing docstring on custom exception class
- [ ] Error message not actionable (no context about what failed)
- [ ] Could use structured logging instead of string formatting
