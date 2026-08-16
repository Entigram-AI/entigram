# Shared project trust

Entigram never requires collaborators to share a private key. Each person owns
an Ed25519 identity key outside the repository; the repository contains only
the public-key registry at `.etg/trust.yaml`.

The registry identifies trusted people, their roles, active and retired keys,
human-owned agent keys, approved runtime versions, recovery quorum, and signed
key-transition events. Its signed root is pinned outside the repository on
each collaborator's host, and every later state is replayed from that root.
Warden also fingerprints the registry, so action admission fails if it is not
under the current lock. A checkout alone can never replace a collaborator's
trust root.

## Set up a project

Each collaborator creates a private key locally. The default location is under
their user configuration directory, not the workspace. `--key` may specify a
different personal path, but Entigram refuses a path inside the workspace.

```bash
# Alice, on her own computer
etg identity create --signer user:alice
etg identity export --signer user:alice --out alice-public.json

# Initialize the project trust registry using Alice's public identity.
etg trust --dir . init --project entigram --owner user:alice

# Bob creates and shares only this public JSON file with the project.
etg identity create --signer user:bob
etg identity export --signer user:bob --out bob-public.json

# In an already locked workspace, explicitly open the reviewed change first.
etg warden --dir . unlock

# A current trust administrator admits Bob and assigns only the required roles.
etg trust --dir . add-signer \
  --signer user:bob --public-key bob-public.json \
  --role release_manager --role recovery_admin \
  --authorized-by user:alice
```

Review the public registry change, then lock the already-unlocked change with
the normal governed contract-change process:

```bash
etg broker --dir . handoff --accept-contract-change
```

`init` pins Alice's signed root in her local Entigram configuration. A new
collaborator must obtain the root digest through an independent channel (for
example, a verified call with Alice), compare it locally, then pin that exact
value before using the shared trust registry:

```bash
# This only displays the root signed in the checkout; compare it out of band.
etg trust --dir . root --json

# Replace the placeholder with the independently confirmed root_digest.
etg trust --dir . pin-root --root-digest <confirmed-root-digest>
```

`trust show` and all trust-backed action operations require this external pin.
If the checked-out root differs from the pin, or its event history cannot be
replayed from that root, Entigram fails closed.

For a new workspace without an existing lock, `etg warden lock` establishes
the initial reviewed fingerprint.

Use `trust_admin` to add members or revoke a compromised key,
`authority_issuer` to issue action grants, action-specific roles such as
`release_manager` to approve those actions, `evidence_issuer` to attest
verified evidence, and `recovery_admin` for key-loss recovery. Set
`--recovery-quorum 2` or higher once more than one independent recovery
administrator is available.

## Enroll agent runtimes

An action request's `agent_id` is not an identity claim. In a shared project,
the adapter host signs each exact request with a protected workload key; a
trust administrator must first enroll that key, its human owner, runtime, and
the exact versions permitted to act. This supports multiple Codex, Claude Code,
CI, or other agents in one workspace without sharing a private key.

Create the agent key where its host can protect it. It must remain outside the
workspace and outside an LLM's prompt or tool-accessible files.

```bash
# Provisioned by the adapter host or its operator, not by an untrusted prompt.
etg identity create-agent --agent agent:codex:desktop --runtime codex
etg identity export-agent --agent agent:codex:desktop --runtime codex \
  --out codex-desktop-public.json

etg warden --dir . unlock
etg trust --dir . enroll-agent \
  --agent agent:codex:desktop --owner user:alice \
  --runtime codex --version 2026.08.16 \
  --public-key codex-desktop-public.json \
  --authorized-by user:alice
etg broker --dir . handoff --accept-contract-change
```

Versions are intentionally exact rather than an open-ended "latest" range.
When an adapter updates, review and add the new version, re-lock the registry,
then remove the old version according to your rollout policy. At least one
version must remain allowed; revoke the agent key to disable the workload.

```bash
etg warden --dir . unlock
etg trust --dir . add-agent-version \
  --agent agent:codex:desktop --version 2026.08.17 \
  --authorized-by user:alice
etg broker --dir . handoff --accept-contract-change
```

After the rollout, remove the retired version through the same review path:

```bash
etg warden --dir . unlock
etg trust --dir . remove-agent-version \
  --agent agent:codex:desktop --version 2026.08.16 \
  --authorized-by user:alice
etg broker --dir . handoff --accept-contract-change
```

Use `rotate-agent-key` for scheduled replacement or `revoke-agent-key` when a
workload key may be exposed. Both require a current trust-admin signature and
are preserved in the public registry history.

## Actions in a shared project

