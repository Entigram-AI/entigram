# Git-native collaboration

Entigram uses Git as the collaboration system of record. Git owns commit
history, branches, and byte-level conflicts; Entigram adds a conservative
semantic check for governed LDS contracts.

## Install explicitly

```bash
etg git install --merge-driver --ci github
```

This preserves an existing pre-commit hook, configures a local `*.lds` merge
driver, and writes a reviewable GitHub Actions required-check template. Add
`--shared` only when the repository should track `.gitattributes` for every
contributor.

## Review a branch

```bash
etg git status --base origin/main
etg git check --base origin/main --write-evidence
```

The command uses Git's merge base and classifies entity and relationship
changes. It automatically accepts only unilateral, identical, or non-
overlapping additive changes. Concurrent non-identical edits to the same
entity or relationship require review.

Assessments are written under `.etg/evidence/merges/`; they are intended to be
committed with the branch. SQLite remains a local query index, not the
collaborative source of truth.

## Resolve and hand off

```bash
etg git resolve --report .etg/evidence/merges/<report>.json \
  --conflict GIT-ENTITY-<id> --strategy theirs \
  --rationale "The data-owner approved the remote type" --apply
etg git rebase-check --base origin/main --report .etg/evidence/merges/<report>.json
etg git handoff --base origin/main
```

`resolve` always writes an append-only resolution record. `--apply` is
explicit because it rewrites the affected LDS file. `handoff` creates a
commit-bound bundle and gives it to the normal broker handoff as an anchored
delivery artifact.

The GitHub template uses read-only permissions and emits a required check plus
a downloadable JSON artifact. Configure CODEOWNERS and branch protection in
GitHub itself; Entigram reports matching owners but does not impersonate GitHub
approval enforcement.
