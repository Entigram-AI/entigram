# Performance Reviewer Checklist

## P0 — Critical
- [ ] N+1 query pattern in loop (database or API calls)
- [ ] Unbounded memory allocation (reading entire file/dataset into memory)
- [ ] Blocking I/O in async context (sync file/network in async handler)

## P1 — High
- [ ] O(n²) or worse algorithm where O(n log n) or O(n) is possible
- [ ] Missing database index on frequently queried column
- [ ] File handle or connection leak (not closed in error path)
- [ ] Repeated expensive computation that should be cached/memoized

## P2 — Medium
- [ ] String concatenation in loop (should use join/builder)
- [ ] Missing pagination on list/query endpoints
- [ ] Unnecessary serialization/deserialization round-trips
- [ ] Large object copied where reference would suffice

## P3 — Low
- [ ] Could use generator/iterator instead of materializing full list
- [ ] Missing connection pooling configuration
- [ ] Suboptimal data structure choice (list vs set for lookups)
