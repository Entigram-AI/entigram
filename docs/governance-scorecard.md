# Entigram Governance Scorecard

This is Entigram's maintained benchmark for closed-world agent governance. It
turns the project thesis into a reviewable score rather than a generic claim of
"agent safety." It can score Entigram releases, a workspace configuration, or
an alternative implementation using the same evidence standard.

The design combines the recurring principles in the research that informed this
project: probabilistic agents need symbolic admission boundaries; discovery is
useful but must not automatically become operational truth; durable evidence
must accompany action; and autonomous loops need bounded, human-reversible
checkpoints.

## Scoring method

Each dimension receives a 0--5 rating, then contributes `weight × rating / 5`
points. Weights total 100. This is an implementation score, not a compliance
certification.

| Rating | Meaning |
| --- | --- |
| 0 | Absent. |
| 1 | Intent or documentation only. |
| 2 | Partial or manual implementation. |
| 3 | Implemented with inspectable evidence. |
| 4 | Enforced in the normal workflow and tested. |
| 5 | Enforced, tested, durable under failure/restart, and independently reviewable. |

| Dimension | Weight | What is being scored |
| --- | ---: | --- |
| Closed-world action contract | 14 | Explicit entities, relations, authority, and rejection of unknown state. |
| Discovery-to-admission separation | 10 | Untrusted discovery produces proposals; an explicit gate makes operational facts. |
| Action admission and least privilege | 12 | Side effects pass an authorization gate with the narrowest practical authority. |
| Context hydration and continuity | 9 | Policy and contract load at session start, resume, and handoff boundaries. |
| Drift detection and bounded checkpoints | 10 | File or state drift is measured and forces a bounded check-in. |
| Evidence, provenance, and ledger integrity | 10 | Decisions and deliveries carry durable, reviewable evidence. |
| Human escalation and reversible exceptions | 8 | Pauses and overrides are explicit, bounded, and recoverable. |
| Validation and delivery handoff | 10 | Tests, impact checks, and a current delivery state precede handoff. |
| Multi-agent semantic coordination | 6 | Agents share a contract, conflict path, and settled decisions. |
| Agent portability and interoperability | 5 | The control model works across agents without pretending every host has equal hooks. |
| Security, privacy, and resilience | 4 | Integrity, untrusted-artifact handling, local control, and failure behavior. |
| Adoption and progressive disclosure | 2 | A new builder can begin quickly without losing the path to rigor. |

## How to run it

The versioned rubric and Entigram baseline live in `benchmarks/`.

```bash
etg benchmark --report benchmarks/entigram-governance-baseline.json
etg benchmark --report benchmarks/entigram-governance-baseline.json --json
```

An alternative implementation copies the baseline report, points `profile` at
the unchanged rubric, and replaces scores and evidence. A score of 3 or higher
requires at least one inspectable artifact. The report output includes evidence
coverage so an unsupported high score is visible.

## Entigram v1 baseline: 86.4 / 100

The current baseline is intentionally conservative. Entigram scores strongly on
the closed-world contract, delivery evidence, and handoff validation. Native
adapters now load the canonical policy and schema, gate MCP calls, and the
portable Git guard supports standard linked worktrees. It does not score maximum
points because hosts can bypass local hooks and not every agent exposes a native
interception surface.

| Dimension | Current rating | Principal limit to improve |
| --- | ---: | --- |
| Closed-world action contract | 5/5 | Maintain independent review evidence. |
| Discovery-to-admission separation | 4/5 | Broaden formalized admission coverage. |
| Action admission and least privilege | 4/5 | Cover hosted and other bypassing host tool surfaces. |
| Context hydration and continuity | 4/5 | Prove restart, compaction, and sub-agent continuity. |
| Drift detection and bounded checkpoints | 4/5 | Add independently reproducible drift fixtures. |
| Evidence, provenance, and ledger integrity | 5/5 | Maintain independent audit evidence. |
| Human escalation and reversible exceptions | 4/5 | Exercise more exception/recovery scenarios. |
| Validation and delivery handoff | 5/5 | Maintain independent delivery evidence. |
| Multi-agent semantic coordination | 4/5 | Measure coordination quality across real agent teams. |
| Agent portability and interoperability | 4/5 | Add verified adapters; retain the generic backstop. |
| Security, privacy, and resilience | 4/5 | Expand hostile-host and failure-mode testing. |
| Adoption and progressive disclosure | 3/5 | Measure time-to-first-governed-work for new builders. |

The next material score improvements should come from proving native adapter
coverage across more agent hosts, strengthening restart/compaction hydration,
and adding independently reproducible benchmark fixtures.
