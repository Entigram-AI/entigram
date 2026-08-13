import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from entigram.agent_hooks import install_agent_hooks, remove_agent_hooks
from entigram.broker import EntigramBroker
from entigram.cli_runner.etg_cli import get_hydration_vector
from entigram.injector import inject_entigram_manifest
from entigram.sqlite_ledger.manager import LedgerManager
from entigram.sqlite_ledger.paths import resolve_ledger_path
from entigram.workspace_lifecycle import (
    active_agent_adapter_status,
    clear_active_agent_adapter_exception,
    record_active_agent_adapter_exception,
)


class TestWorkspaceAgentEnforcement(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.assertTrue(
            inject_entigram_manifest(str(self.root), ["Entigram Schemas"], "Codex")
        )
        (self.root / "schema.lds").write_text(
            "ENTITY: GovernedWork {\n  id UUID PK\n}\n"
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def _declare_agent(self, runtime):
        manifest_path = self.root / ".etg" / "entigram.yaml"
        manifest = yaml.safe_load(manifest_path.read_text())
        governance = manifest.setdefault("agent_governance", {})
        agents = governance.setdefault("active_agents", [])
        if runtime not in agents:
            agents.append(runtime)
        governance.pop("active_agent", None)
        manifest_path.write_text(yaml.safe_dump(manifest, sort_keys=False))

    def _remove_codex_adapter(self):
        remove_agent_hooks(self.root)
        status = active_agent_adapter_status(self.root, agent="codex")
        self.assertEqual("codex", status["operating_agent"])
        self.assertEqual("degraded", status["status"])
        self.assertFalse(status["ok"])
        return status

    def test_new_workspace_declares_and_enforces_selected_agent(self):
        manifest = yaml.safe_load((self.root / ".etg" / "entigram.yaml").read_text())
        self.assertEqual(["codex"], manifest["agent_governance"]["active_agents"])

        status = active_agent_adapter_status(self.root, agent="codex")
        self.assertTrue(status["ok"])
        self.assertEqual("enforced", status["status"])
        self.assertTrue(status["agents"]["codex"]["installed"])

    def test_each_declared_agent_is_checked_when_it_operates(self):
        self._declare_agent("claude")

        codex = active_agent_adapter_status(self.root, agent="codex")
        claude = active_agent_adapter_status(self.root, agent="claude")
        self.assertTrue(codex["ok"])
        self.assertFalse(claude["ok"])
        self.assertEqual("degraded", claude["status"])
        self.assertEqual(["codex", "claude"], claude["active_agents"])

        install_agent_hooks(self.root, engine="claude")
        self.assertTrue(active_agent_adapter_status(self.root, agent="claude")["ok"])

    def test_undeclared_agent_cannot_operate(self):
        status = active_agent_adapter_status(self.root, agent="claude")
        self.assertFalse(status["ok"])
        self.assertEqual("undeclared_agent", status["status"])
        self.assertIn("--add-agent", status["next_action"])

    def test_environment_detection_selects_the_operating_agent(self):
        self._declare_agent("claude")
        install_agent_hooks(self.root, engine="claude")
        with patch.dict("os.environ", {"ENTIGRAM_AGENT_RUNTIME": "claude"}):
            status = active_agent_adapter_status(self.root)
        self.assertTrue(status["ok"])
        self.assertEqual("claude", status["operating_agent"])
        self.assertEqual(
            "environment:ENTIGRAM_AGENT_RUNTIME", status["operating_agent_source"]
        )

    def test_missing_operating_adapter_withholds_hydration_and_blocks_delivery(self):
        self._remove_codex_adapter()

        hydration = get_hydration_vector(self.root, full=True, agent="codex")
        self.assertIn("ACTIVE_AGENT_ADAPTER_REQUIRED", hydration)
        self.assertNotIn("ENTITY: GovernedWork", hydration)

        with EntigramBroker(str(self.root)) as broker:
            delivery = broker.commission_and_record()
            status = broker.delivery_status()

        self.assertFalse(delivery["valid"])
        self.assertEqual("degraded", delivery["adapter_enforcement"]["status"])
        self.assertFalse(status["valid"])
        self.assertEqual("degraded", status["status"])

    def test_per_agent_exception_is_manifest_visible_and_ledger_recorded(self):
        self._declare_agent("ci")
        status = record_active_agent_adapter_exception(
            self.root,
            agent="ci",
            reason="CI runner has no native hook protocol.",
            approved_by="Release Engineering",
        )
        self.assertTrue(status["ok"])
        self.assertEqual("ci", status["recorded_agent"])
        self.assertEqual("exception", status["status"])
        self.assertIsInstance(status["recorded_exception"]["evidence_id"], int)

        manifest = yaml.safe_load((self.root / ".etg" / "entigram.yaml").read_text())
        self.assertEqual(
            "CI runner has no native hook protocol.",
            manifest["agent_governance"]["adapter_exceptions"]["ci"]["reason"],
        )

        ledger = LedgerManager(str(resolve_ledger_path(str(self.root))))
        try:
            evidence = ledger.get_delivery_evidence(limit=10)
        finally:
            ledger.close()
        self.assertTrue(
            any(row["evidence_type"] == "active_agent_adapter_exception" for row in evidence)
        )
        self.assertIn(
            "ENTITY: GovernedWork",
            get_hydration_vector(self.root, full=True, agent="ci"),
        )

        self.assertTrue(clear_active_agent_adapter_exception(self.root, agent="ci"))
        self.assertFalse(active_agent_adapter_status(self.root, agent="ci")["ok"])


if __name__ == "__main__":
    unittest.main()