When `.etg/trust.yaml` exists, Entigram accepts personal-identity signatures
instead of the local development authority. An issuer signs a scoped grant;
the approver signs separately. The agent receives the signed JSON, never a
private key.

```bash
etg action --dir . grant \
  --issuer user:alice --principal user:founder --agent agent:codex:desktop \
  --scope release.publish --expires-at 2026-12-31T23:59:00Z --out grant.json

etg action --dir . approve \
  --name publish_release --request request.json \
  --approver user:bob --role release_manager \
  --expires-at 2026-12-31T23:59:00Z --out approval.json
```

The registry verifies the signer, their active key, and their relevant role.
An approval that merely claims a role is denied if the signer has not been
assigned it in `.etg/trust.yaml`. The agent must also attest the exact request:

```bash
etg action --dir . attest \
  --name publish_release --request request.json \
  --agent agent:codex:desktop --runtime codex --version 2026.08.16 \
  --expires-at <time-no-more-than-15-minutes-after-issuance> \
  --out agent-attestation.json

etg action --dir . validate \
  --name publish_release --request request.json --grant grant.json \
  --approval approval.json --agent-attestation agent-attestation.json
```

The attestation binds the action name, request ID, full request digest, runtime
and version. It must expire within 15 minutes, and a broker consumes its ID and
nonce atomically in its local ledger when admission succeeds; a replay against
that broker is denied. A future cross-host executor must enforce the same
single-use check at its credential or target boundary. A different agent, key,
request, runtime, or unapproved version is denied before admission. The
local-development mode remains explicitly unattested and must not be represented
as identity or impersonation control.

For a contract that requires verified evidence, a signer with the configured
`evidence_issuer` role must also attest the record's ID, SHA-256 digest,
observation time, and source:

```bash
etg action --dir . attest-evidence \
  --request request.json --evidence release_tests \
  --issuer user:alice --source-kind ci --source-uri <immutable-ci-receipt> \
  --out release-tests-attestation.json

etg action --dir . validate \
  --name publish_release --request request.json --grant grant.json \
  --approval approval.json --agent-attestation agent-attestation.json \
  --evidence-attestation release-tests-attestation.json
```

The action contract can restrict evidence to issuer roles and source kinds.
In shared trust, `verified: true` is never a self-asserted request field.

To revoke a shared grant in every clone, submit a signed trust transition. The
issuer must supply the original signed grant, and its identity must match the
grant's issuer:

```bash
etg warden --dir . unlock
etg trust --dir . revoke-grant --grant-id <grant-id> --grant grant.json \
  --issuer user:alice
etg broker --dir . handoff --accept-contract-change
```

This is signed workload identity, not remote measurement of an arbitrary
running binary. The enrolled adapter host asserts its runtime and version. To
make that claim enforcement-grade, keep the signing key in a host/KMS boundary
that verifies the adapter version and does not let the model invoke arbitrary
signing requests. `etg action attest` is the portable reference flow and test
fixture for that adapter boundary; it is not a substitute for one.

## Rotation and recovery

Key rotation uses the current key, so it preserves an unbroken signer history:

```bash
# Create a replacement key for the same signer on the person's own device.
etg identity create --signer user:alice --name 2026-rotation
etg identity export --signer user:alice --name 2026-rotation --out alice-new-public.json

etg trust --dir . rotate-key \
  --signer user:alice --public-key alice-new-public.json \
  --identity-key ~/.config/entigram/identities/user_alice-default-ed25519.pem
```

For a lost or compromised key, the person first creates a replacement public
identity. Recovery administrators then independently approve the exact
replacement request; the configured recovery quorum is enforced when applying
it.

```bash
etg trust --dir . recovery-request \
  --signer user:alice --public-key alice-replacement-public.json \
  --out alice-recovery.json

# Each recovery administrator runs this with their own local key.
etg trust --dir . approve-change \
  --change alice-recovery.json --signer user:bob --out bob-approval.json

etg trust --dir . apply-change \
  --change alice-recovery.json --approval bob-approval.json
```

Recovery marks the old active key revoked and records the signed approvals.
Historic signatures remain inspectable, but cannot authorize new work. If all
recovery keys are lost, cryptographic continuity cannot be restored; treat that
as an external incident and create a documented new project trust root.

## Provenance

```bash
etg history --dir .
etg history --dir . --kind action --json
etg provenance --dir . --event action:action-decision-... --json
```

`history` presents a compact timeline across delivery snapshots, action
decisions, signed trust transitions, resolutions, improvement proposals, and
pending conflicts. `provenance` expands one event with its model fingerprint,
request target, evidence and approval bindings, and the recorded decision.
Signed audit bundles also include action decisions and trust-registry events.
