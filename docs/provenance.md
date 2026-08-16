# Provenance and history

Entigram records governed work as evidence-bearing events rather than a stream
of opaque agent messages. The local ledger powers two read-only views:

```bash
etg history --dir .
etg provenance --dir . --event <event-id>
```

`history` is a concise timeline. Each row reports the time, event kind,
outcome, attributed actor or agent, summary, and a stable event ID.

`provenance` expands that ID into the underlying record. For an action decision
it includes the target, contract and request digests, Warden fingerprint,
authority signer, approval result, evidence checks, policy result, and stated
remediation. For a trust transition it includes the exact public-key change and
the independent signed approvals that authorized it.

The local ledger is a working record. For portable third-party review, export a
signed bundle after handoff:

```bash
etg broker --dir . export-audit --out entigram-audit.json
```

The bundle carries its signer public key, current delivery anchor, evidence,
action decisions, and trust transitions. An already-exported bundle remains
verifiable even if the local audit key is later unavailable.
