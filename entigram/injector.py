import os
import yaml
import shutil
from pathlib import Path
from datetime import datetime
from .project_history import add_project_to_history
from .sqlite_ledger.paths import CANONICAL_LEDGER_NAME


def _agent_policy_text() -> str:
    return """# Entigram Agent Policy

This file is the canonical project policy for agents working in this
Entigram-initialized workspace.

## Boot Sequence

1. Run `hydrate` in the initialized workspace. If the console script is not
   available, run `etg hydrate` or
   `python3 -m entigram.cli_runner.etg_cli hydrate`.
2. Read `.etg/entigram.yaml`, `schema.lds`, and this file.
3. If changing implementation behavior, run impact analysis before editing:
   `etg broker preflight --file <path>` and
   `etg broker impact --file <path>`.

## Governance Rules

- Treat `schema.lds` as the closed-world contract for entities and attributes.
- Use MCP/CLI tools for governed writes; do not bypass ledger APIs with ad hoc
  SQL or direct state mutation.
- Unknown entities, invented attributes, unverified alignments, and schema drift
  must be rejected or escalated to the human operator.
- Resolve conflicts through `.etg/state.db`.
- `etg pause` temporarily compacts Entigram-owned context and blocks governance
  operations. `etg resume` restores that context. These workspace commands are
  separate from `etg broker hibernate` and `etg broker resume`, which checkpoint
  an individual agent.
- `etg eject` archives `.etg` before detaching Entigram from the workspace. It
  does not delete project schemas, ontologies, or application code.

## Pre-Handoff Gate

Before handing work back after source, schema, ontology, package, or release
changes:

1. Run `etg broker handoff`.
2. Run `etg broker status`.

`broker status` must report `Delivery status: current` before handoff.
Do not run `warden lock` after `broker deliver`; `warden lock` mutates
`.etg/entigram.yaml` and immediately invalidates the delivery snapshot.
"""


