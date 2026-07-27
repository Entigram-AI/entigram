import sys
import argparse
import os
import json
import importlib.util
import re
import shutil
from pathlib import Path
from datetime import datetime
from entigram.schema_compiler import compile_schema_file
from entigram.package_builder import PackageBuilder
from entigram.cli_runner.runner import launch_agent
from entigram.utils import find_project_root


def get_package_version() -> str:
    """Return the Entigram package version for CLI display and hydration."""
    project_file = Path(__file__).resolve().parents[2] / "pyproject.toml"
    if project_file.exists():
        match = re.search(r'(?m)^version = "([^"]+)"$', project_file.read_text())
        if match:
            return match.group(1)

    try:
        from importlib.metadata import version
        return version("entigram-ai")
    except Exception:
        return "unknown"


def _load_yaml_module():
    try:
        import yaml as yaml_module
        return yaml_module
    except ModuleNotFoundError as exc:
        if exc.name != "yaml":
            raise
        raise RuntimeError(
            "PyYAML is required for workspace manifest commands. "
            "Install Entigram in this Python environment or run: "
            "python3 -m pip install pyyaml"
        ) from exc


def _halt_response(warden, fallback_code, fallback_message, **context):
    event = getattr(warden, "last_halt_event", None)
    if event is not None:
        return warden.halt_event_payload(ok=False)
    return {
        "ok": False,
        "halt_event": {
            "halt_code": fallback_code,
            "message": fallback_message,
            "expected_schema": {},
            "actual_payload": context,
            "suggested_fix": "Inspect the command input and retry with a schema-valid payload.",
            "details": context,
        },
    }


def _schema_payload_halt_event(halt_code, message, payload, error=None):
    details = {"error": str(error)} if error else {}
    return {
        "halt_code": halt_code,
        "message": message,
        "expected_schema": {
            "format": "Entigram LDS",
            "required_blocks": ["ENTITY"],
            "example": "ENTITY: Name\nATTRIBUTES:\n  - .id (UUID)\n  - name (String)",
        },
        "actual_payload": {"raw_payload": payload},
        "suggested_fix": (
            "Return only valid LDS ENTITY and RELATIONSHIP blocks. "
            "Do not include prose, markdown fences, or explanatory text."
        ),
        "details": details,
    }


def _model_repair_prompt(base_prompt, halt_event, retry_number, retry_limit):
    return (
        f"{base_prompt}\n\n"
        "The previous SCHEMA PAYLOAD failed Entigram Gate validation.\n"
        f"RETRY: {retry_number}/{retry_limit}\n"
        "HALT_EVENT:\n"
        f"{json.dumps(halt_event, indent=2, sort_keys=True)}\n\n"
        "Return a corrected SCHEMA PAYLOAD only:"
    )


def _parse_score_pairs(values):
    scores = {}
    for value in values or []:
        if "=" not in value:
            raise ValueError(f"Expected TASK_TYPE=SCORE, got: {value}")
        key, raw_score = value.split("=", 1)
        key = key.strip()
        if not key:
            raise ValueError(f"Missing task type in capability score: {value}")
        scores[key] = float(raw_score)
    return scores


def _split_classes(values):
    classes = []
    for value in values or []:
        for item in value.split(","):
            item = item.strip()
            if item:
                classes.append(item)
    return classes


def _parse_json_arg(value, *, default=None):
    if value is None:
        return default
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Expected valid JSON: {exc}") from exc
    return parsed


def _agent_instructions_text():
    return """Entigram agent instructions

1. Start every repository session with:
   hydrate

   Fallback when the etg console script is unavailable:
   etg hydrate
   python3 -m entigram.cli_runner.etg_cli hydrate

2. Read the local governance contract before edits:
   .etg/agent_policy.md
   .etg/entigram.yaml
   schema.lds

3. For a concise compliance check, run:
   etg agent start

4. Before source, schema, ontology, package, or release changes, run:
   etg broker preflight --file <path>
   etg broker impact --file <path>

5. Treat schema.lds as closed-world. Do not invent entities, attributes, or alignments.

6. Use Entigram MCP/CLI paths for governed writes. Do not directly mutate .etg/state.db or .etg/entigram_state.db.

7. Before handoff after changes, run:
   etg broker handoff
   etg broker status

   If this repository provides Make, make handoff may wrap the same CLI sequence.

8. Do not hand off unless broker status reports:
   Delivery status: current
"""


def _workspace_relative_path(target_dir, changed_file):
    target_path = Path(target_dir).expanduser().resolve()
    candidate = Path(changed_file).expanduser()
    if not candidate.is_absolute():
        candidate = target_path / candidate
    try:
        return candidate.resolve().relative_to(target_path).as_posix()
    except (OSError, ValueError):
        return str(changed_file).replace("\\", "/").lstrip("./")


def _build_agent_preflight(target_dir, changed_file, impact):
    rel_path = _workspace_relative_path(target_dir, changed_file)
    governed_files = {
        "schema.lds",
        "schema.ttl",
        ".etg/agent_policy.md",
        ".etg/entigram.yaml",
    }
    ledger_files = {
        ".etg/state.db",
        ".etg/entigram_state.db",
    }
    implementation_prefixes = (
        "entigram/",
        "tests/",
        "scripts/",
        "packages/",
    )
    implementation_files = {
        "Makefile",
        "pyproject.toml",
        "setup.py",
        "requirements.txt",
    }

    warnings = []
    next_steps = [
        "Run hydrate if this session has not already been hydrated.",
        "Run broker impact --file before editing risky files.",
        "Run etg broker handoff and etg broker status before handoff after changes.",
    ]

    requires_warden_lock = False
    if rel_path in ledger_files:
        risk = "critical"
        warnings.append("Direct ledger edits are outside the governed write path.")
        next_steps.insert(0, "Use broker, warden, or package CLI APIs instead of writing the ledger file.")
    elif rel_path in governed_files or rel_path.endswith(".lds") or rel_path.endswith(".ttl"):
        risk = "high"
        requires_warden_lock = rel_path in {"schema.lds", "schema.ttl"}
        warnings.append("This file participates in the closed-world governance contract.")
        if requires_warden_lock:
            next_steps.insert(0, "After governed schema or ontology changes, run etg broker handoff.")
    elif rel_path.startswith(".etg/"):
        risk = "high"
        warnings.append("This .etg file may affect workspace governance state.")
    elif rel_path in implementation_files or rel_path.startswith(implementation_prefixes):
        risk = "medium"
    else:
        risk = "low"

    if impact.get("expectations"):
        warnings.append("Modeled expectations reference this change path.")
    if impact.get("entities") or impact.get("relationships"):
        warnings.append("Schema entities or relationships may be affected.")

    return {
        "file": rel_path,
        "risk": risk,
        "requires_impact": risk in {"medium", "high", "critical"} or bool(impact.get("expectations")),
        "requires_handoff": risk in {"medium", "high", "critical"},
        "requires_warden_lock": requires_warden_lock,
        "impact": impact,
        "warnings": warnings,
        "next_steps": next_steps,
    }


def _format_agent_preflight(preflight):
    impact = preflight.get("impact", {})
    lines = [
        f"Preflight: {preflight['file']}",
        f"Risk: {preflight['risk']}",
        f"Requires impact: {'yes' if preflight['requires_impact'] else 'no'}",
        f"Requires handoff: {'yes' if preflight['requires_handoff'] else 'no'}",
        f"Requires warden lock: {'yes' if preflight['requires_warden_lock'] else 'no'}",
        "",
        "Impact:",
        f"  Expectations: {', '.join(impact.get('expectations') or ['none'])}",
        f"  Entities: {', '.join(impact.get('entities') or ['none'])}",
        f"  Relationships: {', '.join(impact.get('relationships') or ['none'])}",
    ]
    if preflight.get("warnings"):
        lines.extend(["", "Warnings:"])
        lines.extend(f"  - {warning}" for warning in preflight["warnings"])
    if preflight.get("next_steps"):
        lines.extend(["", "Next steps:"])
        lines.extend(f"  - {step}" for step in preflight["next_steps"])
    return "\n".join(lines)


def _resolve_workspace_dir(raw_dir=None):
    if raw_dir:
        return Path(raw_dir).expanduser().resolve()
    root = find_project_root(os.getcwd())
    return Path(root).resolve() if root else Path.cwd().resolve()


def _agent_start_text(target_dir):
    target_path = Path(target_dir).expanduser().resolve()
    manifest_path = target_path / ".etg" / "entigram.yaml"
    policy_path = target_path / ".etg" / "agent_policy.md"
    schema_path = target_path / "schema.lds"
    lines = [
        f"Entigram agent start: {target_path}",
        "",
        "Required local files:",
        f"  .etg/entigram.yaml: {'found' if manifest_path.exists() else 'missing'}",
        f"  .etg/agent_policy.md: {'found' if policy_path.exists() else 'missing'}",
        f"  schema.lds: {'found' if schema_path.exists() else 'missing'}",
        "",
        _agent_instructions_text().rstrip(),
        "",
        "Compact hydration vector:",
        get_hydration_vector(target_path, compact=True),
    ]
    return "\n".join(lines)


def get_default_engine():
    """Probes the system for available AI agents (Antigravity, Claude, or Codex)."""
    if shutil.which("agy"):
        return "Antigravity"
    if shutil.which("claude"):
        return "Claude Code"
    if shutil.which("ollama"):
        return "Ollama"
    if shutil.which("codex"):
        return "Codex"
    return "Antigravity"

def load_plugins(subparsers):
    """Loads custom CLI commands from the user's .etg/plugins directory."""
    plugins_dir = Path(".etg/plugins")
    if not plugins_dir.exists():
        return

    for plugin_file in plugins_dir.glob("*.py"):
        if plugin_file.name == "__init__.py":
            continue
            
        module_name = f"entigram_plugin_{plugin_file.stem}"
        spec = importlib.util.spec_from_file_location(module_name, plugin_file)
        if spec and spec.loader:
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            try:
                spec.loader.exec_module(module)
                if hasattr(module, 'register_command'):
                    module.register_command(subparsers)
            except Exception as e:
                print(f"Failed to load plugin {plugin_file.name}: {e}")

def _catalog_metadata_for_package(catalog_path: str, package_dir: str) -> dict:
    from entigram.package_catalog import load_package_catalog

    catalog = load_package_catalog(catalog_path)
    package_path = Path(package_dir).expanduser().resolve()
    candidates = {package_path.name}
    if package_path.parent.name:
        candidates.add(f"{package_path.parent.name}/{package_path.name}")

    for package in catalog.get("packages", []):
        if package.get("name") in candidates:
            return package

    package_parts = package_path.parts
    for package in catalog.get("packages", []):
        name_parts = tuple((package.get("name") or "").split("/"))
        if name_parts and package_parts[-len(name_parts):] == name_parts:
            return package

    raise ValueError(f"catalog entry not found for package: {package_dir}")


def _format_discovery_findings(findings) -> str:
    if not findings:
        return ""
    severity_rank = {"error": 0, "warning": 1, "info": 2}
    sorted_findings = sorted(
        findings,
        key=lambda item: (severity_rank.get(item.severity, 99), item.entity or "", item.attribute or "", item.code),
    )
    counts = {}
    for finding in sorted_findings:
        counts[finding.severity] = counts.get(finding.severity, 0) + 1
    count_text = ", ".join(f"{severity}: {count}" for severity, count in sorted(counts.items()))
    lines = [f"Discovery review findings ({count_text}):"]
    for finding in sorted_findings:
        location = finding.entity or "<source>"
        if finding.attribute:
            location = f"{location}.{finding.attribute}"
        lines.append(f" - [{finding.severity.upper()}] {finding.code} at {location}: {finding.message}")
        if finding.recommendation:
            lines.append(f"   Recommendation: {finding.recommendation}")
    return "\n".join(lines)


def _resolve_merge_paths(args):
    from entigram.sqlite_ledger.paths import resolve_ledger_path

    local_root = Path(find_project_root(os.getcwd()) or os.getcwd()).resolve()
    local_schema = local_root / "schema.lds"
    if not local_schema.exists():
        raise FileNotFoundError(f"Local schema.lds not found in {local_root}")

    remote_input = Path(args.remote_path).expanduser().resolve()
    remote_workspace = None
    if remote_input.is_dir():
        remote_workspace = remote_input
        remote_ledger = resolve_ledger_path(str(remote_workspace), create_default=False)
    else:
        remote_ledger = remote_input
        if remote_input.name in {"state.db", "entigram_state.db"} and remote_input.parent.name == ".etg":
            remote_workspace = remote_input.parent.parent

    if args.remote_schema:
        remote_schema = Path(args.remote_schema).expanduser().resolve()
    elif remote_workspace:
        candidates = [remote_workspace / "schema.lds", remote_workspace / "draft_schema.lds"]
        remote_schema = next((candidate for candidate in candidates if candidate.exists()), None)
        if remote_schema is None:
            raise FileNotFoundError(f"Remote schema.lds or draft_schema.lds not found in {remote_workspace}")
    else:
        raise ValueError("--schema is required when --from points directly to a state.db file outside a workspace")

    if not remote_ledger.exists():
        raise FileNotFoundError(f"Remote state ledger not found: {remote_ledger}")
    if not remote_schema.exists():
        raise FileNotFoundError(f"Remote schema not found: {remote_schema}")

    return local_root, local_schema, remote_workspace, remote_schema, remote_ledger


