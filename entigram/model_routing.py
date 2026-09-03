"""
Provider-neutral Model Routing module for Entigram.

Defines deterministic task routing, tier classification (local, low_cost, premium),
write authority governance, provider discovery, and policy evaluation.
Uses Python standard library only.
"""

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from typing import Any, Dict, List, Optional, Tuple


# Task Tiers
TIER_LOCAL = "local"
TIER_LOW_COST = "low_cost"
TIER_PREMIUM = "premium"

VALID_TIERS = {TIER_LOCAL, TIER_LOW_COST, TIER_PREMIUM}

# Write Authority
WRITE_PROPOSAL_ONLY = "proposal_only"
WRITE_FULL_WRITE = "full_write"

# Escalation Trigger Categories
TRIGGER_SCHEMA_ONTOLOGY = "schema/ontology"
TRIGGER_SECURITY = "security"
TRIGGER_CROSS_PACKAGE = "cross_package"
TRIGGER_DESTRUCTIVE = "destructive"
TRIGGER_WRITE = "write"

# Known Escalation Keywords (lowercased)
ESCALATION_KEYWORDS = {
    TRIGGER_SCHEMA_ONTOLOGY: {
        "schema", "ontology", "lds", "ttl", "draft_schema", "schema.lds",
        "draft_schema.ttl", "schema_compiler", "mutate_schema", "update_ontology"
    },
    TRIGGER_SECURITY: {
        "security", "trust", "warden", "sentinel", "action_admission",
        "credential", "key", "audit_security"
    },
    TRIGGER_CROSS_PACKAGE: {
        "cross_package", "cross-package", "multi_package", "refactor_broker",
        "package_builder"
    },
    TRIGGER_DESTRUCTIVE: {
        "destructive", "delete", "drop", "reset", "wipe", "eject", "purge"
    },
    TRIGGER_WRITE: {
        "write", "mutate", "modify", "update", "apply", "commit", "save", "create_file",
        "edit", "change", "replace", "remove", "add", "insert", "rename", "create"
    },
}

# Task Types that explicitly map to Escalation Categories
EXPLICIT_ESCALATION_TASK_TYPES = {
    "schema": TRIGGER_SCHEMA_ONTOLOGY,
    "ontology": TRIGGER_SCHEMA_ONTOLOGY,
    "security": TRIGGER_SECURITY,
    "cross_package": TRIGGER_CROSS_PACKAGE,
    "cross-package": TRIGGER_CROSS_PACKAGE,
    "multi_package": TRIGGER_CROSS_PACKAGE,
    "destructive": TRIGGER_DESTRUCTIVE,
    "write": TRIGGER_WRITE,
    "mutate": TRIGGER_WRITE,
}


@dataclass
class RouteExplanation:
    task: str
    task_type: Optional[str]
    tier: str
    provider: str
    write_authority: str
    rationale: str
    escalation_triggers: List[str] = field(default_factory=list)
    policy_source: str = "default"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task": self.task,
            "task_type": self.task_type,
            "tier": self.tier,
            "provider": self.provider,
            "write_authority": self.write_authority,
            "rationale": self.rationale,
            "escalation_triggers": self.escalation_triggers,
            "policy_source": self.policy_source,
        }


def _parse_simple_yaml(text: str) -> Dict[str, Any]:
    """
    Simple stdlib-only YAML parser for basic key-value mappings, dicts, lists, and scalars.
    If PyYAML is available, attempts PyYAML first.
    """
    try:
        import yaml
        parsed = yaml.safe_load(text)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass

    result: Dict[str, Any] = {}
    lines = text.splitlines()
    current_key = None

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        if ":" in stripped:
            key, val = stripped.split(":", 1)
            key = key.strip()
            val = val.strip()
            if not val:
                current_key = key
                result[current_key] = {}
            else:
                if val.lower() == "true":
                    val_obj: Any = True
                elif val.lower() == "false":
                    val_obj = False
                elif val.isdigit():
                    val_obj = int(val)
                else:
                    val_obj = val.strip('"\'')
                result[key] = val_obj
                current_key = None
        elif stripped.startswith("- ") and current_key and isinstance(result.get(current_key), list):
            item_val = stripped[2:].strip().strip('"\'')
            result[current_key].append(item_val)

    return result


