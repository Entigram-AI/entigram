import csv
import json
import os
import subprocess
import sys
import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from examples.invoice_reconciliation.demo import FIXTURES, reconcile
from entigram.schema_compiler.compiler import SchemaCompiler
from entigram.schema_compiler.parser import SchemaParser


class TestInvoiceLogic(unittest.TestCase):
    def test_fixture_schema_compiles_and_executes(self):
        entities, relationships = SchemaParser((FIXTURES / "schema.lds").read_text()).parse()
        sql = SchemaCompiler(entities, relationships).compile()
        with sqlite3.connect(":memory:") as connection:
            connection.executescript(sql)
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertTrue({"supplierinvoices", "purchaseorders"}.issubset(tables))

    def test_sensing_reports_conflict_and_match_deterministically(self):
        results = reconcile()
        self.assertEqual([row["status"] for row in results], ["conflict", "matched"])
        self.assertEqual([row["conflict_count"] for row in results], [1, 0])
        self.assertEqual(results[0]["supplier_total"], "120.00")
        self.assertEqual(results[0]["purchase_order_total"], "118.00")
        self.assertEqual(results, reconcile())

    def test_normalizes_equivalent_amounts_and_rejects_invalid_input(self):
        with TemporaryDirectory() as directory:
            path = Path(directory, "input.csv")
            def write(total, other="1.00"):
                with path.open("w", newline="") as target:
                    writer = csv.writer(target)
                    writer.writerow(["invoice_id", "supplier_id", "supplier_total", "purchase_order_total"])
                    writer.writerow(["INV-TEST", "SUP-TEST", total, other])
            write("1.0")
            self.assertEqual(reconcile(path)[0]["status"], "matched")
            for total in ["", "NaN", "Infinity", "-1", "1.001", "not-a-total"]:
                with self.subTest(total=total):
                    write(total)
                    with self.assertRaises(ValueError):
                        reconcile(path)
            path.write_text("invoice_id\nINV-TEST\n")
            with self.assertRaisesRegex(ValueError, "missing required"):
                reconcile(path)

    def test_cli_runs_from_an_uninitialized_directory_without_writes(self):
        with TemporaryDirectory() as directory:
            result = subprocess.run(
                [sys.executable, str(FIXTURES / "demo.py")],
                cwd=directory, capture_output=True, text=True, check=True,
                env={**os.environ, "PYTHONPATH": str(FIXTURES.parents[1])},
            )
            self.assertEqual([row["status"] for row in json.loads(result.stdout)], ["conflict", "matched"])
            self.assertEqual(list(Path(directory).iterdir()), [])
