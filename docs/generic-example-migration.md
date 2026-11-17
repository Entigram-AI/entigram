# Migration to generic product examples

Entigram's maintained reference examples now use supplier invoices, purchase orders, customers, and orders. The core remains a general schema and governance engine: operators can define their own domains, subject to their own permissions and controls.

## Changes in the next release

| Previous artifact | Change | Maintained example |
| --- | --- | --- |
| `entigram.extraction_pipeline.run_extraction_pipeline` | Removed from the Python distribution; no compatibility alias | [Invoice reconciliation](../examples/invoice_reconciliation/README.md), run from a source checkout |
| `sample_ehr.csv` | Removed from the source tree | Synthetic invoice CSV beside the new example |
| FHIR/clinical test fixtures | Replaced while retaining compiler, GraphQL, negotiation, and conflict-sensing coverage | Customer/order and invoice fixtures |
| Built-in clinical synonym classes and blocking category | Removed from automatic alignment proposals | Generic identity, supplier, transaction, product, and location defaults |
| Legacy healthcare package names in Sentinel's fallback list | No longer recognized by the legacy list | Namespaced package classification remains available |

The removed extraction script wrote raw SQL to assumed package tables and printed a de-identification claim without implementing it. The replacement demonstrates only alignment and conflict sensing; it makes no de-identification, regulatory compliance, or domain-certification claim.

Automatic matching proposals may change after upgrading. Review new proposals before verifying alignments. Existing ledger alignments and user-defined schemas are not rewritten or deleted by these source changes; follow normal workspace review and migration procedures if you choose to change them.

## Standard-package retirement

The companion standard-package change removes `@entigram/ehr-extraction`, `@entigram/clinical-validation`, and `@entigram/hipaa-compliance` from the current catalog, package tree, and root model. A companion registry change returns HTTP `410 Gone` for direct download requests for those names, including legacy aliases. The invoice example is a governance demonstration, not a replacement for those packages' domain functionality.

Existing installations, caches, previously downloaded archives, and historical releases are not remotely deleted. Inventory active workspaces and dependencies, retain any records required by your own retention policy, and review removal of package entities, relationships, and state before changing a workspace. Git history remains available for provenance. Do not assume an old archive is maintained because a local copy still installs.

Merging the core PR to `main` triggers the configured [release workflow](../.github/workflows/release.yml). When the release plan calls for a new version, that workflow creates a GitHub release and publishes Python/npm packages and a Homebrew update after validation. Existing published versions remain immutable.

Merge the registry guard before the standard-package retirement. Clients tracking the standard-package repository's default branch can see catalog and artifact changes as soon as that branch is updated; no separate catalog release is required for those clients. The hosted Worker is managed through Terraform Cloud: verify a successful apply and the live `410` response before announcing hosted retirement. A green test or Terraform plan alone does not establish that deployment completed.
