# Action admission

Action admission is Entigram's v2 control-plane contract for a consequential
agent action. It is separate from workspace preflight and delivery handoff:
those govern a workspace change; an action contract governs whether an agent
may cause a named effect against a declared target at a particular time.

The first release implements deterministic **admission**, signed local grants,
signed approvals, and an append-only local decision record. It intentionally
does **not** provide `etg action run` yet. A direct command wrapper would let an
agent bypass the governing boundary and would not be a meaningful prevention
claim. The future runner must hold a scoped credential and mediate the target
effect.

## Contract

Create `actions.yaml` in the workspace root. Action contracts are versioned and
Warden fingerprints the file once it is part of a lock.

```yaml
format: entigram.action-contract.v1
actions:
  publish_release:
    version: 1
    assurance: mediated # advisory | mediated | enforced
    reads: [release.status, release.tests]
    writes: [release.status]
    authority:
      scopes: [release.publish]
      principals: [user:founder]
      agents: [agent:codex:desktop]
    preconditions:
      - path: release.status
        equals: staged
    evidence:
      - id: release_tests
        verified: true
        freshness_seconds: 300
        issuer_roles: [evidence_issuer]
        source_kinds: [ci]
    policy:
      id: release_policy
      decision: allow # allow | deny | approval_required | conflicted
    approval:
      required: true
      roles: [release_manager]
    postcondition:
      path: release.status
      equals: published
      observation_deadline_seconds: 600
    compensation:
      action: rollback_release
```

Every contract declares reads, writes, authority scope, a policy outcome, a
typed postcondition, and either a compensation action or
`not_reversible: true`. A missing context fact is `unknown`, not false. A
conflicted policy returns `conflicted`; it does not resolve according to file
or evaluation order.

Review the contract and put it under Warden integrity protection before issuing
or validating authority for it:

```bash
etg warden lock
```

## Local development authority flow

The local authority adapter uses an Ed25519 key under `.etg/`. It is intended
for local development only: its guarantee depends on preventing the agent from
reading or using the private key. Production deployments should use an IAM or
authorization adapter while retaining the same normalized action record.

For a project with more than one human participant, use the public-key trust
registry instead of this adapter. Each person keeps an individual private key
outside the workspace and signs their own grants or approvals. In that mode an
agent must also sign the exact request using a human-enrolled workload key,
runtime, and approved exact version. See [Shared project trust](project-trust.md).

```bash
# Explicitly create the local root; never overwrite it automatically.
etg action --dir . init-authority

# Issue a narrow, time-bounded grant. The output path must stay in the workspace.
etg action --dir . grant \
  --principal user:founder \
  --agent agent:codex:desktop \
  --scope release.publish \
  --expires-at 2026-08-17T18:00:00Z \
  --out grant.json
```

If the delegated scope should no longer be usable, revoke its stable grant ID.
The local ledger preserves the revocation; a copied signed grant remains denied
in this workspace.

```bash
etg action --dir . revoke \
  --grant-id grant-... \
  --by user:founder \
  --reason "Release window closed"
```

The action request is JSON. It contains the identity attributed to the action,
target, current context, and evidence records. Evidence carries a content hash,
observation time, and verification state.

```json
{
  "request_id": "release-publish-001",
  "principal": "user:founder",
  "agent_id": "agent:codex:desktop",
  "target": {"release_id": "release-2026-08-16"},
  "context": {"release": {"status": "staged", "tests": "passed"}},
  "evidence": [{
    "id": "release_tests",
    "sha256": "<64-character SHA-256 digest>",
    "observed_at": "2026-08-16T17:55:00Z",
    "verified": true
  }]
}
```

In local-development mode, `verified: true` is only a local assertion. In a
shared-trust workspace, every contract requirement with `verified: true` also
requires a signed evidence attestation from a current signer with an allowed
`issuer_roles` role. The attestation binds the evidence ID, SHA-256 digest,
observation time, and declared source kind; `source_kinds` limits which source
kinds the contract accepts.

For an action that requires human approval, bind it to the exact action and
evidence digests:

```bash
etg action --dir . approve \
  --name publish_release \
  --request request.json \
  --approver user:founder \
  --role release_manager \
  --expires-at 2026-08-16T19:00:00Z \
  --out approval.json
```

## Validate and audit

```bash
etg action --dir . validate \
  --name publish_release \
  --request request.json \
  --grant grant.json \
  --approval approval.json \
  --json

etg action --dir . decisions --name publish_release --json
```

In a shared-trust workspace, include the agent attestation produced by the
protected adapter host and one evidence attestation for each verified evidence
record. A string in `agent_id` is not sufficient. The runtime and version are
claims made by that host; use a KMS, managed adapter, or another host boundary
that verifies them before signing if the assurance claim depends on the actual
binary version.

```bash
etg action --dir . attest \
  --name publish_release --request request.json \
  --agent agent:codex:desktop --runtime codex --version 2026.08.16 \
  --expires-at <time-no-more-than-15-minutes-after-issuance> \
  --out agent-attestation.json

etg action --dir . attest-evidence \
  --request request.json --evidence release_tests \
  --issuer user:alice --source-kind ci --source-uri <immutable-ci-receipt> \
  --out release-tests-attestation.json

etg action --dir . validate \
  --name publish_release --request request.json \
  --grant grant.json --approval approval.json \
  --agent-attestation agent-attestation.json \
  --evidence-attestation release-tests-attestation.json --json
```