def load_routing_policy(workspace_dir: Optional[Path] = None) -> Tuple[Dict[str, Any], str]:
    """
    Load optional .etg/routing.yaml from workspace directory, or return default safe policy.
    Returns (policy_dict, policy_source).
    """
    if workspace_dir:
        policy_path = Path(workspace_dir) / ".etg" / "routing.yaml"
        if policy_path.exists() and policy_path.is_file():
            try:
                content = policy_path.read_text(encoding="utf-8")
                policy_dict = _parse_simple_yaml(content)
                if policy_dict and isinstance(policy_dict, dict):
                    return policy_dict, ".etg/routing.yaml"
            except Exception:
                pass

    # Default policy
    default_policy = {
        "version": 1,
        "default_tier": TIER_LOCAL,
        "providers": {
            TIER_LOCAL: "ollama",
            TIER_LOW_COST: "litert_lm",
            TIER_PREMIUM: "codex",
        },
        "write_authority": {
            TIER_LOCAL: WRITE_PROPOSAL_ONLY,
            TIER_LOW_COST: WRITE_PROPOSAL_ONLY,
            TIER_PREMIUM: WRITE_FULL_WRITE,
        },
    }
    return default_policy, "default"


def discover_providers(workspace_dir: Optional[Path] = None) -> Dict[str, Dict[str, Any]]:
    """
    Discover available providers without downloading/running models or making network calls.
    Returns details for Ollama, LiteRT-LM, Codex, and Antigravity.
    """
    providers: Dict[str, Dict[str, Any]] = {}

    # 1. Ollama
    ollama_bin = shutil.which("ollama")
    if ollama_bin:
        models = []
        try:
            res = subprocess.run(
                [ollama_bin, "list"],
                capture_output=True,
                text=True,
                timeout=2,
            )
            if res.returncode == 0:
                lines = res.stdout.splitlines()
                for line in lines[1:]:  # skip header row "NAME ID SIZE MODIFIED"
                    parts = line.split()
                    if parts and parts[0] != "NAME":
                        models.append(parts[0])
        except Exception:
            models = []

        providers["ollama"] = {
            "detected": True,
            "executable": ollama_bin,
            "models": models,
            "notes": "Ollama CLI detected. Safe local list command executed without downloading weights.",
        }
    else:
        providers["ollama"] = {
            "detected": False,
            "executable": None,
            "models": [],
            "notes": "Ollama CLI binary not found in PATH.",
        }

    # 2. LiteRT-LM (lit)
    lit_bin = shutil.which("lit")
    if lit_bin:
        models = []
        notes = "LiteRT 'lit' CLI binary detected. Models listed only if a safe local list command exists."
        try:
            res = subprocess.run(
                [lit_bin, "list"],
                capture_output=True,
                text=True,
                timeout=2,
            )
            if res.returncode == 0 and res.stdout:
                for line in res.stdout.splitlines():
                    stripped = line.strip()
                    if stripped and not stripped.lower().startswith("usage"):
                        models.append(stripped)
        except Exception:
            models = []

        providers["litert_lm"] = {
            "detected": True,
            "executable": lit_bin,
            "models": models,
            "notes": notes,
        }
    else:
        providers["litert_lm"] = {
            "detected": False,
            "executable": None,
            "models": [],
            "notes": "LiteRT 'lit' CLI binary not found in PATH. LiteRT is optional.",
        }

    # 3. Codex
    codex_bin = shutil.which("codex")
    codex_env = any(k in os.environ for k in ["CODEX_API_KEY", "CODEX_HOME", "CODEX_MODEL"])
    if codex_bin or codex_env:
        providers["codex"] = {
            "detected": True,
            "executable": codex_bin,
            "models": ["codex-standard"],
            "notes": "Codex provider detected via CLI binary or environment configuration.",
        }
    else:
        providers["codex"] = {
            "detected": False,
            "executable": None,
            "models": [],
            "notes": "Codex binary or environment configuration not detected.",
        }

    # 4. Antigravity
    agy_bin = shutil.which("antigravity") or shutil.which("agy")
    agy_env = any(k in os.environ for k in ["ANTIGRAVITY_AGENT", "AGY_VERSION", "AGY_HOME"])
    if agy_bin or agy_env:
        providers["antigravity"] = {
            "detected": True,
            "executable": agy_bin,
            "models": ["antigravity-default"],
            "notes": "Antigravity provider detected via CLI binary or environment configuration.",
        }
    else:
        providers["antigravity"] = {
            "detected": False,
            "executable": None,
            "models": [],
            "notes": "Antigravity binary or environment configuration not detected.",
        }

    return providers


