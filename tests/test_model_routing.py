"""
Tests for Entigram Model Routing MVP.

Covers:
- LiteRT absent/present discovery (with binary/env mocking)
- Policy loader defaults vs custom .etg/routing.yaml
- Unsafe escalation forcing premium tier and full_write authority
- Local proposal-only classification
- CLI JSON output for explain, providers, and eval subcommands
"""

import json
import os
from pathlib import Path
import shutil
import subprocess
from unittest.mock import MagicMock, patch
import pytest

from entigram.model_routing import (
    EVAL_SUITE,
    TIER_LOCAL,
    TIER_LOW_COST,
    TIER_PREMIUM,
    WRITE_FULL_WRITE,
    WRITE_PROPOSAL_ONLY,
    discover_providers,
    evaluate_routing_suite,
    explain_routing,
    load_routing_policy,
)


def test_litert_absent_discovery():
    """Verify LiteRT-LM is reported as not detected when 'lit' executable is absent."""
    def mock_which(cmd):
        if cmd == "lit":
            return None
        return f"/usr/bin/{cmd}"

    with patch("shutil.which", side_effect=mock_which):
        providers = discover_providers()
        litert = providers["litert_lm"]
        assert litert["detected"] is False
        assert litert["executable"] is None
        assert litert["models"] == []
        assert "optional" in litert["notes"].lower()


def test_litert_present_discovery():
    """Verify LiteRT-LM discovery when 'lit' binary is present."""
    def mock_which(cmd):
        if cmd == "lit":
            return "/usr/local/bin/lit"
        return None

    def mock_run(cmd, capture_output=True, text=True, timeout=2):
        res = MagicMock()
        res.returncode = 0
        res.stdout = "gemma-2b-it\nphi-2-lit"
        return res

    with patch("shutil.which", side_effect=mock_which), patch("subprocess.run", side_effect=mock_run):
        providers = discover_providers()
        litert = providers["litert_lm"]
        assert litert["detected"] is True
        assert litert["executable"] == "/usr/local/bin/lit"
        assert litert["models"] == ["gemma-2b-it", "phi-2-lit"]


def test_ollama_discovery_mocked():
    """Verify Ollama discovery with mocked safe list command."""
    def mock_which(cmd):
        if cmd == "ollama":
            return "/usr/local/bin/ollama"
        return None

    def mock_run(cmd, capture_output=True, text=True, timeout=2):
        res = MagicMock()
        res.returncode = 0
        res.stdout = "NAME              ID           SIZE     MODIFIED\nllama3:latest     a1b2c3d4e5   4.7 GB   2 days ago\nphi3:latest       f6g7h8i9j0   2.2 GB   5 days ago\n"
        return res

    with patch("shutil.which", side_effect=mock_which), patch("subprocess.run", side_effect=mock_run):
        providers = discover_providers()
        ollama = providers["ollama"]
        assert ollama["detected"] is True
        assert ollama["executable"] == "/usr/local/bin/ollama"
        assert ollama["models"] == ["llama3:latest", "phi3:latest"]


def test_policy_defaults(tmp_path):
    """Verify safe default policy when .etg/routing.yaml is absent."""
    policy, source = load_routing_policy(tmp_path)
    assert source == "default"
    assert policy["default_tier"] == TIER_LOCAL
    assert policy["providers"][TIER_LOCAL] == "ollama"
    assert policy["write_authority"][TIER_PREMIUM] == WRITE_FULL_WRITE


def test_custom_policy_loader(tmp_path):
    """Verify loading custom policy from .etg/routing.yaml."""
    etg_dir = tmp_path / ".etg"
    etg_dir.mkdir(parents=True, exist_ok=True)
    routing_yaml = etg_dir / "routing.yaml"
    routing_yaml.write_text(
        "default_tier: low_cost\n"
        "providers:\n"
        "  local: ollama\n"
        "  low_cost: litert_lm\n"
        "  premium: codex\n",
        encoding="utf-8",
    )

    policy, source = load_routing_policy(tmp_path)
    assert source == ".etg/routing.yaml"
    assert policy.get("default_tier") == "low_cost"


