# Invoice reconciliation example

This synthetic example compares USD supplier-invoice totals with purchase-order totals using Entigram's alignment and conflict-sensing APIs. It needs no service, credentials, or model provider.

From the repository root, with Entigram's dependencies installed:

```sh
PYTHONPATH=. python examples/invoice_reconciliation/demo.py
```

`INV-DEMO-001` reports one conflict (`120.00` versus `118.00`); `INV-DEMO-002` matches (`75.00` on both sides). The JSON output contains no generated IDs or timestamps. All identifiers and amounts are invented.

The contract is [schema.lds](schema.lds), and the input is [invoices.csv](invoices.csv). To try another synthetic CSV, pass its path as the first argument. Required columns are `invoice_id`, `supplier_id`, `supplier_total`, and `purchase_order_total`. Totals must be finite, nonnegative decimals with at most two decimal places, all in USD.

The example copies its contract to a temporary directory and uses an in-memory ledger. It seeds a verified alignment solely as fixture evidence for sensing, then closes the ledger and removes the temporary directory. It does not update the current workspace, reconcile real accounts, authorize transactions, or resolve conflicts. Production use requires reviewed alignments and evidence under the workspace's governance policy.