def detect_escalation_triggers(task: str, task_type: Optional[str] = None) -> List[str]:
    """
    Detect escalation triggers that force premium tier routing:
    schema/ontology, security, cross_package, destructive, or write tasks.
    """
    triggers = []
    task_lower = task.lower()
    task_type_lower = (task_type or "").lower().strip()

    # 1. Explicit task type matches
    if task_type_lower in EXPLICIT_ESCALATION_TASK_TYPES:
        trigger_cat = EXPLICIT_ESCALATION_TASK_TYPES[task_type_lower]
        if trigger_cat not in triggers:
            triggers.append(trigger_cat)

    # 2. Check task text for trigger keywords and actions
    for category, keywords in ESCALATION_KEYWORDS.items():
        if category in triggers:
            continue
        for kw in keywords:
            if re.search(r'\b' + re.escape(kw) + r'\b', task_lower):
                if category == TRIGGER_SCHEMA_ONTOLOGY:
                    is_read_only_text = any(
                        r_kw in task_lower
                        for r_kw in ["explain", "read", "view", "inspect", "draft proposal", "describe", "summarize"]
                    )
                    has_mutation_verb = any(
                        m_kw in task_lower
                        for m_kw in ["mutate", "update", "modify", "edit", "change", "write", "compile", "create", "delete"]
                    )
                    if is_read_only_text and not has_mutation_verb and task_type_lower not in ["schema", "ontology"]:
                        continue

                if category not in triggers:
                    triggers.append(category)
                    break

    return triggers


def explain_routing(
    task: str,
    task_type: Optional[str] = None,
    workspace_dir: Optional[Path] = None,
) -> RouteExplanation:
    """
    Deterministically explain routing selection for a given task.
    """
    policy, policy_source = load_routing_policy(workspace_dir)

    triggers = detect_escalation_triggers(task, task_type)
    task_type_clean = (task_type or "").lower().strip()

    if triggers:
        tier = TIER_PREMIUM
        write_authority = WRITE_FULL_WRITE
        rationale = (
            f"Policy forced premium tier for sensitive operations. "
            f"Detected escalation triggers: {', '.join(triggers)}."
        )
    elif task_type_clean == "low_cost" or "low_cost" in task.lower() or "format docstring" in task.lower():
        tier = TIER_LOW_COST
        write_authority = WRITE_PROPOSAL_ONLY
        rationale = "Routed to low_cost tier for routine, non-sensitive operational task."
    else:
        tier = policy.get("default_tier", TIER_LOCAL)
        if tier not in VALID_TIERS:
            tier = TIER_LOCAL
        configured_authority = policy.get("write_authority", {}).get(tier)
        write_authority = (
            configured_authority
            if tier == TIER_PREMIUM and configured_authority == WRITE_FULL_WRITE
            else WRITE_PROPOSAL_ONLY
        )
        rationale = f"Routed to policy default tier ({tier}) for a non-sensitive task."

    providers_map = policy.get("providers", {})
    preferred_provider = providers_map.get(tier, "ollama" if tier == TIER_LOCAL else "codex")

    return RouteExplanation(
        task=task,
        task_type=task_type,
        tier=tier,
        provider=preferred_provider,
        write_authority=write_authority,
        rationale=rationale,
        escalation_triggers=triggers,
        policy_source=policy_source,
    )