def inject_entigram_manifest(target_dir: str, selected_packages: list, cli_engine: str) -> bool:
    """
    Bootstraps a new Entigram workspace by injecting the YAML manifest 
    and the selected package template files. Uses a .etg subfolder for metadata.
    """
    target_path = Path(target_dir).expanduser().resolve()
    entigram_dir = target_path / ".etg"
    
    try:
        target_path.mkdir(parents=True, exist_ok=True)
        entigram_dir.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        print(f"Error creating directory {target_dir}: {e}")
        return False

    # 1. Generate entigram.yaml in .etg/
    # Convert list to initial dict for version tracking
    locked_packages = {pkg: "0.0.1" for pkg in selected_packages}
    
    manifest = {
        "workspace_schema_version": 1,
        "packages": locked_packages,
        "cli_engine": cli_engine,
        "schema_paths": ["schema.lds"],
        # Keep generated workspaces portable when they are copied or moved.
        "state_ledger": f".etg/{CANONICAL_LEDGER_NAME}",
        "lifecycle": {"state": "active"},
        "status": "initialized",
        "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    
    with open(entigram_dir / "entigram.yaml", "w") as f:
        yaml.dump(manifest, f, default_flow_style=False)

    policy_path = entigram_dir / "agent_policy.md"
    if not policy_path.exists():
        policy_path.write_text(_agent_policy_text())

    # 1.5 Generate/Append instruction file for agent awareness
    if cli_engine == "Antigravity":
        instruction_file = "AGY.md"
    elif cli_engine == "Claude Code":
        instruction_file = "CLAUDE.md"
    elif cli_engine == "Ollama":
        instruction_file = "OLLAMA.md"
    elif cli_engine == "Codex":
        instruction_file = "AGENTS.md"
    else:
        instruction_file = "AGENT_INSTRUCTIONS.md"
        
    instruction_path = target_path / instruction_file
    
    entigram_context = f"""
<!-- ENTIGRAM_START -->
# Entigram Agent Context
You are an edge-agent operating within a Entigram Federated Architecture.

Read and follow `.etg/agent_policy.md` before changing this workspace.

## Workspace Context
- **Manifest:** You MUST read `.etg/entigram.yaml` (using your `read_file` tool) to understand project metadata and active packages.
- **Packages:** {", ".join(selected_packages)}
- **Decisions Ledger:** Contradictions must be resolved via the human tie-breaker ledger at `.etg/state.db`.

## Primary Directives
1. **Hydrate First:** Run `hydrate` at the start of every coding or modeling session.
2. **Schema First:** Never generate code or ontologies before an Entigram Schema is explicitly defined in `schema.lds`.
3. **Persistence:** You MUST maintain the local `schema.lds` and `draft_schema.lds` files. Update them (using your `replace` or `write_file` tools) after EVERY turn where new domain information is established.
4. Broker Interaction: Use the Entigram CLI for cross-domain orchestration:
   - **Preflight Changes:** `etg broker preflight --file [PATH]`
   - **Impact Analysis:** `etg broker impact --file [PATH]`
   - **Check Decisions:** `etg broker check --id [CONFLICT_ID]`
   - **Record Proposals:** `etg broker decide --id [ID] --type [ENTITY] --state [STATE] --rationale [WHY]`
   - **Report Conflicts:** `etg broker conflict --id [ID] --type [ENTITY] --states [JSON_STATES] --agent [AGENT_ID]`
   - **Align Domains:** `etg broker align --src_dom [DOM] --tgt_dom [DOM] --src_con [CON] --tgt_con [CON] --rat [WHY]`
   - **Validate Model:** `etg broker validate`
   - **Handoff:** `etg broker handoff`

5. **Domain Isolation:** Treat external systems as black boxes.
6. **Schema Contract Enforcement (Execution Mode):** Once a build is finalized, the `schema.lds` and `schema.ttl` files represent the immutable schema contracts of this workspace. You are forbidden from attempting to rewrite or modify these files during data execution or orchestration. Any attempt to drift from the established schema will trigger a `SCHEMA_GUARD_HALT`.
7. **Initialization Step:** As your first action, read the project manifest and the local `schema.lds` to synchronize your mental model with the current authoritative state.
8. **Expectation Guard Pre-Handoff Gate:** If you changed implementation behavior, run `etg broker handoff` before handoff. The guard executes unresolved modeled `validation_check` commands, records durable evidence, and fails until every active `EXPECTATION` is verified.

## Active Package Instructions
"""
    if "Entigram Schemas" in selected_packages:
        entigram_context += f"- **Schema Modeling:** Read `interview_prompt.md` and begin the domain modeling interview. Record your progress in `schema.lds`. (Note: For {'Antigravity' if cli_engine == 'Antigravity' else cli_engine}, ensure all turns are committed to state).\n"
    
    if len(selected_packages) > 0:
        entigram_context += "- **Package Skills:** You MUST read the `SKILL.md` file for each active package to understand your specific roles and protocols.\n"
    
    entigram_context += "<!-- ENTIGRAM_END -->\n"

    # Smart Injection: Append or Update instead of Overwrite
    if instruction_path.exists():
        with open(instruction_path, "r") as f:
            existing_content = f.read()
        
        if "<!-- ENTIGRAM_START -->" in existing_content:
            # Update existing Entigram block
            import re
            # Re-wrap in markers since we are replacing the whole block
            full_context = entigram_context.strip() + "\n"
            new_content = re.sub(r"<!-- ENTIGRAM_START -->.*?<!-- ENTIGRAM_END -->", full_context, existing_content, flags=re.DOTALL)
            with open(instruction_path, "w") as f:
                f.write(new_content)
        else:
            # Append to bottom
            with open(instruction_path, "a") as f:
                f.write("\n\n" + entigram_context)
    else:
        # Create new
        with open(instruction_path, "w") as f:
            f.write(entigram_context)

    # 1.6 Record in history
    try:
        add_project_to_history(target_dir)
    except Exception as e:
        print(f"Warning: Failed to record project history: {e}")

    # 2. Copy Templates (Keep templates in root for easy user access, or .etg?)
    # User wanted root for Schema, but manifest in .etg. Let's keep source files in root.
    # 2. Copy Templates
    # Use the internal templates directory within the package
    package_root = Path(__file__).parent
    local_packages_dir = target_path / ".etg" / "packages"

    template_map = {
        "Entigram Schemas": "schema_modeling",
        "Standard Personal Finance": "personal_finance",
        "Startup Founder": "startup_founder",
        "Business Strategy": "business_strategy"
    }

    for package in selected_packages:
        # 1. Check Standard Templates (Bundled)
        template_folder = template_map.get(package)
        src_path = None
        if template_folder:
            src_path = package_root / "templates" / template_folder

        # 2. Fallback to Local Packages (.etg/packages/)
        if (not src_path or not src_path.exists()) and local_packages_dir.exists():
            potential_local = local_packages_dir / package.replace(" ", "-")
            if potential_local.exists():
                src_path = potential_local
                
        # 3. Optional fallback to Registry if not found locally.
        # Workspace init should not unexpectedly mutate a global cache or perform
        # network operations unless explicitly requested.
        if (
            (not src_path or not src_path.exists())
            and os.environ.get("ENTIGRAM_INIT_FETCH_REGISTRY") == "1"
        ):
            from entigram.registry import EntigramRegistry
            registry = EntigramRegistry(target_dir)
            if registry.install_package(package):
                potential_local = local_packages_dir / package.replace(" ", "-")
                if potential_local.exists():
                    src_path = potential_local

        if src_path and src_path.exists():
            for item in src_path.iterdir():
                if item.name == ".keep" or item.name == ".gitignore" or item.name == ".etg": continue

                target_file = target_path / item.name
                # NEVER overwrite the user's model files if they already exist
                if item.name in ["schema.lds", "draft_schema.lds"] and target_file.exists():
                    continue

                if item.is_file():
                    shutil.copy2(item, target_file)
                elif item.is_dir():
                    shutil.copytree(item, target_file, dirs_exist_ok=True)
    return True