def _run_merge_command(args) -> int:
    from entigram.governance.warden import Warden
    from entigram.schema_compiler.merge_renderer import MergeRenderer
    from entigram.schema_compiler.merger import SchemaMerger
    from entigram.schema_compiler.parser import SchemaParser
    from entigram.sqlite_ledger.manager import LedgerManager
    from entigram.sqlite_ledger.paths import resolve_ledger_path

    local_root, local_schema, remote_workspace, remote_schema, remote_ledger = _resolve_merge_paths(args)

    if not Warden(str(local_root)).verify_integrity():
        return 1
    if remote_workspace and not Warden(str(remote_workspace)).verify_integrity():
        print(f"❌ Remote workspace Warden integrity check failed: {remote_workspace}")
        return 1

    print("🔀 Merging remote schema into local workspace...")
    print()
    print(f"   Source: {remote_ledger}")
    print(f"   Target: {resolve_ledger_path(str(local_root))}")
    print()

    ledger = LedgerManager(str(resolve_ledger_path(str(local_root))))
    renderer = MergeRenderer()
    try:
        merger = SchemaMerger(str(local_schema), str(remote_schema), ledger)
        diff = merger.diff()
        renderer.render_diff(diff)

        if args.dry_run:
            return 0

        result = merger.merge(strategy=args.strategy)
        output_path = local_root / ("schema.lds" if getattr(args, "apply", False) else "draft_schema.lds")
        output_path.write_text(result.merged_schema)
        SchemaParser(output_path.read_text()).parse()
        result.output_path = str(output_path)

        result.ledger_stats = merger.merge_state_db(str(remote_ledger))
        if getattr(args, "apply", False):
            Warden(str(local_root)).lock_fingerprint()
            result.warden_locked = True

        renderer.render_result(result)
        if not diff.has_conflicts:
            print(
                f"✅ Clean merge: {len(diff.added_entities)} entities added, "
                f"{result.ledger_stats.get('semantic_alignments', 0)} alignments imported"
            )
        return 0
    finally:
        ledger.close()


def _schema_entity_names(schema_content: str) -> list:
    return re.findall(r"(?m)^\s*ENTITY:\s*([A-Za-z_][A-Za-z0-9_]*)", schema_content or "")


def _first_present(*values, default=None):
    for value in values:
        if value is not None:
            return value
    return default


def _concise_hydration_payload(full_payload: dict, schema_content: str) -> dict:
    boot = full_payload["ENTIGRAM_BOOT_VECTOR"]
    status = boot.get("current_delivery_status") or {}
    snapshot = status.get("snapshot") or boot.get("latest_delivery_snapshot") or {}
    commissioner = boot.get("commissioner") or {}
    entity_names = _schema_entity_names(schema_content)
    return {
        "ENTIGRAM_BOOT_SUMMARY": {
            "version": boot.get("version"),
            "package_version": boot.get("package_version"),
            "workspace_schema_version": boot.get("workspace_schema_version"),
            "packages": boot.get("packages", []),
            "agent_policy_path": boot.get("agent_policy_path"),
            "agent_policy": boot.get("agent_policy", ""),
            "schema_path": "schema.lds",
            "schema_entity_count": len(entity_names),
            "schema_entities": entity_names,
            "delivery_status": status.get("status") or "unknown",
            "warden_status": status.get("warden_status") or snapshot.get("warden_status") or "unknown",
            "snapshot_id": snapshot.get("snapshot_id"),
            "expectation_count": _first_present(
                status.get("expectation_count"),
                commissioner.get("expectation_count"),
                snapshot.get("expectation_count"),
                default=0,
            ),
            "missing_proof_count": _first_present(
                status.get("missing_proof_count"),
                commissioner.get("missing_proof_count"),
                snapshot.get("missing_proof_count"),
                default=0,
            ),
            "blocked_count": status.get("blocked_count", 0),
            "next_commands": [
                "etg broker preflight --file <path>",
                "etg broker impact --file <path>",
                "etg broker handoff",
                "etg broker status",
            ],
            "full_vector_command": "hydrate --full",
            "timestamp": boot.get("timestamp"),
        }
    }


def get_hydration_vector(target_path: Path, compact: bool = False, full: bool = False) -> str:
    """Deterministic boot sequence to align LLM state vector by flattening ledger and Schema."""
    entigram_dir = target_path / ".etg"
    manifest_path = entigram_dir / "entigram.yaml"
    from entigram.sqlite_ledger.paths import resolve_ledger_path
    ledger_path = resolve_ledger_path(str(target_path))

    if not manifest_path.exists():
        return f"❌ Error: Not an Entigram workspace (missing {manifest_path})"

    # 1. Load Manifest
    try:
        yaml = _load_yaml_module()
    except RuntimeError as exc:
        return f"❌ {exc}"
    with open(manifest_path, "r") as f:
        manifest = yaml.safe_load(f)

    # 2. Extract state from ledger
    alignments = []
    resolutions = []
    delivery_evidence = []
    delivery_artifacts = []
    improvement_proposals = []
    latest_delivery_snapshot = None
    current_delivery_status = None
    if ledger_path.exists():
        from entigram.sqlite_ledger.manager import LedgerManager
        manager = LedgerManager(str(ledger_path))
        conn = manager._get_connection()
        try:
            # Fetch alignments
            cursor = conn.execute("SELECT source_domain, target_domain, source_concept, target_concept FROM semantic_alignments WHERE status='approved'")
            alignments = [{"src_dom": r[0], "tgt_dom": r[1], "src_con": r[2], "tgt_con": r[3]} for r in cursor.fetchall()]
            
            # Fetch recent resolutions
            cursor = conn.execute("SELECT conflict_id, entity_type, resolved_state FROM human_resolutions ORDER BY timestamp DESC LIMIT 10")
            resolutions = [{"id": r[0], "type": r[1], "state": r[2]} for r in cursor.fetchall()]

            delivery_evidence = manager.get_delivery_evidence(passed_only=True, limit=10)
            delivery_artifacts = manager.get_delivery_artifacts(limit=10)
            improvement_proposals = manager.get_improvement_proposals(limit=10)
            latest_delivery_snapshot = manager.get_latest_snapshot()
            if latest_delivery_snapshot:
                from entigram.broker import EntigramBroker
                current_delivery_status = EntigramBroker(
                    str(target_path),
                    ledger=manager,
                    seed_synonyms=False,
                ).delivery_status()
        except Exception:
            pass
        finally:
            if not manager.db_path == ":memory:":
                conn.close()

    # 3. Load Schema (Schema)
    schema_path = target_path / "schema.lds"
    schema_content = ""
    if schema_path.exists():
        schema_content = schema_path.read_text()

    agent_policy_path = entigram_dir / "agent_policy.md"
    agent_policy = ""
    if agent_policy_path.exists():
        agent_policy = agent_policy_path.read_text()

    commissioner_checklist = {"expectation_count": 0, "items": []}
    if schema_content:
        from entigram.governance.commissioner import Commissioner
        commissioner_checklist = Commissioner(schema_content).build_checklist()

    # 4. Flatten to High-Density String
    workspace_schema_version = manifest.get(
        "workspace_schema_version",
        manifest.get("entigram_version", "1"),
    )
    package_version = get_package_version()

    boot_payload = {
        "ENTIGRAM_BOOT_VECTOR": {
            "version": package_version,
            "package_version": package_version,
            "workspace_schema_version": workspace_schema_version,
            "packages": list(manifest.get("packages", {}).keys()),
            "agent_policy_path": ".etg/agent_policy.md",
            "agent_policy": agent_policy,
            "physical_laws": schema_content,
            "commissioner": commissioner_checklist,
            "semantic_alignments": alignments,
            "settled_decisions": resolutions,
            "delivery_evidence": delivery_evidence,
            "delivery_artifacts": delivery_artifacts,
            "improvement_proposals": improvement_proposals,
            "latest_delivery_snapshot": latest_delivery_snapshot,
            "current_delivery_status": current_delivery_status,
            "timestamp": datetime.now().isoformat()
        }
    }
    output_payload = boot_payload if full else _concise_hydration_payload(boot_payload, schema_content)

    output = f"--- ENTIGRAM HYDRATION SEQUENCE ---\n"
    if compact:
        output += json.dumps(output_payload, separators=(',', ':'))
    else:
        output += json.dumps(output_payload, indent=2)
    output += f"\n--- SEQUENCE COMPLETE ---"
    return output

def launch_ui(target_dir=None):
    """Launches the Streamlit UI dashboard."""
    import subprocess
    if importlib.util.find_spec("streamlit") is None:
        print("❌ Streamlit is not installed in this environment.")
        print("The CLI/MCP runtime is headless by default.")
        print("For pipx installs: pipx install 'entigram-ai[ui]'")
        print("For an existing pipx install: pipx inject entigram-ai streamlit")
        print("For Homebrew installs, run the MCP/CLI normally or install the UI with pipx.")
        return False

    ui_path = Path(__file__).parent.parent / "ui" / "app.py"
    print(f"🚀 Launching Entigram Visual Dashboard...")
    try:
        # Pass the directory to the UI via environment variable
        env = os.environ.copy()
        
        # Resolve target directory (search upwards if not provided)
        if not target_dir:
            root = find_project_root(os.getcwd())
            target_dir = str(root) if root else os.getcwd()
        
        env["ENTIGRAM_PROJECT_DIR"] = str(Path(target_dir).expanduser().resolve())
        
        # Invoke streamlit via the current python interpreter to ensure it works in venvs (like Brew)
        subprocess.run([sys.executable, "-m", "streamlit", "run", str(ui_path)], env=env, check=True)
        return True
    except KeyboardInterrupt:
        print("\n👋 Dashboard stopped.")
        return True
    except Exception as e:
        print(f"❌ Failed to launch dashboard: {e}")
        return False

