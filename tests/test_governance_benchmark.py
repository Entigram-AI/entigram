import json
import tempfile
import unittest
from pathlib import Path

from entigram.governance_benchmark import (
    GovernanceBenchmarkError,
    evaluate_governance_report,
)


ROOT = Path(__file__).resolve().parents[1]


class TestGovernanceBenchmark(unittest.TestCase):
    def test_entigram_baseline_is_valid_and_conservative(self):
        result = evaluate_governance_report(
            ROOT / "benchmarks" / "entigram-governance-baseline.json"
        )
        self.assertEqual(result["summary"]["governance_score"], 86.4)
        self.assertEqual(result["summary"]["evidence_coverage"], 100.0)
        self.assertEqual(len(result["dimensions"]), 12)

    def test_high_scores_require_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            profile = {
                "id": "test",
                "version": "v1",
                "dimensions": [{"id": "contract", "weight": 100}],
            }
            report = {
                "profile": "profile.json",
                "ratings": {"contract": {"score": 4, "evidence": []}},
            }
            (root / "profile.json").write_text(json.dumps(profile))
            report_path = root / "report.json"
            report_path.write_text(json.dumps(report))
            with self.assertRaises(GovernanceBenchmarkError):
                evaluate_governance_report(report_path)


if __name__ == "__main__":
    unittest.main()
