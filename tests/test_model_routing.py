"""Stdlib-only coverage for the opt-in Model Routing MVP."""
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock, patch

from entigram.model_routing import (
    TIER_LOCAL, TIER_LOW_COST, TIER_PREMIUM, WRITE_FULL_WRITE,
    WRITE_PROPOSAL_ONLY, discover_providers, evaluate_routing_suite,
    explain_routing, load_routing_policy,
)


class TestModelRouting(unittest.TestCase):
    def test_litert_is_optional_and_discovered_without_download(self):
        with patch("shutil.which", side_effect=lambda name: "/usr/local/bin/lit" if name == "lit" else None), patch("subprocess.run") as run:
            result = MagicMock(returncode=0, stdout="gemma-2b-it\n")
            run.return_value = result
            provider = discover_providers()["litert_lm"]
        self.assertTrue(provider["detected"])
        self.assertEqual(provider["models"], ["gemma-2b-it"])

    def test_default_and_custom_policy_tiers(self):
        self.assertEqual(explain_routing("summarize build log").tier, TIER_LOCAL)
        with tempfile.TemporaryDirectory() as directory:
            etg = Path(directory) / ".etg"; etg.mkdir()
            (etg / "routing.yaml").write_text("default_tier: low_cost\n", encoding="utf-8")
            route = explain_routing("summarize build log", workspace_dir=Path(directory))
        self.assertEqual((route.tier, route.write_authority), (TIER_LOW_COST, WRITE_PROPOSAL_ONLY))

    def test_writes_and_governed_work_escalate(self):
        for task in ("Edit a source file", "change tests", "mutate schema.lds", "audit security policy"):
            route = explain_routing(task)
            self.assertEqual((route.tier, route.write_authority), (TIER_PREMIUM, WRITE_FULL_WRITE))

    def test_evaluation_suite_passes(self):
        result = evaluate_routing_suite()
        self.assertEqual((result["failed_tasks"], result["accuracy_percent"]), (0, 100.0))

    def _cli_json(self, argv):
        from entigram.cli_runner.etg_cli import _main
        output = io.StringIO()
        with patch.dict(os.environ, {"ENTIGRAM_ENABLE_MODEL_ROUTING": "1"}), patch("sys.argv", argv), redirect_stdout(output):
            _main()
        return json.loads(output.getvalue())

    def test_opt_in_cli_commands(self):
        explain = self._cli_json(["etg", "route", "explain", "--task", "Explain syntax", "--json"])
        providers = self._cli_json(["etg", "route", "providers", "--json"])
        evaluation = self._cli_json(["etg", "route", "eval", "--json"])
        self.assertEqual(explain["write_authority"], WRITE_PROPOSAL_ONLY)
        self.assertIn("litert_lm", providers)
        self.assertEqual(evaluation["failed_tasks"], 0)