Validation does not execute the target effect. It verifies Warden integrity,
the action contract, human authority signature/scope/expiry, enrolled agent
key/runtime/version and request binding, typed preconditions, trusted evidence
provenance and freshness, policy result, and approval binding. On an admitted
shared-trust action, it atomically consumes the short-lived agent attestation
ID and nonce in that broker's ledger while recording the decision, preventing a
replay through the same broker. A cross-host executor must enforce equivalent
single-use handling at its credential or target boundary. Every result—including denials,
unknown preconditions, stale evidence, and conflicts—is appended to the local
ledger with the model fingerprint and relevant digests.

Local grants are revoked in the local ledger. Shared grants are revoked through
the signed, replayed project-trust history so every clone receives the same
decision; see [Shared project trust](project-trust.md).

An admitted action is still only admitted for an executor with the declared
assurance level:

| Assurance | Meaning |
| --- | --- |
| `advisory` | Entigram can ask for or observe the check, but the host can bypass it. This is detection, not prevention. |
| `mediated` | A governed tool or proxy owns the scoped credential and accepts only a current admitted decision. This is the minimum for a prevention claim. |
| `enforced` | Sandbox, network, credential, or target-system controls prevent bypass of the mediator. Required for high-blast-radius assurance. |

Changing `actions.yaml` after a Warden lock requires the same explicit review
path as another governed contract change:

```bash
etg warden unlock
# review the action-contract change
etg broker handoff --accept-contract-change
```

## Native tool parameter admission

The portable `admit_tool_proposals` API and the hydrated Sentinel mediator use
the same parameter normalization and JSON Schema validation. Flattened field
maps (`{field: {type: ..., required: true}}`) are normalized for both the model
provider and admission, so provider-facing constraints are not lost at the gate.
Native schemas retain nested constraints, composition, nullable types, and local
references. Invalid schemas, unknown dialects, and unresolved references deny
the proposal. Validation does not fetch referenced schemas over the network or
from local files. `format` is an annotation, not an enforced assertion; use
explicit assertion keywords when a format must be enforced.

The Responses adapter explicitly defaults function tools to `strict: false`
to prevent implicit provider normalization from making optional fields required.
An explicit producer `strict: true` declaration is preserved. Entigram still
validates proposed calls against the original contract; non-strict generation
does not disable admission checks.

Policy citation checks apply to citation names such as `policy_reference` and
`policy_sections_cited`, not arbitrary business references such as
`customer_reference`. A producer can declare a property's semantics explicitly
with the boolean `x-entigram-policy-reference` annotation. Model-facing citation
constraints never expand an existing producer enum, and admission does not
rewrite argument values. These checks validate a proposed call; they do not
prove its semantic correctness or that the complete workflow has finished.

Sentinel gives a rejected action draft one repair attempt. If any call fails
admission, the entire original batch is withheld before dispatch; the planner
receives the denial codes and missing prerequisites and may propose a replacement.
Every replacement is checked again against the unchanged contract and observed
state. Repeated rejection or provider failure releases no calls. Decision events
for these attempts include `proposal_attempt` and `dispatch_released`, separating
individual-call admission from actual release of the batch. This is not a
transaction guarantee for failures that occur after the external host executes
an admitted batch.

### Experimental sequential plan execution

`ENTIGRAM_SENTINEL_PLAN_EXECUTION=sequential` opts Sentinel into a session-local
ordered plan instead of a second model independently selecting calls from an
advisory draft. Every action still passes the same admission gate. Only one call
is released at a time; the next step requires a successful receipt matching the
previous call ID, tool name, and arguments. Failed or mismatched receipts discard
the remaining plan. New user instructions invalidate pending work; an already
dispatched call must return before replanning, because it cannot be canceled by
discarding a local queue.

On the unchanged request, a completed plan enters a tool-free response phase.
Across follow-up messages, sequential mode rejects exact repeats of successful
calls in the same session unless the producer's parameter schema explicitly
declares `x-entigram-repeatable: true` (for example, for polling). This guards
identical replays, not semantically equivalent calls with different arguments;
target-side idempotency remains necessary for consequential effects.

The default remains `advisory` pending evaluation. Sequential mode requires the
bootstrap session protocol to preserve its queue across requests. It is not a
durable execution ledger, does not infer missing policy obligations, and does not
make model-proposed plans authoritative. Tool-result success conventions and the
existing mediator's policy interpretation retain their documented limitations.

`ENTIGRAM_SENTINEL_PLAN_REVIEW=1` adds an experimental semantic veto in sequential
mode. A separate model invocation reviews the exact proposed plan, including
text-only replies, against runtime policy, declared contracts, and conversation
evidence before it enters the queue. Completed-plan customer responses are also
reviewed. The reviewer has no business tools, cannot rewrite actions, and cannot
override native admission. Malformed, contradictory, incomplete, unavailable,
or negative verdicts withhold the draft. A rejected new plan gets one replanning
attempt; a rejected completed-plan response becomes a neutral fallback. Events
report review approval and scope without exposing reviewer rationale.
Server logs distinguish parsing, review-format, semantic-review, and native
admission failures using bounded gate metadata; draft contents, policy text,
reviewer rationale, and provider exception bodies are not included in review logs.

This is a fallible model-based check for semantic errors and missing obligations,
not a verified policy compiler, independent human approval, or production safety
guarantee. It uses the configured Sentinel model and adds latency and inference
cost. Approval of an ordered plan does not prove future results: the existing
receipt gate still governs progression, but semantic conditions that change on
an otherwise successful receipt require richer conditional planning. No benchmark
identifiers, hidden fixtures, or externally preloaded policy mappings are supplied.
