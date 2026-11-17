"""Compare synthetic invoice totals using an isolated, in-memory broker ledger."""

import argparse
import csv
import json
from decimal import Decimal, InvalidOperation
from pathlib import Path
from tempfile import TemporaryDirectory

from entigram.broker import EntigramBroker
from entigram.sqlite_ledger.manager import LedgerManager

FIXTURES = Path(__file__).resolve().parent


def _amount(value):
    try:
        amount = Decimal(value)
    except (InvalidOperation, TypeError) as exc:
        raise ValueError("Invoice totals must be finite decimal amounts") from exc
    try:
        valid = amount.is_finite() and amount >= 0 and amount == amount.quantize(Decimal("0.01"))
    except InvalidOperation:
        valid = False
    if not valid:
        raise ValueError("Invoice totals must be nonnegative amounts with at most two decimal places")
    # Decimal preserves a sign on zero; sensing compares canonical strings.
    if amount.is_zero():
        amount = amount.copy_abs()
    return format(amount, ".2f")


def reconcile(csv_path=FIXTURES / "invoices.csv"):
    """Return conflicts without modifying the caller's workspace or approving writes."""
    results = []
    with TemporaryDirectory(prefix="entigram-invoice-demo-") as directory:
        Path(directory, "schema.lds").write_text((FIXTURES / "schema.lds").read_text())
        ledger = LedgerManager(":memory:")
        try:
            broker = EntigramBroker(directory, ledger=ledger, seed_synonyms=False)
            # Fixture evidence authorizes only this demonstration's sensing path.
            # Production alignments require the workspace's actual review process.
            if not ledger.record_alignment(
                source_domain="SupplierInvoice", target_domain="PurchaseOrder",
                source_concept="invoice_total", target_concept="invoice_total",
                confidence=1.0, rationale="Synthetic fixture: USD invoice totals.",
                lifecycle_status="verified", evidence_type="sample_data_match",
                verified=True, verified_by="example:invoice-reconciliation",
            ):
                raise RuntimeError("Could not establish the demonstration alignment")
            with Path(csv_path).open(newline="", encoding="utf-8") as source:
                reader = csv.DictReader(source)
                required = {"invoice_id", "supplier_id", "supplier_total", "purchase_order_total"}
                columns = reader.fieldnames or []
                if len(columns) != len(set(columns)):
                    raise ValueError("CSV contains duplicate column names")
                if not required.issubset(columns):
                    raise ValueError("CSV is missing required invoice columns")
                for row in reader:
                    if None in row or any(value is None for value in row.values()):
                        raise ValueError("CSV row does not match the header columns")
                    if not row["invoice_id"] or not row["supplier_id"]:
                        raise ValueError("Invoice and supplier identifiers are required")
                    supplier = {"invoice_total": _amount(row["supplier_total"])}
                    order = {"invoice_total": _amount(row["purchase_order_total"])}
                    conflicts = broker.detect_cross_domain_conflict(
                        "SupplierInvoice", "PurchaseOrder", supplier, order,
                    )
                    results.append({
                        "invoice_id": row["invoice_id"], "supplier_id": row["supplier_id"],
                        "status": "conflict" if conflicts else "matched",
                        "supplier_total": supplier["invoice_total"],
                        "purchase_order_total": order["invoice_total"],
                        "conflict_count": len(conflicts),
                    })
        finally:
            ledger.close()
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv", nargs="?", type=Path, default=FIXTURES / "invoices.csv")
    print(json.dumps(reconcile(parser.parse_args().csv), indent=2))