# Fixed built-in evaluation suite of Entigram-relevant tasks
EVAL_SUITE: List[Dict[str, Any]] = [
    {
        "task": "Explain LDS schema entity syntax",
        "task_type": "proposal",
        "expected_tier": TIER_LOCAL,
        "expected_write_authority": WRITE_PROPOSAL_ONLY,
    },
    {
        "task": "Draft local proposal for helper function",
        "task_type": "read",
        "expected_tier": TIER_LOCAL,
        "expected_write_authority": WRITE_PROPOSAL_ONLY,
    },
    {
        "task": "Summarize workspace changelog and docs",
        "task_type": "read",
        "expected_tier": TIER_LOCAL,
        "expected_write_authority": WRITE_PROPOSAL_ONLY,
    },
    {
        "task": "Format python docstrings in utility module",
        "task_type": "low_cost",
        "expected_tier": TIER_LOW_COST,
        "expected_write_authority": WRITE_PROPOSAL_ONLY,
    },
    {
        "task": "Mutate schema.lds to add new Entity",
        "task_type": "schema",
        "expected_tier": TIER_PREMIUM,
        "expected_write_authority": WRITE_FULL_WRITE,
    },
    {
        "task": "Update ontology draft_schema.ttl attributes",
        "task_type": "ontology",
        "expected_tier": TIER_PREMIUM,
        "expected_write_authority": WRITE_FULL_WRITE,
    },
    {
        "task": "Audit action_admission trust and warden policy",
        "task_type": "security",
        "expected_tier": TIER_PREMIUM,
        "expected_write_authority": WRITE_FULL_WRITE,
    },
    {
        "task": "Refactor broker sync across multiple packages",
        "task_type": "cross_package",
        "expected_tier": TIER_PREMIUM,
        "expected_write_authority": WRITE_FULL_WRITE,
    },
    {
        "task": "Destructive reset of ledger database state",
        "task_type": "destructive",
        "expected_tier": TIER_PREMIUM,
        "expected_write_authority": WRITE_FULL_WRITE,
    },
    {
        "task": "Write new action handler to workspace",
        "task_type": "write",
        "expected_tier": TIER_PREMIUM,
        "expected_write_authority": WRITE_FULL_WRITE,
    },
]


def evaluate_routing_suite(workspace_dir: Optional[Path] = None) -> Dict[str, Any]:
    """
    Evaluate fixed built-in suite of Entigram-relevant tasks against model routing policy.
    Does not invoke agents or models.
    """
    results = []
    passed_count = 0

    for item in EVAL_SUITE:
        task = item["task"]
        task_type = item.get("task_type")
        expected_tier = item["expected_tier"]
        expected_write_auth = item["expected_write_authority"]

        explanation = explain_routing(task, task_type=task_type, workspace_dir=workspace_dir)

        tier_matches = (explanation.tier == expected_tier)
        auth_matches = (explanation.write_authority == expected_write_auth)
        passed = tier_matches and auth_matches

        if passed:
            passed_count += 1

        results.append({
            "task": task,
            "task_type": task_type,
            "expected_tier": expected_tier,
            "routed_tier": explanation.tier,
            "expected_write_authority": expected_write_auth,
            "routed_write_authority": explanation.write_authority,
            "escalation_triggers": explanation.escalation_triggers,
            "passed": passed,
        })

    total = len(EVAL_SUITE)
    accuracy = (passed_count / total * 100.0) if total > 0 else 0.0

    return {
        "total_tasks": total,
        "passed_tasks": passed_count,
        "failed_tasks": total - passed_count,
        "accuracy_percent": round(accuracy, 2),
        "results": results,
    }