def main():
    if Path(sys.argv[0]).name == "hydrate":
        sys.argv.insert(1, "hydrate")

    parser = argparse.ArgumentParser(description="Entigram Headless Compiler CLI")
    parser.add_argument("--version", action="version", version=f"etg {get_package_version()}")
    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # Load user plugins
    load_plugins(subparsers)

    # init command
    init_parser = subparsers.add_parser("init", help="Initialize a workspace")
    init_parser.add_argument("--dir", help="Target directory (defaults to current directory)")
    init_parser.add_argument("--packages", default="Entigram Schemas", help="Comma separated packages")
    default_engine = get_default_engine()
    init_parser.add_argument("--engine", default=default_engine, help=f"CLI Engine (Detected: {default_engine})")
    init_parser.add_argument("--force", action="store_true", help="Force initialization even if workspace exists")

    # config command
    config_parser = subparsers.add_parser("config", help="Manage workspace configuration")
    config_parser.add_argument("--dir", default=".", help="Target directory")
    config_parser.add_argument("--engine", help="Update the default CLI engine")
    config_parser.add_argument("--list", action="store_true", help="List current configuration")

    # warden command
    warden_parser = subparsers.add_parser("warden", help="Manage schema contract integrity (The Warden)")
    warden_subparsers = warden_parser.add_subparsers(dest="warden_command", help="Warden commands")
    warden_parser.add_argument("--dir", default=".", help="Target directory")
    
    warden_subparsers.add_parser("lock", help="Lock the current Schema and Ontology via cryptographic checksum")
    warden_subparsers.add_parser("unlock", help="Unlock the domain for authorized modifications")
    warden_check_parser = warden_subparsers.add_parser("check", help="Verify the current integrity state")
    warden_check_parser.add_argument("--json", action="store_true", dest="json_output", help="Print HaltEvent JSON")

    # build command
    build_parser = subparsers.add_parser("build", help="Build models from Schema")
    build_parser.add_argument("--dir", help="Target directory (defaults to current directory)")
    build_parser.add_argument("--format", choices=["sql", "ttl", "mermaid"], default="sql", help="Output format")

    # discover command
    discover_parser = subparsers.add_parser("discover", help="Discover a draft Schema from an external source")
    discover_parser.add_argument("--db", help="Path to a SQLite database file (.db or .sqlite); compatibility alias for --source sqlite --path")
    discover_parser.add_argument("--path", help="Path to the source to inspect")
    discover_parser.add_argument("--source", default="auto", help="Discovery source adapter")
    discover_parser.add_argument("--adapter-module", help="Path to a standard-package source_adapter.py module to register before discovery")
    discover_parser.add_argument("--domain", help="Domain/entity name for file-based discovery")
    discover_parser.add_argument("--out", help="Output Schema file (prints to stdout if omitted)")
    discover_parser.add_argument("--metadata", action="store_true", help="Include discovery provenance as a Schema comment")
    discover_parser.add_argument("--report-json", action="store_true", help="Print a structured discovery report instead of only LDS")

    # merge command
    merge_parser = subparsers.add_parser("merge", help="Merge a remote schema and state ledger into the local workspace")
    merge_parser.add_argument("--from", dest="remote_path", required=True,
                              help="Path to remote .etg/state.db or workspace root to merge from")
    merge_parser.add_argument("--schema", dest="remote_schema",
                              help="Path to remote schema.lds (auto-detected from workspace if --from is a directory)")
    merge_parser.add_argument("--strategy", choices=["interactive", "union", "ours", "theirs", "auto"],
                              default="interactive",
                              help="Merge strategy (default: interactive)")
    merge_parser.add_argument("--dry-run", action="store_true",
                              help="Show diff without applying changes")
    merge_parser.add_argument("--apply", action="store_true",
                              help="Write merged schema to schema.lds and relock Warden fingerprint")

    # package command
    pkg_parser = subparsers.add_parser("package", help="Package management")
    pkg_subparsers = pkg_parser.add_subparsers(dest="pkg_command", help="Package commands")
    
    create_pkg_parser = pkg_subparsers.add_parser("create", help="Create a new package")
    create_pkg_parser.add_argument("--name", required=True, help="Package name")
    create_pkg_parser.add_argument("--out", default="packages", help="Output directory")
    create_pkg_parser.add_argument("--skill", help="Base skill to inherit from")
    create_pkg_parser.add_argument("--depends", help="Dependency skill")

    create_from_schema_parser = pkg_subparsers.add_parser("import", help="Import an existing Schema into a package")
    create_from_schema_parser.add_argument("--schema", required=True, help="Path to Schema file")
    create_from_schema_parser.add_argument("--name", help="Package name")
    create_from_schema_parser.add_argument("--out", default=".etg/packages", help="Output directory")

    install_pkg_parser = pkg_subparsers.add_parser("install", help="Install a package from a registered remote repository")
    install_pkg_parser.add_argument("--name", required=True, help="Package name")
    install_pkg_parser.add_argument("--dir", default=".", help="Target directory")

    suggest_pkg_parser = pkg_subparsers.add_parser("suggest", help="Suggest standard packages from a package catalog")
    suggest_pkg_parser.add_argument("--query", required=True, help="Use case, source system, or adapter need")
    suggest_pkg_parser.add_argument("--catalog", default="standard_package_catalog.json", help="Path to the standard package catalog JSON")
    suggest_pkg_parser.add_argument("--limit", type=int, default=5, help="Maximum suggestions to print")
    suggest_pkg_parser.add_argument("--json", action="store_true", help="Print machine-readable suggestions")

    audit_pkg_parser = pkg_subparsers.add_parser("audit", help="Audit standard package catalog metadata")
    audit_pkg_parser.add_argument("--catalog", default="standard_package_catalog.json", help="Path to the standard package catalog JSON")
    audit_pkg_parser.add_argument("--json", action="store_true", help="Print machine-readable audit results")
    audit_pkg_parser.add_argument("--verify-signatures", action="store_true", help="Also verify package signatures for catalog entries")
    audit_pkg_parser.add_argument("--packages-root", help="Root directory containing catalog package paths; defaults to catalog parent")

    manifest_pkg_parser = pkg_subparsers.add_parser("manifest", help="Generate a deterministic package manifest")
    manifest_pkg_parser.add_argument("--package", required=True, help="Path to a package directory")
    manifest_pkg_parser.add_argument("--catalog", help="Optional catalog JSON used to embed package metadata")
    manifest_pkg_parser.add_argument("--out", help="Manifest output path; defaults to package.manifest.json")
    manifest_pkg_parser.add_argument("--json", action="store_true", help="Print manifest JSON")

    sign_pkg_parser = pkg_subparsers.add_parser("sign", help="Sign a package manifest with an Ed25519 key")
    sign_pkg_parser.add_argument("--package", required=True, help="Path to a package directory")
    sign_pkg_parser.add_argument("--catalog", help="Optional catalog JSON used to generate/update manifest before signing")
    sign_pkg_parser.add_argument("--key", help="Ed25519 private key path; auto-created at .etg/package_signing_ed25519_private.pem if omitted")
    sign_pkg_parser.add_argument("--no-manifest", action="store_true", help="Do not regenerate package.manifest.json before signing")
    sign_pkg_parser.add_argument("--json", action="store_true", help="Print machine-readable signing result")

    verify_pkg_parser = pkg_subparsers.add_parser("verify", help="Verify package manifest hashes and signature")
    verify_pkg_parser.add_argument("--package", required=True, help="Path to a package directory")
    verify_pkg_parser.add_argument("--no-signature-required", action="store_true", help="Warn instead of failing when signature is absent")
    verify_pkg_parser.add_argument("--json", action="store_true", help="Print machine-readable verification result")

    sign_catalog_parser = pkg_subparsers.add_parser("sign-catalog", help="Sign a standard package catalog")
    sign_catalog_parser.add_argument("--catalog", default="standard_package_catalog.json", help="Path to catalog JSON")
    sign_catalog_parser.add_argument("--key", help="Ed25519 private key path; auto-created at .etg/package_signing_ed25519_private.pem if omitted")
    sign_catalog_parser.add_argument("--out", help="Signature output path; defaults to <catalog>.sig")
    sign_catalog_parser.add_argument("--json", action="store_true", help="Print machine-readable signing result")

    verify_catalog_parser = pkg_subparsers.add_parser("verify-catalog", help="Verify a signed package catalog")
    verify_catalog_parser.add_argument("--catalog", default="standard_package_catalog.json", help="Path to catalog JSON")
    verify_catalog_parser.add_argument("--signature", help="Catalog signature path; defaults to <catalog>.sig")
    verify_catalog_parser.add_argument("--json", action="store_true", help="Print machine-readable verification result")

    # registry command
    registry_parser = subparsers.add_parser("registry", help="Manage package registries")
    registry_subparsers = registry_parser.add_subparsers(dest="registry_command", help="Registry commands")
    
    add_reg_parser = registry_subparsers.add_parser("add", help="Add a remote git repository as a package registry")
    add_reg_parser.add_argument("--url", required=True, help="Git URL of the registry")
    add_reg_parser.add_argument("--dir", default=".", help="Target directory")
    
    list_reg_parser = registry_subparsers.add_parser("list", help="List configured registries")
    list_reg_parser.add_argument("--dir", default=".", help="Target directory")

    # inject command
    inject_parser = subparsers.add_parser("inject", help="Inject compiled SQLite databases for active domains")
    inject_parser.add_argument("--dir", default=".", help="Target directory")
    inject_parser.add_argument("--crsqlite", action="store_true", help="Enable CR-SQLite Conflict-free Replicated Relations")

    # query command
    query_parser = subparsers.add_parser("query", help="Execute a GraphQL-LD federated query")
    query_parser.add_argument("--dir", default=".", help="Target directory")
    query_parser.add_argument("--graphql", required=True, help="GraphQL query string")

    # serve command
    serve_parser = subparsers.add_parser("serve", help="Launch the Entigram MCP server")
    serve_parser.add_argument("--dir", default=".", help="Target directory")
    serve_parser.add_argument(
        "--transport",
        choices=["stdio", "sse", "graphql"],
        default="stdio",
        help="Server transport: stdio MCP (default), sse MCP, or legacy graphql",
    )
    serve_parser.add_argument("--host", default="127.0.0.1", help="Host for SSE transport (default: 127.0.0.1)")
    serve_parser.add_argument("--port", type=int, default=8080, help="Port for SSE or legacy GraphQL (default: 8080)")
    serve_parser.add_argument(
        "--legacy-graphql",
        action="store_true",
        help="Launch the previous Federated GraphQL Hub instead of MCP",
    )

    # panel-bridge command
    panel_bridge_parser = subparsers.add_parser(
        "panel-bridge",
        help="Run a WebSocket bridge for Agent-Hosted Panel bot proxying",
    )
    panel_bridge_parser.add_argument("--host", default="127.0.0.1", help="Bind host (default: 127.0.0.1)")
    panel_bridge_parser.add_argument("--port", type=int, default=9090, help="Bind port (default: 9090)")
    panel_bridge_parser.add_argument(
        "--proxy-url",
        default="http://127.0.0.1:11435",
        help="Cloudflare/Ollama proxy URL for LLM completions (default: http://127.0.0.1:11435)",
    )
    panel_bridge_parser.add_argument("--dir", default=".", help="Target directory for ledger")

    # cloudflare-ollama-proxy command
    cloudflare_proxy_parser = subparsers.add_parser(
        "cloudflare-ollama-proxy",
        help="Run an Ollama-compatible proxy backed by Cloudflare Workers AI",
    )
    cloudflare_proxy_parser.add_argument("--host", default="127.0.0.1", help="Bind host")
    cloudflare_proxy_parser.add_argument("--port", type=int, default=11435, help="Bind port")
    cloudflare_proxy_parser.add_argument(
        "--model",
        default=None,
        help="Workers AI model (default: @cf/zai-org/glm-5.2)",
    )
    cloudflare_proxy_parser.add_argument(
        "--model-profile",
        choices=["auto", "coding", "configured", "conversation"],
        default=None,
        help="Model selection profile when Cloudflare model discovery is enabled",
    )
    cloudflare_proxy_parser.add_argument(
        "--no-discover-models",
        action="store_true",
        help="Disable Cloudflare model discovery and use the configured model",
    )
    cloudflare_proxy_parser.add_argument(
        "--env-file",
        default=".env",
        help="Environment file to load before startup",
    )
    cloudflare_proxy_parser.add_argument(
        "--smoke-test",
        action="store_true",
        help="Call Cloudflare once and exit",
    )
    cloudflare_proxy_parser.add_argument("--timeout-seconds", type=int, default=None, help="Cloudflare request timeout")
    cloudflare_proxy_parser.add_argument("--retry-attempts", type=int, default=None, help="Cloudflare transient retry attempts")
    cloudflare_proxy_parser.add_argument("--retry-sleep-seconds", type=int, default=None, help="Default sleep between retries")
    cloudflare_proxy_parser.add_argument(
        "--no-compact-prompts",
        action="store_true",
        help="Disable oversized tool-result compaction before forwarding prompts",
    )
    cloudflare_proxy_parser.add_argument(
        "--max-tool-result-chars",
        type=int,
        default=None,
        help="Maximum characters to keep for each tool result when compaction is enabled",
    )

    cloudflare_claude_parser = subparsers.add_parser(
        "cloudflare-claude",
        help="Start a dynamic Cloudflare Ollama proxy and launch Claude Code through it",
    )
    cloudflare_claude_parser.add_argument("--host", default="127.0.0.1", help="Bind host")
    cloudflare_claude_parser.add_argument("--port", type=int, default=0, help="Bind port; 0 selects a free port")
    cloudflare_claude_parser.add_argument(
        "--model",
        default=None,
        help="Workers AI model (default: @cf/zai-org/glm-5.2)",
    )
    cloudflare_claude_parser.add_argument(
        "--model-profile",
        choices=["auto", "coding", "configured", "conversation"],
        default=None,
        help="Model selection profile when Cloudflare model discovery is enabled",
    )
    cloudflare_claude_parser.add_argument(
        "--no-discover-models",
        action="store_true",
        help="Disable Cloudflare model discovery and use the configured model",
    )
    cloudflare_claude_parser.add_argument(
        "--env-file",
        default=".env",
        help="Environment file to load before startup",
    )
    cloudflare_claude_parser.add_argument("--timeout-seconds", type=int, default=None, help="Cloudflare request timeout")
    cloudflare_claude_parser.add_argument("--retry-attempts", type=int, default=None, help="Cloudflare transient retry attempts")
    cloudflare_claude_parser.add_argument("--retry-sleep-seconds", type=int, default=None, help="Default sleep between retries")
    cloudflare_claude_parser.add_argument(
        "--no-compact-prompts",
        action="store_true",
        help="Disable oversized tool-result compaction before forwarding prompts",
    )
    cloudflare_claude_parser.add_argument(
        "--max-tool-result-chars",
        type=int,
        default=None,
        help="Maximum characters to keep for each tool result when compaction is enabled",
    )

    # ui command
    ui_parser = subparsers.add_parser("ui", help="Launch the Entigram Visual Dashboard")
    ui_parser.add_argument("--dir", default=".", help="Target directory")

    # agent command
    agent_launch_parser = subparsers.add_parser("agent", help="Start agent boot, show instructions, or launch your configured AI agent")
    agent_launch_parser.add_argument(
        "agent_action",
        nargs="?",
        choices=["start", "instructions"],
        help="Run portable agent boot checks or print copy-paste instructions",
    )
    agent_launch_parser.add_argument("--dir", help="Target directory")
    agent_launch_parser.add_argument("--engine", help="Override CLI Engine (Claude Code, Antigravity, Codex, Ollama)")
    agent_launch_parser.add_argument("--model", help="Specify model (e.g., qwen2.5, claude-3-opus)")
    agent_launch_parser.add_argument("--ollama-launch-option", help="Ollama launch target (e.g., claude, codex)")
    agent_launch_parser.add_argument("--headless", action="store_true", help="Run headlessly using stdin piping (Antigravity only)")

    subparsers.add_parser("agent-instructions", help="Print copy-paste instructions for agents")

    # interview command
    interview_parser = subparsers.add_parser("interview", help="Launch an autonomous agent to model your domain")
    interview_parser.add_argument("--dir", help="Target directory")
    interview_parser.add_argument("--engine", help="Override CLI Engine (Claude Code, Antigravity, Codex, Ollama)")
    interview_parser.add_argument("--model", help="Specify model")
    interview_parser.add_argument("--ollama-launch-option", help="Ollama launch target (e.g., claude, codex)")
    interview_parser.add_argument("--headless", action="store_true", help="Run headlessly using stdin piping (Antigravity only)")

    # model command (One-shot headless modeling)
    model_parser = subparsers.add_parser("model", help="One-shot autonomous domain modeling using headless agent")
    model_parser.add_argument("description", help="Natural language description of the domain to model")
    model_parser.add_argument("--dir", help="Target directory")
    model_parser.add_argument("--engine", help="Override CLI Engine")
    model_parser.add_argument("--append", action="store_true", help="Append to draft_schema.lds instead of printing only")
    model_parser.add_argument(
        "--max-repair-attempts",
        type=int,
        default=3,
        help="Maximum Gate feedback retries after the first invalid payload",
    )
    model_parser.add_argument(
        "--no-auto-repair",
        action="store_true",
        help="Disable automatic Gate feedback retries",
    )

    # cloud command
    cloud_parser = subparsers.add_parser("cloud", help="Entigram Cloud Managed Services")
    cloud_subparsers = cloud_parser.add_subparsers(dest="cloud_command", help="Cloud commands")
    
    login_parser = cloud_subparsers.add_parser("login", help="Login to Entigram Cloud")
    login_parser.add_argument("--token", required=True, help="API Token")
    
    sync_cloud_parser = cloud_subparsers.add_parser("sync", help="Synchronize local ledger with Entigram Cloud")
    sync_cloud_parser.add_argument("--dir", default=".", help="Target directory")
    sync_cloud_parser.add_argument("--endpoint", default="https://api.entigram.ai/v1", help="Cloud endpoint")

    # broker command
    broker_parser = subparsers.add_parser("broker", help="Agent orchestration broker")
    broker_subparsers = broker_parser.add_subparsers(dest="broker_command", help="Broker commands")
    broker_parser.add_argument("--dir", default=".", help="Target directory")

    decide_parser = broker_subparsers.add_parser("decide", help="Record a proposed decision")
    decide_parser.add_argument("--id", required=True, help="Conflict ID")
    decide_parser.add_argument("--type", required=True, help="Entity Type")
    decide_parser.add_argument("--state", required=True, help="Resolved State")
    decide_parser.add_argument("--rationale", required=True, help="Rationale")
    decide_parser.add_argument("--json", action="store_true", dest="json_output", help="Print result as JSON")

    conflict_parser = broker_subparsers.add_parser("conflict", help="Report a contradiction")
    conflict_parser.add_argument("--id", required=True, help="Conflict ID")
    conflict_parser.add_argument("--type", required=True, help="Entity Type")
    conflict_parser.add_argument("--states", required=True, help="Proposed States (JSON)")
    conflict_parser.add_argument("--agent", required=True, help="Agent ID")
    conflict_parser.add_argument("--json", action="store_true", dest="json_output", help="Print result as JSON")

    check_parser = broker_subparsers.add_parser("check", help="Check for an existing decision")
    check_parser.add_argument("--id", required=True, help="Conflict ID")

    broker_subparsers.add_parser("conflicts", help="List all pending conflicts")
    broker_subparsers.add_parser("resolutions", help="List all settled resolutions")
    broker_subparsers.add_parser("validate", help="Validate current Schema model")

    agent_register_parser = broker_subparsers.add_parser(
        "agent-register",
        help="Register or update an agent capability profile",
    )
    agent_register_parser.add_argument("--agent", required=True, help="Stable agent ID")
    agent_register_parser.add_argument("--class", dest="agent_class", help="Agent class, e.g. strong, continuation, read-only")
    agent_register_parser.add_argument("--provider", help="Provider or runtime")
    agent_register_parser.add_argument("--model", help="Underlying model")
    agent_register_parser.add_argument("--score", type=float, default=0.5, help="Observed reliability score from 0.0 to 1.0")
    agent_register_parser.add_argument(
        "--capability",
        action="append",
        default=[],
        help="Task-specific score in TASK_TYPE=SCORE form; may be repeated",
    )
    agent_register_parser.add_argument("--allow", action="append", default=[], help="Allowed task classes or risk levels")
    agent_register_parser.add_argument("--restrict", action="append", default=[], help="Restricted task classes or risk levels")
    agent_register_parser.add_argument("--notes", help="Operator notes")
    agent_register_parser.add_argument("--json", action="store_true", dest="json_output", help="Print result as JSON")

    agent_list_parser = broker_subparsers.add_parser(
        "agent-list",
        help="List registered agents and capability scores",
    )
    agent_list_parser.add_argument("--json", action="store_true", dest="json_output", help="Print result as JSON")

    task_enqueue_parser = broker_subparsers.add_parser(
        "task-enqueue",
        help="Persist a task for capability-gated assignment",
    )
    task_enqueue_parser.add_argument("--id", required=True, help="Stable task ID")
    task_enqueue_parser.add_argument("--title", required=True, help="Human-readable task title")
    task_enqueue_parser.add_argument("--type", required=True, dest="task_type", help="Task type, e.g. tests, docs, terraform")
    task_enqueue_parser.add_argument(
        "--risk",
        default="low_risk",
        choices=["read_only", "low_risk", "medium_risk", "high_risk", "critical"],
        help="Risk level for assignment gating",
    )
    task_enqueue_parser.add_argument("--required-score", type=float, help="Minimum capability score override")
    task_enqueue_parser.add_argument("--details", help="JSON details to persist with the task")
    task_enqueue_parser.add_argument("--json", action="store_true", dest="json_output", help="Print result as JSON")

    task_assign_parser = broker_subparsers.add_parser(
        "task-assign",
        help="Assign a task to an agent only if capability gates pass",
    )
    task_assign_parser.add_argument("--id", required=True, help="Task ID")
    task_assign_parser.add_argument("--agent", required=True, help="Agent ID")
    task_assign_parser.add_argument("--json", action="store_true", dest="json_output", help="Print result as JSON")

    task_list_parser = broker_subparsers.add_parser(
        "task-list",
        help="List queued and assigned agent tasks",
    )
    task_list_parser.add_argument("--status", help="Filter by task status")
    task_list_parser.add_argument("--json", action="store_true", dest="json_output", help="Print result as JSON")

    hibernate_parser = broker_subparsers.add_parser(
        "hibernate",
        help="Persist a low-token checkpoint for external scheduler resume",
    )
    hibernate_parser.add_argument("--agent", required=True, help="Agent ID")
    hibernate_parser.add_argument("--run-id", help="Current run/session ID")
    hibernate_parser.add_argument("--remaining-tokens", type=int, help="Observed remaining token budget")
    hibernate_parser.add_argument("--threshold", type=int, dest="token_threshold", help="Token threshold that triggered hibernation")
    hibernate_parser.add_argument("--refresh-window-end", help="Refresh window end timestamp")
    hibernate_parser.add_argument("--resume-after", help="Earliest timestamp an external scheduler should resume")
    hibernate_parser.add_argument("--summary", required=True, help="Durable checkpoint summary")
    hibernate_parser.add_argument("--next-action", required=True, help="Exact next action after resume")
    hibernate_parser.add_argument("--pending-task", action="append", default=[], help="Pending task ID; may be repeated")
    hibernate_parser.add_argument("--json", action="store_true", dest="json_output", help="Print result as JSON")

    resume_parser = broker_subparsers.add_parser(
        "resume",
        help="Show the latest hibernated checkpoint to resume",
    )
    resume_parser.add_argument("--agent", help="Filter by agent ID")
    resume_parser.add_argument("--mark-resumed", action="store_true", help="Mark the returned checkpoint as resumed")
    resume_parser.add_argument("--json", action="store_true", dest="json_output", help="Print result as JSON")

    preflight_parser = broker_subparsers.add_parser(
        "preflight",
        help="Show file risk, impact, and required governance steps before editing",
    )
    preflight_parser.add_argument("--file", required=True, help="Path intended for change")
    preflight_parser.add_argument("--json", action="store_true", dest="json_output", help="Print result as JSON")

    commission_parser = broker_subparsers.add_parser(
        "commission",
        aliases=["commissioner"],
        help="Run the Commissioner pre-handoff expectation checklist",
    )
    commission_parser.add_argument(
        "--proof",
        action="append",
        default=[],
        help="Proof text or artifact reference that satisfies a validation_check",
    )
    commission_parser.add_argument("--json", action="store_true", dest="json_output", help="Print checklist as JSON")
    commission_parser.add_argument("--agent", help="Agent ID to attribute evidence to")
    commission_parser.add_argument(
        "--blocked",
        action="append",
        default=[],
        metavar="CHECK",
        help="Mark a validation_check as Blocked (infra failure, not a proof gap)",
    )

    guard_parser = broker_subparsers.add_parser(
        "guard",
        aliases=["verify"],
        help="Run the out-of-the-box expectation guard before agent handoff",
    )
    guard_parser.add_argument(
        "--expectation",
        help="Filter to a specific expectation name",
    )
    guard_parser.add_argument(
        "--proof",
        action="append",
        default=[],
        help="Proof text or artifact reference",
    )
    guard_parser.add_argument(
        "--blocked",
        action="append",
        default=[],
        metavar="CHECK",
        help="Mark a validation_check as Blocked",
    )
    guard_parser.add_argument(
        "--no-run",
        action="store_true",
        help="Do not execute missing validation_check commands; only inspect recorded/provided proof",
    )
    guard_parser.add_argument(
        "--timeout",
        type=int,
        default=120,
        help="Seconds before a validation_check command times out",
    )
    guard_parser.add_argument("--agent", help="Agent ID to attribute evidence to")
    guard_parser.add_argument("--json", action="store_true", dest="json_output", help="Print result as JSON")

    deliver_parser = broker_subparsers.add_parser(
        "deliver",
        help="Run commissioner + write a delivery snapshot (deterministic handoff gate)",
    )
    deliver_parser.add_argument(
        "--expectation",
        help="Filter to a specific expectation name",
    )
    deliver_parser.add_argument(
        "--proof",
        action="append",
        default=[],
        help="Proof text or artifact reference",
    )
    deliver_parser.add_argument(
        "--artifact",
        action="append",
        default=[],
        help="Local artifact path to hash and attach to the delivery snapshot",
    )
    deliver_parser.add_argument(
        "--artifact-role",
        default="delivery_artifact",
        help="Role to apply to --artifact entries",
    )
    deliver_parser.add_argument(
        "--blocked",
        action="append",
        default=[],
        metavar="CHECK",
        help="Mark a validation_check as Blocked",
    )
    deliver_parser.add_argument("--agent", help="Agent ID to attribute this delivery to")
    deliver_parser.add_argument("--json", action="store_true", dest="json_output", help="Print result as JSON")

    status_parser = broker_subparsers.add_parser(
        "status",
        aliases=["diff"],
        help="Compare current expectations and artifacts against the latest delivery snapshot",
    )
    status_parser.add_argument(
        "--artifact",
        action="append",
        default=[],
        help="Local artifact path expected to be included in the delivery anchor",
    )
    status_parser.add_argument(
        "--artifact-role",
        default="delivery_artifact",
        help="Role to apply to --artifact entries",
    )
    status_parser.add_argument("--json", action="store_true", dest="json_output", help="Print result as JSON")

    audit_parser = broker_subparsers.add_parser(
        "export-audit",
        help="Export a tamper-evident JSON audit bundle for the current workspace",
    )
    audit_parser.add_argument("--out", help="Output path for the audit bundle JSON")
    audit_parser.add_argument(
        "--signing-key",
        help="Path to an Ed25519 private key. Defaults to .etg/audit_ed25519_private.pem",
    )
    audit_parser.add_argument("--json", action="store_true", dest="json_output", help="Print full bundle as JSON")

    resolve_parser = broker_subparsers.add_parser(
        "resolve",
        help="Run missing proof commands and record evidence (exit commissioner blocked state)",
    )
    resolve_parser.add_argument(
        "--run-missing-proofs",
        action="store_true",
        help="Attempt to run all validation_check commands and record the outcomes",
    )
    resolve_parser.add_argument("--agent", help="Agent ID to attribute evidence to")
    resolve_parser.add_argument("--json", action="store_true", dest="json_output", help="Print result as JSON")

    handoff_parser = broker_subparsers.add_parser(
        "handoff",
        help="Run guard, warden lock, deliver, and status without requiring Make",
    )
    handoff_parser.add_argument(
        "--proof",
        action="append",
        default=[],
        help="Proof text or artifact reference",
    )
    handoff_parser.add_argument(
        "--blocked",
        action="append",
        default=[],
        metavar="CHECK",
        help="Mark a validation_check as Blocked",
    )
    handoff_parser.add_argument(
        "--artifact",
        action="append",
        default=[],
        help="Local artifact path to hash and attach to the delivery snapshot",
    )
    handoff_parser.add_argument(
        "--artifact-role",
        default="delivery_artifact",
        help="Role to apply to --artifact entries",
    )
    handoff_parser.add_argument("--agent", help="Agent ID to attribute evidence to")
    handoff_parser.add_argument("--json", action="store_true", dest="json_output", help="Print result as JSON")

    add_package_parser = broker_subparsers.add_parser("add-package", help="Add a package to the manifest")
    add_package_parser.add_argument("--name", required=True, help="Package name")

    broker_subparsers.add_parser("sense", help="Scan active domains for contradictions")
    broker_subparsers.add_parser("sync", help="Propagate human resolutions to domain states")

    # improve command (improvement proposal lifecycle)
    improve_parser = subparsers.add_parser("improve", help="Manage Entigram improvement proposals")
    improve_subparsers = improve_parser.add_subparsers(dest="improve_command", help="Improve commands")
    improve_parser.add_argument("--dir", default=".", help="Target directory")

    propose_parser = improve_subparsers.add_parser("propose", help="Record a new improvement proposal")
    propose_parser.add_argument("--title", required=True, help="Short title for the proposal")
    propose_parser.add_argument("--model", required=True, help="Affected model or entity name")
    propose_parser.add_argument("--rationale", required=True, help="Why this improvement is needed")
    propose_parser.add_argument("--change", required=True, help="Proposed change description (plain text or JSON)")
    propose_parser.add_argument("--benefit", help="Expected benefit")
    propose_parser.add_argument("--by", help="Agent or author ID")
    propose_parser.add_argument(
        "--status",
        default="Proposed",
        help="Lifecycle status: Proposed (default), Reviewed, Implemented, Rejected",
    )

    list_proposals_parser = improve_subparsers.add_parser("list", help="List improvement proposals")
    list_proposals_parser.add_argument(
        "--status",
        default=None,
        help="Filter by lifecycle status (Proposed, Reviewed, Implemented, Rejected)",
    )
    list_proposals_parser.add_argument("--json", action="store_true", dest="json_output", help="Output as JSON")

    # learn command (Entigram_Lesson operational path)
    learn_parser = subparsers.add_parser("learn", help="Record and retrieve reusable lessons from deliveries")
    learn_subparsers = learn_parser.add_subparsers(dest="learn_command", help="Learn commands")
    learn_parser.add_argument("--dir", default=".", help="Target directory")

    learn_record_parser = learn_subparsers.add_parser("record", help="Persist a new lesson to the ledger")
    learn_record_parser.add_argument("--lesson", required=True, help="The lesson learned (plain language)")
    learn_record_parser.add_argument("--task", help="Source task or session context")
    learn_record_parser.add_argument("--rule", help="Reusable rule derived from the lesson")
    learn_record_parser.add_argument("--confidence", type=float, default=1.0, help="Confidence 0.0-1.0 (default 1.0)")
    learn_record_parser.add_argument("--by", help="Agent ID")

    learn_list_parser = learn_subparsers.add_parser("list", help="List recorded lessons")
    learn_list_parser.add_argument("--status", default=None, help="Filter by lifecycle (Active, Archived)")
    learn_list_parser.add_argument("--json", action="store_true", dest="json_output", help="Output as JSON")

    # hydrate / boot command
    hydrate_parser = subparsers.add_parser("hydrate", help="Deterministic boot sequence to align LLM state vector")
    hydrate_parser.add_argument("--dir", help="Target directory (defaults to current directory)")
    hydrate_parser.add_argument("--compact", action="store_true", help="Minimize whitespace for extreme token efficiency")
    hydrate_parser.add_argument("--full", action="store_true", help="Include the full schema, ledger, and delivery state vector")
    hydrate_parser.add_argument("--out", help="Save the hydration vector to a file instead of printing")

    boot_parser = subparsers.add_parser("boot", help="Alias for 'hydrate'")
    boot_parser.add_argument("--dir", help="Target directory (defaults to current directory)")
    boot_parser.add_argument("--compact", action="store_true", help="Minimize whitespace for extreme token efficiency")
    boot_parser.add_argument("--full", action="store_true", help="Include the full schema, ledger, and delivery state vector")
    boot_parser.add_argument("--out", help="Save the hydration vector to a file instead of printing")

    align_parser = broker_subparsers.add_parser("align", help="Authorize semantic alignment")
    align_parser.add_argument("--src_dom", required=True)
    align_parser.add_argument("--tgt_dom", required=True)
    align_parser.add_argument("--src_con", required=True)
    align_parser.add_argument("--tgt_con", required=True)
    align_parser.add_argument("--conf", type=float, default=1.0)
    align_parser.add_argument("--rat", required=True)

    synonym_parser = broker_subparsers.add_parser("synonym", help="Manage persistent synonyms for semantic reconciliation")
    synonym_parser.add_argument("--add", help="Add a synonym pair (format: 'term,synonym')")
    synonym_parser.add_argument("--conf", type=float, default=1.0, help="Confidence score")
    synonym_parser.add_argument("--list", action="store_true", help="List all synonyms")

    export_align_parser = broker_subparsers.add_parser("export-alignments", help="Export alignments to EXMO Align API format")
    export_align_parser.add_argument("--domain", help="Filter by source domain")

    import_align_parser = broker_subparsers.add_parser("import-alignments", help="Import alignments from EXMO Align API XML file")
    import_align_parser.add_argument("--file", required=True, help="Path to XML file")

    negotiate_parser = broker_subparsers.add_parser("negotiate", help="Propose alignments between two Schema files")
    negotiate_parser.add_argument("--src", required=True, help="Source Schema file")
    negotiate_parser.add_argument("--tgt", required=True, help="Target Schema file")
    negotiate_parser.add_argument("--threshold", type=float, default=0.6, help="Confidence threshold")

    negotiate_auto_parser = broker_subparsers.add_parser("negotiate-auto", help="Record high-confidence alignment proposals")
    negotiate_auto_parser.add_argument("--src", required=True, help="Source Schema file")
    negotiate_auto_parser.add_argument("--tgt", required=True, help="Target Schema file")
    negotiate_auto_parser.add_argument("--src_dom", required=True, help="Source Domain Name")
    negotiate_auto_parser.add_argument("--tgt_dom", required=True, help="Target Domain Name")
    negotiate_auto_parser.add_argument("--threshold", type=float, default=0.8, help="Proposal confidence threshold")

    scan_parser = broker_subparsers.add_parser("scan", help="Run Sentinel package vulnerability scanner")
    scan_parser.add_argument("--package", help="Specific package to scan (scans all if omitted)")

    bypass_parser = broker_subparsers.add_parser("bypass", help="Authorize a bypass for a Sentinel vulnerability (Custom Packages Only)")
    bypass_parser.add_argument("--package", required=True, help="Target package name")
    bypass_parser.add_argument("--id", required=True, help="Vulnerability ID (e.g., SNTNL-CUST-001)")
    bypass_parser.add_argument("--rationale", required=True, help="Reason for bypass")

    mesh_parser = broker_subparsers.add_parser("mesh", help="Automate multi-partner ingestion and alignment")
    mesh_parser.add_argument("--data", required=True, help="Directory containing partner CSV/JSON files")
    mesh_parser.add_argument("--threshold", type=float, default=0.8, help="Auto-alignment confidence threshold")

    impact_parser = broker_subparsers.add_parser("impact", help="Change Impact Analysis")
    impact_parser.add_argument("--file", required=True, help="Path to changed file")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
    elif hasattr(args, 'func'):
        args.func(args)
    elif args.command == "init":
        target_dir = args.dir
        if not target_dir:
            confirm = input(f"📂 No --dir provided. Initialize in current directory ({os.getcwd()})? [Y/n]: ").strip().lower()
            if confirm and confirm != 'y':
                print("Aborted.")
                sys.exit(0)
            target_dir = "."
        
        # Safety Check: Does workspace already exist?
        manifest_path = Path(target_dir) / ".etg" / "entigram.yaml"
        if manifest_path.exists() and not args.force:
            confirm = input(f"⚠️  Workspace already exists at {target_dir}. Overwrite configuration? [y/N]: ").strip().lower()
            if confirm != 'y':
                print("Aborted. Use --force to override.")
                sys.exit(0)

        packages = [p.strip() for p in args.packages.split(",")]
        from entigram.injector import inject_entigram_manifest
        success = inject_entigram_manifest(target_dir, packages, args.engine)
        if success:
            print(f"✅ Workspace initialized at {target_dir}")
        else:
            print(f"❌ Failed to initialize workspace.")
            sys.exit(1)

    elif args.command == "config":
        target_dir = args.dir
        manifest_path = Path(target_dir) / ".etg" / "entigram.yaml"
        
        if not manifest_path.exists():
            print(f"❌ No workspace found at {target_dir}. Did you run 'etg init'?")
            sys.exit(1)
            
        try:
            yaml = _load_yaml_module()
            with open(manifest_path, 'r') as f:
                config = yaml.safe_load(f)
        except Exception as e:
            print(f"❌ Failed to read config: {e}")
            sys.exit(1)
            
        if args.list:
            print(f"--- Entigram Workspace Config ({target_dir}) ---")
            for k, v in config.items():
                print(f"{k}: {v}")
            return

        if args.engine:
            config['cli_engine'] = args.engine
            config['last_updated'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            with open(manifest_path, 'w') as f:
                yaml.dump(config, f, default_flow_style=False)
            print(f"✅ Default engine updated to: {args.engine}")
        else:
            print("No changes requested. Use --engine to update settings or --list to view them.")

    elif args.command == "warden":
        from entigram.governance.warden import Warden
        warden = Warden(args.dir)
        if args.warden_command == "lock":
            warden.lock_fingerprint()
        elif args.warden_command == "unlock":
            warden.unlock()
        elif args.warden_command == "check":
            json_output = getattr(args, "json_output", False)
            ok = warden.verify_integrity(emit_human=not json_output)
            if json_output:
                print(json.dumps(warden.halt_event_payload(ok=ok), indent=2, sort_keys=True))
                if not ok:
                    sys.exit(1)
            elif ok:
                print("🛡️  [WARDEN] Schema contracts are intact and verified.")
            else:
                sys.exit(1)
        else:
            warden_parser.print_help()

    elif args.command == "build":
        target_dir = args.dir
        if not target_dir:
            root = find_project_root(os.getcwd())
            if root:
                target_dir = str(root)
            else:
                confirm = input(f"🏗️  No --dir provided and no .etg found. Build from current directory ({os.getcwd()})? [Y/n]: ").strip().lower()
                if confirm and confirm != 'y':
                    print("Aborted.")
                    sys.exit(0)
                target_dir = "."
            
        schema_file = Path(target_dir) / "schema.lds"
        if not schema_file.exists():
            print(f"❌ schema.lds not found in {target_dir}")
            sys.exit(1)
        
        try:
            output = compile_schema_file(str(schema_file), output_format=args.format)
            print(output)
            
            # Lock the Schema Contracts after a successful build
            from entigram.governance.warden import Warden
            Warden(target_dir).lock_fingerprint()
            
        except Exception as e:
            print(f"❌ Build failed: {e}")
            sys.exit(1)
    
    elif args.command == "discover":
        from entigram.schema_compiler.discoverer import discover_source, load_discovery_adapter_module
        try:
            source_path = args.path or args.db
            if not source_path:
                raise ValueError("discover requires --path or --db")
            if args.adapter_module:
                load_discovery_adapter_module(args.adapter_module)
            source = "sqlite" if args.db and not args.path and args.source == "auto" else args.source
            discovery = discover_source(source_path, source=source, domain_name=args.domain)
            schema_content = discovery.to_schema(include_metadata_comment=args.metadata)
            review_summary = _format_discovery_findings(discovery.findings)
            if args.out:
                with open(args.out, 'w') as f:
                    f.write(schema_content)
                print(f"✅ Discovered Schema saved to {args.out}")
                if args.report_json:
                    print(json.dumps(discovery.to_dict(), indent=2, sort_keys=True))
                elif review_summary:
                    print(review_summary)
            elif args.report_json:
                print(json.dumps(discovery.to_dict(), indent=2, sort_keys=True))
            else:
                print(schema_content)
                if review_summary:
                    print(review_summary, file=sys.stderr)
        except Exception as e:
            print(str(e))
            sys.exit(1)

    elif args.command == "merge":
        try:
            sys.exit(_run_merge_command(args))
        except Exception as e:
            print(f"❌ Merge failed: {e}")
            sys.exit(1)
    
    elif args.command == "registry":
        from entigram.registry import EntigramRegistry
        registry = EntigramRegistry(args.dir)
        if args.registry_command == "add":
            if registry.add_registry(args.url):
                print(f"✅ Added registry: {args.url}")
            else:
                sys.exit(1)
        elif args.registry_command == "list":
            regs = registry.get_registries()
            print("Configured Registries:")
            for r in regs:
                print(f" - {r}")
                
    elif args.command == "package":
        if args.pkg_command == "create":
            builder = PackageBuilder()
            pkg_path = builder.create_package(
                name=args.name,
                output_dir=args.out,
                base_skill=args.skill,
                depends_on=args.depends
            )
            print(f"✅ Package '{args.name}' created at {pkg_path}")
        elif args.pkg_command == "import":
            builder = PackageBuilder()
            pkg_path = builder.create_package_from_schema(
                schema_path=args.schema,
                name=args.name,
                output_dir=args.out
            )
            print(f"✅ Package imported at {pkg_path}")
        elif args.pkg_command == "install":
            from entigram.registry import EntigramRegistry
            registry = EntigramRegistry(args.dir)
            if not registry.install_package(args.name):
                sys.exit(1)
        elif args.pkg_command == "suggest":
            from dataclasses import asdict
            from entigram.package_catalog import (
                format_package_suggestions,
                load_package_catalog,
                suggest_packages,
            )
            catalog = load_package_catalog(args.catalog)
            suggestions = suggest_packages(catalog, args.query, limit=args.limit)
            if args.json:
                print(json.dumps([asdict(suggestion) for suggestion in suggestions], indent=2))
            elif suggestions:
                print(format_package_suggestions(suggestions))
            else:
                print("No matching standard packages found.")
        elif args.pkg_command == "audit":
            from dataclasses import asdict
            from entigram.package_catalog import (
                PackageCatalogIssue,
                format_package_catalog_issues,
                load_package_catalog,
                validate_package_catalog,
            )
            from entigram.package_signing import verify_package
            catalog = load_package_catalog(args.catalog)
            issues = validate_package_catalog(catalog)
            signature_results = []
            if args.verify_signatures:
                catalog_path = Path(args.catalog).expanduser().resolve()
                packages_root = Path(args.packages_root).expanduser().resolve() if args.packages_root else catalog_path.parent
                for package in catalog.get("packages", []):
                    package_path = packages_root / package.get("name", "")
                    verification = verify_package(str(package_path), require_signature=package.get("provenance", {}).get("signed") is True)
                    signature_results.append(asdict(verification))
                    for error in verification.errors:
                        issues.append(PackageCatalogIssue(
                            package=package.get("name", "<unknown>"),
                            field="signature",
                            message=error,
                        ))
            if args.json:
                serialized_issues = [{"package": issue.package, "field": issue.field, "message": issue.message} for issue in issues]
                print(json.dumps({"ok": not issues, "issues": serialized_issues, "signatures": signature_results}, indent=2))
            elif issues:
                print(format_package_catalog_issues(issues))
            else:
                print("Package catalog audit passed.")
            if issues:
                sys.exit(1)
        elif args.pkg_command == "manifest":
            from entigram.package_signing import create_package_manifest, write_package_manifest
            metadata = _catalog_metadata_for_package(args.catalog, args.package) if args.catalog else None
            manifest = create_package_manifest(args.package, metadata)
            path = write_package_manifest(args.package, manifest, out=args.out)
            if args.json:
                print(json.dumps(manifest, indent=2, sort_keys=True))
            else:
                print(f"Package manifest written: {path}")
                print(f"SHA-256: {manifest['sha256']}")
        elif args.pkg_command == "sign":
            from entigram.package_signing import create_package_manifest, sign_package_manifest, write_package_manifest
            if not args.no_manifest:
                metadata = _catalog_metadata_for_package(args.catalog, args.package) if args.catalog else None
                write_package_manifest(args.package, create_package_manifest(args.package, metadata))
            signature = sign_package_manifest(args.package, key_path=args.key)
            if args.json:
                print(json.dumps(signature, indent=2, sort_keys=True))
            else:
                print("Package signed.")
                print(f"Key: {signature['signing_key_path']}")
                print(f"Key ID: {signature['key_id']}")
                print(f"Manifest SHA-256: {signature['manifest_sha256']}")
        elif args.pkg_command == "verify":
            from dataclasses import asdict
            from entigram.package_signing import verify_package
            verification = verify_package(args.package, require_signature=not args.no_signature_required)
            if args.json:
                print(json.dumps(asdict(verification), indent=2))
            elif verification.ok:
                print("Package verification passed.")
                if verification.key_id:
                    print(f"Key ID: {verification.key_id}")
            else:
                for error in verification.errors:
                    print(error)
            if not verification.ok:
                sys.exit(1)
        elif args.pkg_command == "sign-catalog":
            from entigram.package_signing import sign_catalog
            signature = sign_catalog(args.catalog, key_path=args.key, out=args.out)
            if args.json:
                print(json.dumps(signature, indent=2, sort_keys=True))
            else:
                print(f"Catalog signed: {signature['signature_path']}")
                print(f"Key: {signature['signing_key_path']}")
                print(f"Key ID: {signature['key_id']}")
                print(f"Catalog SHA-256: {signature['catalog_sha256']}")
        elif args.pkg_command == "verify-catalog":
            from entigram.package_signing import verify_catalog
            verification = verify_catalog(args.catalog, signature_path=args.signature)
            if args.json:
                print(json.dumps(verification, indent=2))
            elif verification["ok"]:
                print("Catalog verification passed.")
                print(f"Key ID: {verification['key_id']}")
            else:
                for error in verification["errors"]:
                    print(error)
            if not verification["ok"]:
                sys.exit(1)
        else:
            pkg_parser.print_help()

    elif args.command == "inject":
        from entigram.sqlite_ledger.injector import DomainSQLiteInjector
        injector = DomainSQLiteInjector(args.dir)
        successful = injector.inject_all_active(enable_crsqlite=args.crsqlite)
        if successful:
            print(f"✅ Successfully injected SQLite databases for: {', '.join(successful)}")
        else:
            print("⚠️  No domain databases injected.")

    elif args.command == "query":
        target_dir = args.dir
        if not target_dir:
            root = find_project_root(os.getcwd())
            target_dir = str(root) if root else os.getcwd()

        from entigram.governance.warden import Warden
        if not Warden(target_dir).verify_integrity():
            sys.exit(1)

        from entigram.federated_router import FederatedRouter
        router = FederatedRouter(target_dir)
        results = router.execute(args.graphql)
        print(json.dumps(results, indent=2))
    elif args.command == "serve":
        target_dir = args.dir
        if not target_dir:
            root = find_project_root(os.getcwd())
            target_dir = str(root) if root else os.getcwd()

        if args.legacy_graphql or args.transport == "graphql":
            from entigram.governance.warden import Warden
            if not Warden(target_dir).verify_integrity():
                sys.exit(1)

            from entigram.server import run_server
            run_server(port=args.port, project_dir=target_dir)
        else:
            from entigram.mcp_server import run_mcp_server
            run_mcp_server(
                target_dir=target_dir,
                transport=args.transport,
                host=args.host,
                port=args.port,
            )

    elif args.command == "panel-bridge":
        from entigram.panel_bridge import run_panel_bridge

        target_dir = args.dir
        if not target_dir or target_dir == ".":
            root = find_project_root(os.getcwd())
            target_dir = str(root) if root else os.getcwd()

        run_panel_bridge(
            host=args.host,
            port=args.port,
            proxy_url=args.proxy_url,
            target_dir=target_dir,
        )

    elif args.command in {"cloudflare-ollama-proxy", "cloudflare-claude"}:
        from entigram.cli_runner.cloudflare_ollama_proxy import main as proxy_main

        proxy_args = [
            "--host",
            args.host,
            "--port",
            str(args.port),
            "--env-file",
            args.env_file,
        ]
        if args.model:
            proxy_args.extend(["--model", args.model])
        if getattr(args, "model_profile", None):
            proxy_args.extend(["--model-profile", args.model_profile])
        if getattr(args, "no_discover_models", False):
            proxy_args.append("--no-discover-models")
        if args.timeout_seconds is not None:
            proxy_args.extend(["--timeout-seconds", str(args.timeout_seconds)])
        if args.retry_attempts is not None:
            proxy_args.extend(["--retry-attempts", str(args.retry_attempts)])
        if args.retry_sleep_seconds is not None:
            proxy_args.extend(["--retry-sleep-seconds", str(args.retry_sleep_seconds)])
        if args.no_compact_prompts:
            proxy_args.append("--no-compact-prompts")
        if args.max_tool_result_chars is not None:
            proxy_args.extend(["--max-tool-result-chars", str(args.max_tool_result_chars)])
        if args.command == "cloudflare-claude":
            proxy_args.append("--launch-claude")
        if getattr(args, "smoke_test", False):
            proxy_args.append("--smoke-test")
        sys.exit(proxy_main(proxy_args))

    elif args.command == "boot" or args.command == "hydrate":
        # Resolve target directory
        if not args.dir:
            root = find_project_root(os.getcwd())
            target_path = Path(root) if root else Path.cwd()
        else:
            target_path = Path(args.dir).expanduser().resolve()

        vector = get_hydration_vector(
            target_path,
            compact=getattr(args, "compact", False),
            full=getattr(args, "full", False),
        )
        
        if getattr(args, "out", None):
            with open(args.out, "w") as f:
                f.write(vector)
            print(f"✅ Hydration vector saved to {args.out}")
        else:
            print(vector)

    elif args.command == "ui":
        if not launch_ui(args.dir):
            sys.exit(1)

    elif args.command == "agent-instructions":
        print(_agent_instructions_text())

    elif args.command == "agent":
        if getattr(args, "agent_action", None) == "instructions":
            print(_agent_instructions_text())
            return

        if getattr(args, "agent_action", None) == "start":
            target_dir = _resolve_workspace_dir(getattr(args, "dir", None))
            print(_agent_start_text(target_dir))
            return

        target_dir = args.dir
        if not target_dir:
            root = find_project_root(os.getcwd())
            if root:
                target_dir = str(root)
            else:
                confirm = input(f"🤖 No --dir provided and no .etg found. Launch agent in current directory ({os.getcwd()})? [Y/n]: ").strip().lower()
                if confirm and confirm != 'y':
                    print("Aborted.")
                    sys.exit(0)
                target_dir = "."
        
        # Verify Integrity: Prevent agent from starting if schema contracts have been tampered with
        from entigram.governance.warden import Warden
        if not Warden(target_dir).verify_integrity():
            sys.exit(1)
        
        # Load engine from manifest
        engine = get_default_engine()
        manifest_path = Path(target_dir) / ".etg" / "entigram.yaml"
        if manifest_path.exists():
            try:
                yaml = _load_yaml_module()
                with open(manifest_path, 'r') as f:
                    manifest = yaml.safe_load(f)
                    engine = manifest.get('cli_engine', engine)
            except: pass
            
        # Override if provided
        if args.engine:
            engine = args.engine

        print(f"🤖 Launching AI Agent ({engine})...")
        
        # ACTIVE HYDRATION: Force the LLM state vector to align with the local ontology
        # OPTIMIZATION: Use a temporary boot file for long vectors
        hydration_vector = get_hydration_vector(Path(target_dir), compact=True, full=True)
        
        boot_file = Path(target_dir) / ".etg" / "boot.json"
        with open(boot_file, "w") as f:
            f.write(hydration_vector)
            
        launch_agent(
            target_dir,
            engine,
            model=getattr(args, "model", None),
            ollama_launch_option=getattr(args, "ollama_launch_option", None),
            initial_prompt=f"Initialize from {boot_file}. Silent boot. Ready.",
            headless=args.headless,
        )

    elif args.command == "interview":
        target_dir = args.dir
        if not target_dir:
            root = find_project_root(os.getcwd())
            if root:
                target_dir = str(root)
            else:
                confirm = input(f"🎤 No --dir provided and no .etg found. Start interview in current directory ({os.getcwd()})? [Y/n]: ").strip().lower()
                if confirm and confirm != 'y':
                    print("Aborted.")
                    sys.exit(0)
                target_dir = "."

        # 1. Verify workspace is initialized
        if not (Path(target_dir) / ".etg" / "entigram.yaml").exists():
            print(f"❌ Error: Workspace not initialized in {target_dir}. Run 'etg init' first.")
            sys.exit(1)
            
        # 2. Load engine from manifest
        engine = get_default_engine()
        manifest_path = Path(target_dir) / ".etg" / "entigram.yaml"
        if manifest_path.exists():
            try:
                yaml = _load_yaml_module()
                with open(manifest_path, 'r') as f:
                    manifest = yaml.safe_load(f)
                    engine = manifest.get('cli_engine', engine)
            except: pass
            
        # Override if provided
        if args.engine:
            engine = args.engine

        print(f"🎤 Starting Autonomous Modeler Interview ({engine})...")

        # ACTIVE HYDRATION: Force the LLM state vector to align with the local ontology
        # OPTIMIZATION: Use a temporary boot file for long vectors
        hydration_vector = get_hydration_vector(Path(target_dir), compact=True, full=True)
        
        boot_file = Path(target_dir) / ".etg" / "boot.json"
        with open(boot_file, "w") as f:
            f.write(hydration_vector)
            
        launch_agent(
            target_dir,
            engine,
            model=getattr(args, "model", None),
            ollama_launch_option=getattr(args, "ollama_launch_option", None),
            initial_prompt=f"Initialize from {boot_file}. Silent boot. Ready.",
            headless=args.headless,
        )

    elif args.command == "model":
        target_dir = args.dir
        if not target_dir:
            root = find_project_root(os.getcwd())
            target_dir = str(root) if root else os.getcwd()

        # 1. Load engine
        engine = get_default_engine()
        manifest_path = Path(target_dir) / ".etg" / "entigram.yaml"
        if manifest_path.exists():
            try:
                yaml = _load_yaml_module()
                with open(manifest_path, 'r') as f:
                    manifest = yaml.safe_load(f)
                    engine = manifest.get('cli_engine', engine)
            except: pass
        if args.engine: engine = args.engine

        # 2. Construct Prompt
        # ACTIVE HYDRATION: Force the LLM state vector to align with the local ontology
        # OPTIMIZATION: Use a temporary boot file for long vectors
        hydration_vector = get_hydration_vector(Path(target_dir), compact=True, full=True)
        boot_file = Path(target_dir) / ".etg" / "boot.json"
        with open(boot_file, "w") as f:
            f.write(hydration_vector)

        # We instruct the agent to return ONLY the Schema block.
        system_prompt = f"""Initialize from {boot_file}. Silent boot.

You are the Entigram Domain Modeler. Your goal is to translate a natural language description into a strict Entigram Schema.
Return ONLY the ENTITY and RELATIONSHIP blocks. Do not include any conversational filler.
Format:
ENTITY: Name
ATTRIBUTES:
  - .id (UUID)
  - attribute_name (Type)

RELATIONSHIPS:
- EntityA (1) [MUST] --- [MAY] (MANY) EntityB
"""
        full_prompt = f"{system_prompt}\n\nDOMAIN DESCRIPTION: {args.description}\n\nSCHEMA PAYLOAD:"

        # 3. Headless Execution (Intercepting the Payload)
        print(f"🧠 Intercepting domain payload from {engine}...")
        from entigram.cli_runner.runner import execute_headless_agy

        # 4. Validation & Enforcement
        from entigram.schema_compiler.parser import SchemaParser
        repair_limit = 0 if getattr(args, "no_auto_repair", False) else max(0, args.max_repair_attempts)
        prompt = full_prompt
        payload = ""
        entities = {}
        rels = []
        halt_event = None

        for attempt in range(repair_limit + 1):
            if attempt:
                print(f"🔁 Gate retry {attempt}/{repair_limit}: sending HaltEvent feedback to {engine}...")

            payload = execute_headless_agy(prompt, target_dir=target_dir)

            if not payload:
                halt_event = _schema_payload_halt_event(
                    "EMPTY_PAYLOAD",
                    "The model returned an empty schema payload.",
                    payload,
                )
            else:
                print("\n--- INTERCEPTED PAYLOAD ---")
                print(payload)
                print("---------------------------\n")
                try:
                    parser = SchemaParser(payload)
                    entities, rels = parser.parse()
                    if not entities:
                        halt_event = _schema_payload_halt_event(
                            "NO_ENTITIES",
                            "No ENTITY blocks were detected in the schema payload.",
                            payload,
                        )
                    else:
                        halt_event = None
                        break
                except Exception as e:
                    halt_event = _schema_payload_halt_event(
                        "INVALID_LDS",
                        "The schema payload could not be parsed as valid LDS.",
                        payload,
                        error=e,
                    )

            if attempt >= repair_limit:
                print("❌ Max Gate feedback retries exhausted.")
                print(json.dumps({"ok": False, "halt_event": halt_event}, indent=2, sort_keys=True))
                sys.exit(1)

            prompt = _model_repair_prompt(
                full_prompt,
                halt_event,
                retry_number=attempt + 1,
                retry_limit=repair_limit,
            )

        print(f"✅ Validated: {len(entities)} entities and {len(rels)} relationships found.")

        if args.append:
            draft_path = Path(target_dir) / "draft_schema.lds"
            with open(draft_path, "a") as f:
                f.write(f"\n\n/* Autonomous model added on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} */\n")
                f.write(payload)
            print(f"📝 Appended to {draft_path}")

            # Auto-sync ontology
            from entigram.broker import EntigramBroker
            EntigramBroker(target_dir).sync_all_ontologies()
            print("🛡️  Ontology synchronized.")

    elif args.command == "cloud":
        if args.cloud_command == "login":
            creds_dir = Path.home() / ".etg"
            creds_dir.mkdir(parents=True, exist_ok=True)
            (creds_dir / "credentials").write_text(f"token: {args.token}")
            print("✅ Successfully logged into Entigram Cloud.")
        elif args.cloud_command == "sync":
            from entigram.broker import EntigramBroker
            broker = EntigramBroker(args.dir)
            
            token = "anonymous"
            creds_file = Path.home() / ".etg" / "credentials"
            if creds_file.exists():
                try:
                    token = creds_file.read_text().split("token: ")[1].strip()
                except: pass
            
            broker.ledger.sync_with_cloud(args.endpoint, token)

    elif args.command == "broker":
        from entigram.broker import EntigramBroker
        broker = EntigramBroker(args.dir)
        
        if args.broker_command == "decide":
            if getattr(args, "json_output", False):
                from contextlib import redirect_stdout
                from io import StringIO
                with redirect_stdout(StringIO()):
                    success = broker.propose_resolution(args.id, args.type, args.state, args.rationale)
                if success:
                    print(json.dumps({"ok": True, "status": "recorded", "conflict_id": args.id}, indent=2))
                else:
                    print(json.dumps(
                        _halt_response(
                            broker.warden,
                            "DECISION_REJECTED",
                            "Broker rejected the proposed decision.",
                            conflict_id=args.id,
                            entity_type=args.type,
                        ),
                        indent=2,
                        sort_keys=True,
                    ))
                    sys.exit(1)
            elif broker.propose_resolution(args.id, args.type, args.state, args.rationale):
                print(f"✅ Decision '{args.id}' recorded.")
            else:
                sys.exit(1)
        elif args.broker_command == "conflict":
            try:
                states = json.loads(args.states)
            except json.JSONDecodeError as exc:
                if getattr(args, "json_output", False):
                    print(json.dumps({
                        "ok": False,
                        "halt_event": {
                            "halt_code": "INVALID_JSON",
                            "message": "Proposed states must be valid JSON.",
                            "expected_schema": {"states": "JSON object"},
                            "actual_payload": {"states": args.states},
                            "suggested_fix": "Pass --states as a JSON object keyed by agent id.",
                            "details": {"error": str(exc)},
                        },
                    }, indent=2, sort_keys=True))
                else:
                    print(f"❌ Invalid --states JSON: {exc}")
                sys.exit(1)
            if getattr(args, "json_output", False):
                from contextlib import redirect_stdout
                from io import StringIO
                with redirect_stdout(StringIO()):
                    result = broker.report_conflict(args.id, args.type, states, args.agent)
            else:
                result = broker.report_conflict(args.id, args.type, states, args.agent)
            if result:
                # The broker might have auto-resolved it
                decision = broker.check_decision(args.id)
                if getattr(args, "json_output", False):
                    print(json.dumps({
                        "ok": True,
                        "status": "auto_resolved" if decision and "Auto-resolved" in decision.get('rationale', '') else "logged",
                        "conflict_id": args.id,
                    }, indent=2, sort_keys=True))
                elif decision and "Auto-resolved" in decision.get('rationale', ''):
                    print(f"✅ Conflict '{args.id}' was auto-resolved by Policy.")
                else:
                    print(f"✅ Conflict '{args.id}' reported and logged for review.")
            else:
                if getattr(args, "json_output", False):
                    print(json.dumps(
                        _halt_response(
                            broker.warden,
                            "CONFLICT_REJECTED",
                            "Broker rejected the reported conflict.",
                            conflict_id=args.id,
                            entity_type=args.type,
                        ),
                        indent=2,
                        sort_keys=True,
                    ))
                sys.exit(1)
        elif args.broker_command == "check":
            decision = broker.check_decision(args.id)
            if decision:
                print(f"FOUND: {decision['state']} (Rationale: {decision['rationale']})")
            else:
                print("NOT_FOUND")
        elif args.broker_command == "conflicts":
            conflicts = broker.ledger.get_pending_conflicts()
            if not conflicts:
                print("No pending conflicts.")
            for c in conflicts:
                print(f"[{c['timestamp']}] ID: {c['conflict_id']} | Type: {c['entity_type']} | Agents: {c['source_agents']}")
                print(f"  Proposed States: {c['proposed_states']}")
        elif args.broker_command == "resolutions":
            resolutions = broker.ledger.get_all_resolutions()
            if not resolutions:
                print("No resolutions found.")
            for r in resolutions:
                print(f"[{r['timestamp']}] ID: {r['conflict_id']} (v{r['version']}) | State: {r['state']}")
                print(f"  Rationale: {r['rationale']}")
        elif args.broker_command == "validate":
            result = broker.validate_model()
            if result['valid']:
                e_warns = result.get("expectation_warnings", [])
                print(f"✅ Model valid: {result['entity_count']} entities, {result['relationship_count']} rels.")
                if e_warns:
                    print(f"⚠️  {len(e_warns)} expectation-to-test mapping warning(s):")
                    for w in e_warns:
                        print(f"   [{w['code']}] {w['message']}")
                elif result.get("expectation_count", 0) == 0 or not result.get("expectation_warnings"):
                    print("   ✅ All modeled expectations have resolvable validation_check paths.")
            else:
                print(f"❌ Model invalid: {result['error']}")
                sys.exit(1)
        elif args.broker_command == "agent-register":
            try:
                ok = broker.ledger.record_agent(
                    args.agent,
                    agent_class=getattr(args, "agent_class", None),
                    provider=getattr(args, "provider", None),
                    model=getattr(args, "model", None),
                    reliability_score=getattr(args, "score", 0.5),
                    capability_scores=_parse_score_pairs(getattr(args, "capability", [])),
                    allowed_task_classes=_split_classes(getattr(args, "allow", [])),
                    restricted_task_classes=_split_classes(getattr(args, "restrict", [])),
                    last_workspace_seen=str(Path(args.dir).expanduser().resolve()),
                    notes=getattr(args, "notes", None),
                )
            except ValueError as exc:
                print(str(exc))
                sys.exit(1)
            agent = broker.ledger.get_agent(args.agent)
            if getattr(args, "json_output", False):
                print(json.dumps({"ok": ok, "agent": agent}, indent=2, sort_keys=True))
            elif ok:
                print(f"✅ Agent registered: {args.agent} (score {agent['reliability_score']:.2f})")
            else:
                sys.exit(1)
        elif args.broker_command == "agent-list":
            agents = broker.ledger.get_agents()
            if getattr(args, "json_output", False):
                print(json.dumps(agents, indent=2, sort_keys=True))
            elif not agents:
                print("No agents registered.")
            else:
                for agent in agents:
                    print(
                        f"{agent['agent_id']} | score={agent['reliability_score']:.2f} "
                        f"| class={agent.get('agent_class') or '-'} | model={agent.get('model') or '-'}"
                    )
        elif args.broker_command == "task-enqueue":
            try:
                details = _parse_json_arg(getattr(args, "details", None), default={})
                ok = broker.ledger.enqueue_agent_task(
                    args.id,
                    args.title,
                    args.task_type,
                    risk_level=args.risk,
                    required_score=getattr(args, "required_score", None),
                    details=details,
                )
            except ValueError as exc:
                print(str(exc))
                sys.exit(1)
            task = broker.ledger.get_agent_task(args.id)
            if getattr(args, "json_output", False):
                print(json.dumps({"ok": ok, "task": task}, indent=2, sort_keys=True))
            elif ok:
                print(
                    f"✅ Task queued: {args.id} "
                    f"({task['risk_level']}, requires {task['required_score']:.2f})"
                )
            else:
                sys.exit(1)
        elif args.broker_command == "task-assign":
            result = broker.ledger.assign_agent_task(args.id, args.agent)
            if getattr(args, "json_output", False):
                print(json.dumps(result, indent=2, sort_keys=True))
            elif result.get("ok"):
                print(f"✅ Assigned {args.id} to {args.agent}: {result['rationale']}")
            else:
                if result.get("serious_conflict"):
                    print("⚠️  Serious assignment conflict recorded for human resolution.")
                print(f"❌ Assignment rejected: {result.get('rationale') or result.get('reason')}")
            if not result.get("ok"):
                sys.exit(1)
        elif args.broker_command == "task-list":
            tasks = broker.ledger.get_agent_tasks(status=getattr(args, "status", None))
            if getattr(args, "json_output", False):
                print(json.dumps(tasks, indent=2, sort_keys=True))
            elif not tasks:
                print("No agent tasks found.")
            else:
                for task in tasks:
                    owner = task.get("assigned_agent_id") or "-"
                    print(
                        f"{task['task_id']} | {task['status']} | {task['risk_level']} "
                        f"| requires={task['required_score']:.2f} | agent={owner} | {task['title']}"
                    )
        elif args.broker_command == "hibernate":
            plan = broker.ledger.record_agent_hibernation(
                args.agent,
                run_id=getattr(args, "run_id", None),
                token_threshold=getattr(args, "token_threshold", None),
                remaining_tokens=getattr(args, "remaining_tokens", None),
                refresh_window_end=getattr(args, "refresh_window_end", None),
                resume_after=getattr(args, "resume_after", None),
                checkpoint_summary=args.summary,
                next_action=getattr(args, "next_action", None),
                pending_task_ids=getattr(args, "pending_task", []),
            )
            if getattr(args, "json_output", False):
                print(json.dumps({"ok": True, "hibernate": plan}, indent=2, sort_keys=True))
            else:
                print(f"💤 Hibernation checkpoint recorded: {plan['hibernate_id']}")
                print(f"   Resume after: {plan.get('resume_after') or 'external scheduler decides'}")
                print(f"   Next action: {plan.get('next_action')}")
        elif args.broker_command == "resume":
            plan = broker.ledger.get_resume_plan(agent_id=getattr(args, "agent", None))
            if plan and getattr(args, "mark_resumed", False):
                broker.ledger.mark_hibernation_resumed(plan["hibernate_id"])
                plan["status"] = "Resumed"
            if getattr(args, "json_output", False):
                print(json.dumps({"ok": bool(plan), "resume": plan}, indent=2, sort_keys=True))
            elif not plan:
                print("No hibernated checkpoint is ready to resume.")
            else:
                print(f"🔁 Resume checkpoint: {plan['hibernate_id']} ({plan['agent_id']})")
                print(f"   Summary: {plan.get('checkpoint_summary')}")
                print(f"   Next action: {plan.get('next_action')}")
                pending = ", ".join(plan.get("pending_task_ids") or [])
                if pending:
                    print(f"   Pending tasks: {pending}")
        elif args.broker_command == "preflight":
            impact = broker.analyze_impact(args.file)
            preflight = _build_agent_preflight(args.dir, args.file, impact)
            if getattr(args, "json_output", False):
                print(json.dumps(preflight, indent=2, sort_keys=True))
            else:
                print(_format_agent_preflight(preflight))
        elif args.broker_command == "impact":
            impact = broker.analyze_impact(args.file)
            print(json.dumps(impact, indent=2))
        elif args.broker_command in ("commission", "commissioner"):
            result = broker.commission(
                proofs=args.proof,
                blocked_checks=getattr(args, "blocked", []),
                agent_id=getattr(args, "agent", None),
            )
            if args.json_output:
                print(json.dumps(result, indent=2))
            else:
                print(broker.format_commission(result))
            if not result["valid"]:
                sys.exit(1)
        elif args.broker_command in ("guard", "verify"):
            result = broker.expectation_guard(
                proofs=getattr(args, "proof", []),
                blocked_checks=getattr(args, "blocked", []),
                agent_id=getattr(args, "agent", None),
                expectation_name=getattr(args, "expectation", None),
                run_validation_checks=not getattr(args, "no_run", False),
                timeout=getattr(args, "timeout", 120),
            )
            if args.json_output:
                print(json.dumps(result, indent=2))
            else:
                print(broker.format_expectation_guard(result))
            if not result["valid"]:
                sys.exit(1)
        elif args.broker_command == "deliver":
            result = broker.commission_and_record(
                proofs=args.proof,
                blocked_checks=getattr(args, "blocked", []),
                agent_id=getattr(args, "agent", None),
                expectation_name=getattr(args, "expectation", None),
                artifact_paths=getattr(args, "artifact", []),
                artifact_role=getattr(args, "artifact_role", "delivery_artifact"),
            )
            if args.json_output:
                print(json.dumps(result, indent=2))
            else:
                print(broker.format_commission(result))
                if result["valid"]:
                    snap = result.get("snapshot_id", "unknown")
                    ts = result.get("trust_score", {})
                    grade = ts.get("grade", "?")
                    score = ts.get("score", 0.0)
                    print(f"\n📦 Delivery snapshot: {snap}")
                    print(f"🎯 Trust score: {score:.0%} (Grade {grade})")
                    bkd = ts.get("breakdown", {})
                    for k, v in bkd.items():
                        if isinstance(v, (int, float)):
                            print(f"   {k}: {v:.0%}")
                        else:
                            print(f"   {k}: {v}")
                    print("✅ Handoff gate: PASSED. This delivery is anchored.")
                else:
                    ts = result.get("trust_score", {})
                    if ts:
                        print(f"\n🎯 Trust score: {ts.get('score', 0):.0%} (Grade {ts.get('grade', 'F')})")
                    print("\n❌ Handoff gate: FAILED. Resolve missing proofs before delivering.")
            if not result["valid"]:
                sys.exit(1)
        elif args.broker_command in ("status", "diff"):
            result = broker.delivery_status(
                artifact_paths=getattr(args, "artifact", []),
                artifact_role=getattr(args, "artifact_role", "delivery_artifact"),
            )
            if args.json_output:
                print(json.dumps(result, indent=2))
            else:
                print(broker.format_delivery_status(result))
            if result.get("needs_recommission"):
                sys.exit(1)
        elif args.broker_command == "export-audit":
            bundle = broker.export_audit_bundle(
                out_path=getattr(args, "out", None),
                signing_key_path=getattr(args, "signing_key", None),
            )
            if args.json_output:
                print(json.dumps(bundle, indent=2, sort_keys=True))
            else:
                if bundle.get("path"):
                    print(f"📦 Audit bundle: {bundle['path']}")
                print(f"🔏 SHA-256: {bundle['sha256']}")
                sig = bundle.get("signature", {})
                print(f"Signature: {sig.get('type', 'unknown')} ({sig.get('key_id', 'unknown')})")
                print(f"Signing key: {bundle.get('signing_key_path', 'unknown')}")
        elif args.broker_command == "resolve":
            # Run missing proof commands and record outcomes
            checklist = broker.commission()
            if checklist["valid"]:
                print("✅ Commissioner: all expectations already have proof. Nothing to resolve.")
            else:
                missing = [
                    item for item in checklist["items"]
                    if item["status"] not in ("passed", "blocked")
                ]
                agent_id = getattr(args, "agent", None)
                if getattr(args, "run_missing_proofs", False):
                    import subprocess
                    import shlex
                    results = []
                    for item in missing:
                        cmd = item.get("validation_check", "")
                        if not cmd:
                            continue
                        print(f"▶ Running: {cmd}")
                        try:
                            try:
                                cmd_args = shlex.split(cmd)
                            except ValueError as e:
                                broker.ledger.record_delivery_evidence(
                                    evidence_type="test_run",
                                    artifact_ref=cmd,
                                    expectation_name=item["name"],
                                    command=cmd,
                                    result_summary=f"Invalid validation command: {e}",
                                    passed=False,
                                    agent_id=agent_id,
                                )
                                results.append({"name": item["name"], "passed": False, "command": cmd})
                                print(f"  ❌ INVALID COMMAND: {item['name']} ({e})")
                                continue
                            if not cmd_args:
                                continue
                            proc = subprocess.run(
                                cmd_args,
                                capture_output=True,
                                text=True,
                                timeout=getattr(args, "timeout", 120),
                                cwd=str(broker.target_dir),
                            )
                            passed = proc.returncode == 0
                            summary = (proc.stdout + proc.stderr).strip()[:500]
                            broker.ledger.record_delivery_evidence(
                                evidence_type="test_run",
                                artifact_ref=cmd,
                                expectation_name=item["name"],
                                command=cmd,
                                result_summary=summary,
                                passed=passed,
                                agent_id=agent_id,
                            )
                            status = "✅ PASS" if passed else "❌ FAIL"
                            print(f"  {status}: {item['name']}")
                            results.append({"name": item["name"], "passed": passed, "command": cmd})
                        except FileNotFoundError as e:
                            print(f"  ❌ COMMAND NOT FOUND: {item['name']} ({cmd})")
                            broker.ledger.record_delivery_evidence(
                                evidence_type="test_run",
                                artifact_ref=cmd,
                                expectation_name=item["name"],
                                command=cmd,
                                result_summary=str(e),
                                passed=False,
                                agent_id=agent_id,
                            )
                            results.append({"name": item["name"], "passed": False, "command": cmd})
                        except subprocess.TimeoutExpired:
                            print(f"  ⏰ TIMEOUT: {item['name']} ({cmd})")
                            broker.ledger.record_delivery_evidence(
                                evidence_type="test_run",
                                artifact_ref=cmd,
                                expectation_name=item["name"],
                                command=cmd,
                                result_summary=f"Timeout after {getattr(args, 'timeout', 120)}s",
                                passed=False,
                                agent_id=agent_id,
                            )
                            results.append({"name": item["name"], "passed": False, "command": cmd})
                    all_passed = all(r["passed"] for r in results) if results else False
                    if all_passed:
                        print("\n✅ All missing proofs resolved. Run 'etg broker deliver' to anchor this delivery.")
                    else:
                        print("\n⚠️  Some proofs failed. Address failures before delivering.")
                        if args.json_output:
                            print(json.dumps(results, indent=2))
                        sys.exit(1)
                else:
                    print(f"Commissioner: {len(missing)} expectation(s) need proof:")
                    for item in missing:
                        print(f"  TODO {item['name']}: {item['validation_check']}")
                    print("\nRun with --run-missing-proofs to execute and record them.")
                    sys.exit(1)
        elif args.broker_command == "handoff":
            from entigram.governance.warden import Warden
            json_output = getattr(args, "json_output", False)
            report = {}

            if not json_output:
                print("Step 1/4: broker guard")
            guard_result = broker.expectation_guard(
                proofs=getattr(args, "proof", []),
                blocked_checks=getattr(args, "blocked", []),
                agent_id=getattr(args, "agent", None),
            )
            report["guard"] = guard_result
            if not json_output:
                print(broker.format_expectation_guard(guard_result))
            if not guard_result["valid"]:
                if json_output:
                    print(json.dumps(report, indent=2, sort_keys=True))
                sys.exit(1)

            if not json_output:
                print("\nStep 2/4: warden lock")
                Warden(args.dir).lock_fingerprint()
            else:
                from contextlib import redirect_stdout
                from io import StringIO
                with redirect_stdout(StringIO()):
                    Warden(args.dir).lock_fingerprint()
            report["warden_lock"] = {"ok": True}

            if not json_output:
                print("\nStep 3/4: broker deliver")
            deliver_result = broker.commission_and_record(
                proofs=getattr(args, "proof", []),
                blocked_checks=getattr(args, "blocked", []),
                agent_id=getattr(args, "agent", None),
                artifact_paths=getattr(args, "artifact", []),
                artifact_role=getattr(args, "artifact_role", "delivery_artifact"),
            )
            report["deliver"] = deliver_result
            if not json_output:
                print(broker.format_commission(deliver_result))
                if deliver_result["valid"]:
                    snap = deliver_result.get("snapshot_id", "unknown")
                    ts = deliver_result.get("trust_score", {})
                    print(f"\nDelivery snapshot: {snap}")
                    print(f"Trust score: {ts.get('score', 0.0):.0%} (Grade {ts.get('grade', '?')})")
                else:
                    print("\nHandoff gate: FAILED. Resolve missing proofs before delivering.")
            if not deliver_result["valid"]:
                if json_output:
                    print(json.dumps(report, indent=2, sort_keys=True))
                sys.exit(1)

            if not json_output:
                print("\nStep 4/4: broker status")
            status_result = broker.delivery_status(
                artifact_paths=getattr(args, "artifact", []),
                artifact_role=getattr(args, "artifact_role", "delivery_artifact"),
            )
            report["status"] = status_result
            if json_output:
                print(json.dumps(report, indent=2, sort_keys=True))
            else:
                print(broker.format_delivery_status(status_result))
            if status_result.get("needs_recommission"):
                sys.exit(1)
        elif args.broker_command == "add-package":
            if broker.add_package(args.name):
                print(f"✅ Package '{args.name}' added to manifest.")
            else:
                sys.exit(1)
        elif args.broker_command == "sense":
            conflicts = broker.sense_all()
            if not conflicts:
                print("✅ No cross-domain contradictions detected.")
            else:
                print(f"⚠️  Detected {len(conflicts)} contradictions:")
                for c in conflicts:
                    print(f" - {c['id']}: {c['rationale']}")
                    print(f"   States: {c['proposed_states']}")
        elif args.broker_command == "sync":
            broker.sync_resolutions()
            print("✅ Domain states synchronized with human resolutions.")
        elif args.broker_command == "align":
            if broker.authorize_alignment(args.src_dom, args.tgt_dom, args.src_con, args.tgt_con, args.conf, args.rat):
                print(f"✅ Alignment Authorized.")
            else:
                sys.exit(1)
        elif args.broker_command == "synonym":
            if args.add:
                try:
                    term, synonym = args.add.split(",")
                    if broker.ledger.record_synonym(term, synonym, args.conf):
                        print(f"✅ Synonym added: {term} <-> {synonym} ({args.conf})")
                    else:
                        sys.exit(1)
                except ValueError:
                    print("❌ Invalid format for --add. Use 'term,synonym'.")
                    sys.exit(1)
            elif args.list:
                syns = broker.ledger.get_synonyms()
                if not syns:
                    print("No synonyms recorded.")
                else:
                    print("Global Synonym Ledger:")
                    for s in syns:
                        print(f" - {s['term']} <-> {s['synonym']} ({s['confidence']})")
            else:
                synonym_parser.print_help()
        elif args.broker_command == "export-alignments":
            output = broker.export_alignments(args.domain)
            if output:
                print(output)
            else:
                print("No alignments found.")
        elif args.broker_command == "import-alignments":
            count = broker.import_alignments(args.file)
            print(f"✅ Successfully imported and authorized {count} alignments from {args.file}")
        elif args.broker_command == "negotiate":
            proposals = broker.negotiate_alignments(args.src, args.tgt, args.threshold)
            if not proposals:
                print("No alignment proposals found above threshold.")
            else:
                print(f"Found {len(proposals)} alignment proposals:")
                for p in proposals:
                    print(f" - [{p['confidence']}] {p['source_concept']} <-> {p['target_concept']}")
                    print(f"   Rationale: {p['rationale']}")
        elif args.broker_command == "negotiate-auto":
            proposals = broker.negotiate_alignments(args.src, args.tgt, args.threshold)
            count = 0
            for p in proposals:
                if broker.propose_alignment(
                    source_domain=args.src_dom,
                    target_domain=args.tgt_dom,
                    source_concept=p['source_concept'],
                    target_concept=p['target_concept'],
                    confidence=p['confidence'],
                    rationale=f"Negotiated proposal: {p['rationale']}",
                    source_artifact=f"{args.src}::{args.tgt}",
                ):
                    print(f"✅ Proposed: {p['source_concept']} <-> {p['target_concept']} ({p['confidence']})")
                    count += 1
            print(f"\n✅ Recorded {count} alignment proposals between {args.src_dom} and {args.tgt_dom}.")
        elif args.broker_command == "mesh":
            from entigram.sensing.partner_mesh import PartnerMesh
            mesh = PartnerMesh(args.dir)
            results = mesh.mesh_directory(args.data, auto_align_threshold=args.threshold)
            print(json.dumps(results, indent=2))
        elif args.broker_command == "scan":
            from entigram.governance.sentinel import SentinelScanner
            scanner = SentinelScanner(args.dir)
            if args.package:
                results = {args.package: scanner.scan_package(args.package)}
            else:
                results = scanner.scan_all()
            
            if "status" in results and results["status"] == "no_packages_found":
                print("No packages found to scan.")
            else:
                total_vulns = 0
                for pkg, result in results.items():
                    vulns = result.get("vulnerabilities", [])
                    total_vulns += len(vulns)
                    if vulns:
                        pkg_type = "Standard" if result["is_standard"] else "Custom"
                        print(f"\n🛡️  Sentinel Scan: {pkg} ({pkg_type} Package)")
                        for v in vulns:
                            print(f"  [{v['severity']}] {v['id']}: {v['description']}")
                
                if total_vulns == 0:
                    print("✅ Sentinel Scan Complete: No active vulnerabilities found.")
                else:
                    print(f"\n⚠️  Sentinel Scan Complete: Found {total_vulns} active vulnerabilities.")
                    print("   To bypass custom package warnings, use: broker bypass --package <pkg> --id <id> --rationale \"<reason>\"")
        
        elif args.broker_command == "bypass":
            from entigram.governance.sentinel import SentinelScanner
            scanner = SentinelScanner(args.dir)
            if not scanner.authorize_bypass(args.package, args.id, args.rationale):
                sys.exit(1)
    
    elif args.command == "improve":
        from entigram.broker import EntigramBroker
        broker = EntigramBroker(args.dir)
        if args.improve_command == "propose":
            try:
                import json as _json
                change = _json.loads(args.change)
            except (ValueError, TypeError):
                change = {"description": args.change}
            proposal_id = broker.record_improvement_proposal(
                title=args.title,
                affected_model=args.model,
                proposed_change=change,
                rationale=args.rationale,
                expected_benefit=getattr(args, "benefit", None),
                created_by=getattr(args, "by", None),
                lifecycle_status=getattr(args, "status", "Proposed"),
            )
            if proposal_id:
                status_label = getattr(args, "status", "Proposed")
                print(f"✅ Improvement proposal #{proposal_id} recorded: '{args.title}'")
                print(f"   Affected model: {args.model}")
                print(f"   Lifecycle: {status_label}")
                if status_label == "Proposed":
                    print("   Advance with: etg improve list --status Proposed")
            else:
                print("❌ Failed to record proposal.")
                sys.exit(1)
        elif args.improve_command == "list":
            proposals = broker.ledger.get_improvement_proposals(
                lifecycle_status=getattr(args, "status", None)
            )
            if not proposals:
                status_filter = getattr(args, "status", None)
                label = f" (status: {status_filter})" if status_filter else ""
                print(f"No improvement proposals found{label}.")
            elif getattr(args, "json_output", False):
                print(json.dumps(proposals, indent=2))
            else:
                print(f"Improvement Proposals ({len(proposals)} found):")
                for p in proposals:
                    icon = {"Proposed": "🔵", "Reviewed": "🟡", "Implemented": "🟢", "Rejected": "🔴"}.get(
                        p["lifecycle_status"], "⚪"
                    )
                    print(f"  {icon} [{p['lifecycle_status']}] #{p['id']} {p['title']}")
                    print(f"    Model: {p['affected_model']}")
                    print(f"    Rationale: {p['rationale']}")
                    if p.get("expected_benefit"):
                        print(f"    Benefit: {p['expected_benefit']}")
                    print(f"    Created: {p['created_at']} by {p.get('created_by', 'unknown')}")
        else:
            improve_parser.print_help()

    elif args.command == "learn":
        from entigram.broker import EntigramBroker
        broker = EntigramBroker(args.dir)
        if args.learn_command == "record":
            lesson_id = broker.record_lesson(
                lesson=args.lesson,
                source_task=getattr(args, "task", None),
                reusable_rule=getattr(args, "rule", None),
                confidence=getattr(args, "confidence", 1.0),
                agent_id=getattr(args, "by", None),
            )
            if lesson_id:
                print(f"✅ Lesson #{lesson_id} recorded.")
                if getattr(args, "rule", None):
                    print(f"   Rule: {args.rule}")
                print("   View with: etg learn list")
            else:
                print("❌ Failed to record lesson.")
                sys.exit(1)
        elif args.learn_command == "list":
            lessons = broker.ledger.get_lessons(
                lifecycle_status=getattr(args, "status", None)
            )
            if not lessons:
                print("No lessons recorded yet.")
            elif getattr(args, "json_output", False):
                print(json.dumps(lessons, indent=2))
            else:
                print(f"Lessons ({len(lessons)} found):")
                for l in lessons:
                    print(f"  [{l['lifecycle_status']}] #{l['id']} {l['lesson'][:80]}")
                    if l.get("reusable_rule"):
                        print(f"    Rule: {l['reusable_rule']}")
                    if l.get("source_task"):
                        print(f"    Task: {l['source_task']}")
                    conf = l.get("confidence", 1.0)
                    print(f"    Confidence: {conf:.0%} | Agent: {l.get('agent_id', 'unknown')} | {l['observed_at']}")
        else:
            learn_parser.print_help()

    else:
        parser.print_help()

if __name__ == "__main__":
    main()