def test_unsafe_escalation_schema():
    """Verify policy forces premium tier for schema tasks."""
    explanation = explain_routing("Mutate schema.lds to add new entity", task_type="schema")
    assert explanation.tier == TIER_PREMIUM
    assert explanation.write_authority == WRITE_FULL_WRITE
    assert "schema/ontology" in explanation.escalation_triggers


def test_unsafe_escalation_ontology():
    """Verify policy forces premium tier for ontology tasks."""
    explanation = explain_routing("Update draft_schema.ttl attributes", task_type="ontology")
    assert explanation.tier == TIER_PREMIUM
    assert explanation.write_authority == WRITE_FULL_WRITE
    assert "schema/ontology" in explanation.escalation_triggers


def test_unsafe_escalation_security():
    """Verify policy forces premium tier for security tasks."""
    explanation = explain_routing("Audit action_admission trust and warden policy", task_type="security")
    assert explanation.tier == TIER_PREMIUM
    assert explanation.write_authority == WRITE_FULL_WRITE
    assert "security" in explanation.escalation_triggers


def test_unsafe_escalation_cross_package():
    """Verify policy forces premium tier for cross-package tasks."""
    explanation = explain_routing("Refactor broker sync across multiple packages", task_type="cross_package")
    assert explanation.tier == TIER_PREMIUM
    assert explanation.write_authority == WRITE_FULL_WRITE
    assert "cross_package" in explanation.escalation_triggers


def test_unsafe_escalation_destructive():
    """Verify policy forces premium tier for destructive tasks."""
    explanation = explain_routing("Destructive reset of ledger state", task_type="destructive")
    assert explanation.tier == TIER_PREMIUM
    assert explanation.write_authority == WRITE_FULL_WRITE
    assert "destructive" in explanation.escalation_triggers


def test_unsafe_escalation_write():
    """Verify policy forces premium tier for write tasks."""
    explanation = explain_routing("Write new action handler to workspace", task_type="write")
    assert explanation.tier == TIER_PREMIUM
    assert explanation.write_authority == WRITE_FULL_WRITE
    assert "write" in explanation.escalation_triggers


def test_local_proposal_only_classification():
    """Verify local read/explain proposal tasks receive proposal_only write authority and local tier."""
    explanation = explain_routing("Explain LDS schema entity syntax", task_type="proposal")
    assert explanation.tier == TIER_LOCAL
    assert explanation.write_authority == WRITE_PROPOSAL_ONLY
    assert explanation.escalation_triggers == []

    explanation2 = explain_routing("Summarize workspace docs", task_type="read")
    assert explanation2.tier == TIER_LOCAL
    assert explanation2.write_authority == WRITE_PROPOSAL_ONLY


def test_evaluate_routing_suite():
    """Verify built-in evaluation suite reports 100% accuracy without model invocations."""
    eval_result = evaluate_routing_suite()
    assert eval_result["total_tasks"] == len(EVAL_SUITE)
    assert eval_result["passed_tasks"] == len(EVAL_SUITE)
    assert eval_result["accuracy_percent"] == 100.0


def test_cli_explain_json_output(capsys):
    """Verify 'etg route explain --task <text> --json' CLI output."""
    from entigram.cli_runner.etg_cli import main

    with patch("sys.argv", ["etg", "route", "explain", "--task", "Explain syntax", "--task-type", "proposal", "--json"]):
        main()

    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert data["task"] == "Explain syntax"
    assert data["tier"] == TIER_LOCAL
    assert data["write_authority"] == WRITE_PROPOSAL_ONLY


def test_cli_providers_json_output(capsys):
    """Verify 'etg route providers --json' CLI output."""
    from entigram.cli_runner.etg_cli import main

    with patch("sys.argv", ["etg", "route", "providers", "--json"]):
        main()

    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert "ollama" in data
    assert "litert_lm" in data
    assert "codex" in data
    assert "antigravity" in data


def test_cli_eval_json_output(capsys):
    """Verify 'etg route eval --json' CLI output."""
    from entigram.cli_runner.etg_cli import main

    with patch("sys.argv", ["etg", "route", "eval", "--json"]):
        main()

    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert data["total_tasks"] == 10
    assert data["accuracy_percent"] == 100.0
    assert len(data["results"]) == 10
