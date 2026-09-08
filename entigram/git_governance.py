"""Git-native semantic governance for Entigram workspaces.

Git remains the source of truth for bytes and history.  This module adds a
small, deliberately conservative semantic layer for LDS contracts: only
changes that can be proven non-overlapping are merged automatically.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from entigram.schema_compiler.parser import SchemaParser


EVIDENCE_DIR = Path(".etg/evidence")
MERGE_EVIDENCE_DIR = EVIDENCE_DIR / "merges"
RESOLUTION_EVIDENCE_DIR = EVIDENCE_DIR / "resolutions"
HANDOFF_EVIDENCE_DIR = EVIDENCE_DIR / "handoffs"
HOOK_START = "# >>> entigram git governance >>>"
HOOK_END = "# <<< entigram git governance <<<"


class GitGovernanceError(ValueError):
    pass


def _run(root: Path, args: List[str], *, text: bool = True) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            ["git", "-C", str(root), *args], capture_output=True, text=text, check=False
        )
    except FileNotFoundError as exc:
        raise GitGovernanceError("git is required for Git governance") from exc


def _require_git_root(root: Path) -> Path:
    result = _run(root, ["rev-parse", "--show-toplevel"])
    if result.returncode:
        raise GitGovernanceError("target is not inside a Git working tree")
    return Path(result.stdout.strip()).resolve()


def _rev(root: Path, ref: str) -> str:
    result = _run(root, ["rev-parse", "--verify", f"{ref}^{{commit}}"])
    if result.returncode:
        raise GitGovernanceError(f"unable to resolve Git revision: {ref}")
    return result.stdout.strip()


def _merge_base(root: Path, ours: str, theirs: str) -> str:
    result = _run(root, ["merge-base", ours, theirs])
    if result.returncode:
        raise GitGovernanceError(f"unable to calculate merge base for {ours} and {theirs}")
    return result.stdout.strip()


def _git_path(root: Path, path: str) -> Path:
    result = _run(root, ["rev-parse", "--git-path", path])
    if result.returncode or not result.stdout.strip():
        raise GitGovernanceError(f"unable to resolve Git path: {path}")
    candidate = Path(result.stdout.strip())
    return candidate if candidate.is_absolute() else root / candidate


def _blob(root: Path, revision: str, path: str) -> Optional[str]:
    result = _run(root, ["show", f"{revision}:{path}"])
    if result.returncode:
        # Git uses this form for a genuinely absent path. Do not hide failures
        # resolving a revision because revisions are validated before this call.
        return None
    return result.stdout


def _working_blob(root: Path, path: str) -> Optional[str]:
    candidate = root / path
    return candidate.read_text() if candidate.is_file() else None


def _index_blob(root: Path, path: str) -> Optional[str]:
    result = _run(root, ["show", f":{path}"])
    return result.stdout if result.returncode == 0 else None


def _sha(value: Optional[str]) -> Optional[str]:
    return hashlib.sha256(value.encode()).hexdigest() if value is not None else None


def _entity_state(entity: Any) -> Dict[str, Any]:
    return {
        "name": entity.name,
        "external_ref": entity.external_ref,
        "attributes": sorted(entity.attributes, key=lambda item: item["name"]),
    }


def _relationship_state(rel: Any) -> Dict[str, Any]:
    return {
        "entity_a": rel.entity_a,
        "degree_a": rel.degree_a,
        "part_a": rel.part_a,
        "entity_b": rel.entity_b,
        "degree_b": rel.degree_b,
        "part_b": rel.part_b,
    }


def schema_state(text: Optional[str]) -> Dict[str, Any]:
    """Return a canonical parsed LDS state. Missing files are valid states."""
    if text is None:
        return {"entities": {}, "relationships": {}}
    parser = SchemaParser(text)
    entities, relationships = parser.parse()
    entity_map = {name: _entity_state(entity) for name, entity in entities.items()}
    relationship_map = {
        json.dumps(_relationship_state(rel), sort_keys=True): _relationship_state(rel)
        for rel in relationships
    }
    return {"entities": entity_map, "relationships": relationship_map}


def _unsupported_auto_merge_constructs(text: Optional[str]) -> List[str]:
    """Detect LDS features the canonical renderer cannot round-trip losslessly."""
    if text is None:
        return []
    patterns = {
        "comments": ("/*", "#", "//"),
        "block syntax": ("{", "}"),
        "external entities": ("EXTERNAL_ENTITY", "::"),
        "external attribute links": ("[EXTERNAL:",),
        "edge metadata": ("[EDGE_BOUNDARY]",),
    }
    return [name for name, markers in patterns.items() if any(marker in text for marker in markers)]


def _changed(base: Any, candidate: Any) -> bool:
    return base != candidate


def _classify_map(kind: str, base: Dict[str, Any], ours: Dict[str, Any], theirs: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    safe: List[Dict[str, Any]] = []
    conflicts: List[Dict[str, Any]] = []
    for key in sorted(set(base) | set(ours) | set(theirs)):
        original, local, remote = base.get(key), ours.get(key), theirs.get(key)
        ours_changed, theirs_changed = _changed(original, local), _changed(original, remote)
        if not ours_changed or not theirs_changed or local == remote:
            safe.append({"kind": kind, "id": key, "classification": "safe"})
            continue
        conflict_id = hashlib.sha256(f"{kind}:{key}".encode()).hexdigest()[:16]
        conflicts.append({
            "id": f"GIT-{kind.upper()}-{conflict_id}",
            "kind": kind,
            "key": key,
            "classification": "review_required",
            "base": original,
            "ours": local,
            "theirs": remote,
        })
    return safe, conflicts


def _merge_value(base: Any, ours: Any, theirs: Any) -> Tuple[Any, bool]:
    """Return a three-way value and whether it has an irreconcilable conflict."""
    ours_changed, theirs_changed = ours != base, theirs != base
    if not ours_changed:
        return theirs, False
    if not theirs_changed or ours == theirs:
        return ours, False
    return None, True


def _merge_entity(base: Optional[Dict[str, Any]], ours: Optional[Dict[str, Any]], theirs: Optional[Dict[str, Any]]) -> Tuple[Optional[Dict[str, Any]], bool]:
    """Three-way merge an entity, allowing only disjoint attribute additions."""
    if base is None:
        if ours is None or theirs is None:
            return ours if theirs is None else theirs, False
        if ours == theirs:
            return ours, False
        external_ref, bad_ref = _merge_value(None, ours.get("external_ref"), theirs.get("external_ref"))
        if bad_ref:
            return None, True
        original_attrs: Dict[str, Any] = {}
    elif ours is None or theirs is None:
        # A deletion concurrent with a modification needs a human decision.
        if ours == theirs:
            return None, False
        return None, True
    else:
        external_ref, bad_ref = _merge_value(base.get("external_ref"), ours.get("external_ref"), theirs.get("external_ref"))
        if bad_ref:
            return None, True
        original_attrs = {item["name"]: item for item in base.get("attributes", [])}

    ours_attrs = {item["name"]: item for item in (ours or {}).get("attributes", [])}
    theirs_attrs = {item["name"]: item for item in (theirs or {}).get("attributes", [])}
    merged_attrs: Dict[str, Any] = {}
    for name in sorted(set(original_attrs) | set(ours_attrs) | set(theirs_attrs)):
        value, conflict = _merge_value(original_attrs.get(name), ours_attrs.get(name), theirs_attrs.get(name))
        if conflict:
            return None, True
        if value is not None:
            merged_attrs[name] = value
    name = (ours or theirs or base)["name"]
    return {"name": name, "external_ref": external_ref, "attributes": [merged_attrs[key] for key in sorted(merged_attrs)]}, False


def _classify_entities(base: Dict[str, Any], ours: Dict[str, Any], theirs: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    safe: List[Dict[str, Any]] = []
    conflicts: List[Dict[str, Any]] = []
    for key in sorted(set(base) | set(ours) | set(theirs)):
        _merged, conflict = _merge_entity(base.get(key), ours.get(key), theirs.get(key))
        if not conflict:
            safe.append({"kind": "entity", "id": key, "classification": "safe"})
            continue
        conflict_id = hashlib.sha256(f"entity:{key}".encode()).hexdigest()[:16]
        conflicts.append({
            "id": f"GIT-ENTITY-{conflict_id}", "kind": "entity", "key": key,
            "classification": "review_required", "base": base.get(key),
            "ours": ours.get(key), "theirs": theirs.get(key),
        })
    return safe, conflicts


def assess_schema(base_text: Optional[str], ours_text: Optional[str], theirs_text: Optional[str]) -> Dict[str, Any]:
    """Assess a three-way LDS change without selecting a semantic winner."""
    base, ours, theirs = schema_state(base_text), schema_state(ours_text), schema_state(theirs_text)
    entity_safe, entity_conflicts = _classify_entities(base["entities"], ours["entities"], theirs["entities"])
    relationship_safe, relationship_conflicts = _classify_map(
        "relationship", base["relationships"], ours["relationships"], theirs["relationships"]
    )
    conflicts = entity_conflicts + relationship_conflicts
    unsupported = sorted(set(
        _unsupported_auto_merge_constructs(base_text)
        + _unsupported_auto_merge_constructs(ours_text)
        + _unsupported_auto_merge_constructs(theirs_text)
    ))
    if unsupported:
        conflicts.append({
            "id": "GIT-SYNTAX-LOSSLESS-RENDER-REQUIRED",
            "kind": "syntax",
            "key": "lossless_render_required",
            "classification": "review_required",
            "reason": "automatic merge would not preserve all LDS constructs",
            "unsupported_constructs": unsupported,
            "base": None,
            "ours": None,
            "theirs": None,
        })
    return {
        "safe": not conflicts,
        "classifications": entity_safe + relationship_safe,
        "conflicts": conflicts,
        "hashes": {"base": _sha(base_text), "ours": _sha(ours_text), "theirs": _sha(theirs_text)},
    }


def _render_state(state: Dict[str, Any]) -> str:
    lines: List[str] = []
    for name in sorted(state["entities"]):
        entity = state["entities"][name]
        lines.extend([f"ENTITY: {name}", "ATTRIBUTES:"])
        for attr in entity["attributes"]:
            prefix = "." if attr.get("pk") else "- "
            suffix = ", ".join([attr["type"], *attr.get("constraints", [])])
            lines.append(f"  {prefix}{attr['name']} ({suffix})")
        lines.append("")
    if state["relationships"]:
        lines.append("RELATIONSHIPS:")
        for key in sorted(state["relationships"]):
            rel = state["relationships"][key]
            lines.append(
                f"  {rel['entity_a']} ({rel['degree_a']}) [{rel['part_a']}] --- "
                f"[{rel['part_b']}] ({rel['degree_b']}) {rel['entity_b']}"
            )
    return "\n".join(lines).rstrip() + "\n"


def safe_merge(base_text: Optional[str], ours_text: Optional[str], theirs_text: Optional[str]) -> Tuple[Optional[str], Dict[str, Any]]:
    assessment = assess_schema(base_text, ours_text, theirs_text)
    if not assessment["safe"]:
        return None, assessment
    base, ours, theirs = schema_state(base_text), schema_state(ours_text), schema_state(theirs_text)
    merged: Dict[str, Dict[str, Any]] = {"entities": {}, "relationships": {}}
    for key in sorted(set(base["entities"]) | set(ours["entities"]) | set(theirs["entities"])):
        value, conflict = _merge_entity(base["entities"].get(key), ours["entities"].get(key), theirs["entities"].get(key))
        if conflict:
            raise GitGovernanceError("internal error: safe entity merge classified as conflicting")
        if value is not None:
            merged["entities"][key] = value
    for key in sorted(set(base["relationships"]) | set(ours["relationships"]) | set(theirs["relationships"])):
        value, conflict = _merge_value(base["relationships"].get(key), ours["relationships"].get(key), theirs["relationships"].get(key))
        if conflict:
            raise GitGovernanceError("internal error: safe relationship merge classified as conflicting")
        if value is not None:
            merged["relationships"][key] = value
    return _render_state(merged), assessment


def _changed_lds_paths(root: Path, base: str, head: str) -> List[str]:
    result = _run(root, ["diff", "--name-only", f"{base}..{head}", "--", "*.lds"])
    if result.returncode:
        raise GitGovernanceError("unable to list changed LDS files")
    return [path for path in result.stdout.splitlines() if path.strip()]


def _codeowners(root: Path, paths: Iterable[str]) -> Dict[str, List[str]]:
    candidate = root / "CODEOWNERS"
    if not candidate.is_file():
        candidate = root / ".github" / "CODEOWNERS"
    if not candidate.is_file():
        return {}
    rules: List[Tuple[str, List[str]]] = []
    for raw in candidate.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) > 1:
            rules.append((parts[0].lstrip("/"), parts[1:]))
    result: Dict[str, List[str]] = {}
    for path in paths:
        owners: List[str] = []
        for pattern, declared in rules:
            if path == pattern or (pattern.endswith("/") and path.startswith(pattern)) or Path(path).match(pattern):
                owners = declared
        if owners:
            result[path] = owners
    return result


def assess_repository(root: Path, base_ref: str, head_ref: str = "HEAD", *, staged: bool = False) -> Dict[str, Any]:
    root = _require_git_root(root)
    head = _rev(root, head_ref)
    base_target = _rev(root, base_ref)
    base = _merge_base(root, head, base_target)
    if staged:
        result = _run(root, ["diff", "--cached", "--name-only", "--", "*.lds"])
        paths = [item for item in result.stdout.splitlines() if item.strip()]
    else:
        paths = _changed_lds_paths(root, base, head)
    files = []
    conflicts = []
    for path in paths:
        ours = _index_blob(root, path) if staged else _blob(root, head, path)
        theirs = _blob(root, base_target, path)
        assessment = assess_schema(_blob(root, base, path), ours, theirs)
        assessment["path"] = path
        files.append(assessment)
        conflicts.extend({**conflict, "path": path} for conflict in assessment["conflicts"])
    return {
        "format": "entigram.git-assessment.v1",
        "repository": str(root),
        "base": base,
        "ours": head,
        "theirs": base_target,
        "staged": staged,
        "paths": paths,
        "owners": _codeowners(root, paths),
        "files": files,
        "conflicts": conflicts,
        "safe": not conflicts,
    }


def _canonical_json(payload: Dict[str, Any]) -> str:
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def write_assessment(root: Path, assessment: Dict[str, Any]) -> Path:
    root = _require_git_root(root)
    stable = {key: value for key, value in assessment.items() if key != "generated_at"}
    digest = hashlib.sha256(json.dumps(stable, sort_keys=True).encode()).hexdigest()[:16]
    output = root / MERGE_EVIDENCE_DIR / f"{assessment['base'][:12]}-{assessment['ours'][:12]}-{digest}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = {**assessment, "generated_at": datetime.now(timezone.utc).isoformat()}
    if output.exists():
        return output
    output.write_text(_canonical_json(payload))
    return output


def write_handoff_bundle(root: Path, assessment: Dict[str, Any], assessment_path: Path) -> Path:
    """Create the immutable pre-handoff artifact which the broker will anchor."""
    root = _require_git_root(root)
    payload = {
        "format": "entigram.git-handoff.v1",
        "repository": str(root),
        "commit": assessment["ours"],
        "base": assessment["base"],
        "target": assessment["theirs"],
        "assessment": assessment_path.relative_to(root).as_posix(),
        "assessment_hash": hashlib.sha256(assessment_path.read_bytes()).hexdigest(),
        "governed_paths": assessment["paths"],
        "safe": assessment["safe"],
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    digest = hashlib.sha256(_canonical_json(payload).encode()).hexdigest()[:16]
    output = root / HANDOFF_EVIDENCE_DIR / f"{assessment['ours'][:12]}-{digest}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(_canonical_json(payload))
    return output


def _read_report(root: Path, value: str) -> Tuple[Path, Dict[str, Any]]:
    path = (root / value).resolve() if not Path(value).is_absolute() else Path(value).resolve()
    if root not in path.parents:
        raise GitGovernanceError("report path must stay inside the repository")
    try:
        return path, json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise GitGovernanceError(f"unable to read merge assessment: {value}") from exc


def _union_entity(ours: Optional[Dict[str, Any]], theirs: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if ours is None:
        return theirs
    if theirs is None:
        return ours
    merged = {**ours, "attributes": []}
    attributes: Dict[str, Dict[str, Any]] = {}
    for attribute in [*ours.get("attributes", []), *theirs.get("attributes", [])]:
        name = attribute["name"]
        if name in attributes and attributes[name] != attribute:
            raise GitGovernanceError(f"union cannot reconcile incompatible attribute: {name}")
        attributes[name] = attribute
    merged["attributes"] = [attributes[name] for name in sorted(attributes)]
    return merged


def _apply_resolution(root: Path, conflict: Dict[str, Any], strategy: str) -> None:
    path = root / conflict["path"]
    current = schema_state(path.read_text() if path.exists() else None)
    category = "entities" if conflict["kind"] == "entity" else "relationships"
    key = conflict["key"]
    if strategy == "ours":
        selected = conflict["ours"]
    elif strategy == "theirs":
        selected = conflict["theirs"]
    elif category == "entities":
        selected = _union_entity(conflict["ours"], conflict["theirs"])
    else:
        raise GitGovernanceError("union is only supported for entity attributes; select ours or theirs for relationships")
    if selected is None:
        current[category].pop(key, None)
    else:
        current[category][key] = selected
    path.write_text(_render_state(current))


def resolve_conflict(
    root: Path,
    report_value: str,
    conflict_id: str,
    strategy: str,
    rationale: str,
    *,
    apply: bool = False,
) -> Path:
    if strategy not in {"ours", "theirs", "union"}:
        raise GitGovernanceError("strategy must be ours, theirs, or union")
    root = _require_git_root(root)
    report_path, report = _read_report(root, report_value)
    conflict = next((item for item in report.get("conflicts", []) if item.get("id") == conflict_id), None)
    if conflict is None:
        raise GitGovernanceError(f"unknown conflict ID: {conflict_id}")
    if apply:
        if report.get("ours") != _rev(root, "HEAD"):
            raise GitGovernanceError(
                "assessment is stale for this HEAD; run `etg git rebase-check` and create a new assessment"
            )
        file_record = next((item for item in report.get("files", []) if item.get("path") == conflict["path"]), None)
        current_text = _working_blob(root, conflict["path"])
        expected_hash = (file_record or {}).get("hashes", {}).get("ours")
        unresolved_markers = current_text and any(marker in current_text for marker in ("<<<<<<<", "=======", ">>>>>>>"))
        if expected_hash and _sha(current_text) != expected_hash and not unresolved_markers:
            raise GitGovernanceError(
                "affected file changed after assessment; create a new assessment before applying a resolution"
            )
        _apply_resolution(root, conflict, strategy)
    payload = {
        "format": "entigram.git-resolution.v1",
        "assessment": report_path.relative_to(root).as_posix(),
        "assessment_base": report.get("base"),
        "assessment_ours": report.get("ours"),
        "conflict_id": conflict_id,
        "path": conflict["path"],
        "strategy": strategy,
        "rationale": rationale,
        "resolved_by": os.environ.get("GIT_AUTHOR_NAME") or os.environ.get("USER") or "unknown",
        "resolved_at": datetime.now(timezone.utc).isoformat(),
    }
    digest = hashlib.sha256(_canonical_json(payload).encode()).hexdigest()[:16]
    output = root / RESOLUTION_EVIDENCE_DIR / f"{conflict_id.lower()}-{digest}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(_canonical_json(payload))
    # The SQLite ledger is a local index. Git-tracked evidence remains the
    # collaborative source of truth and resolution succeeds if no ledger exists.
    if (root / ".etg").is_dir():
        from entigram.sqlite_ledger.manager import LedgerManager
        ledger = LedgerManager(str(root / ".etg" / "state.db"))
        try:
            ledger.record_resolution(
                conflict_id,
                conflict["kind"],
                json.dumps({"strategy": strategy, "evidence": output.relative_to(root).as_posix()}),
                rationale,
            )
        finally:
            ledger.close()
    return output


def assessment_is_fresh(root: Path, report_value: str, base_ref: str, head_ref: str = "HEAD") -> Dict[str, Any]:
    _report_path, report = _read_report(root, report_value)
    current = assess_repository(root, base_ref, head_ref)
    expected = (report.get("base"), report.get("ours"), report.get("theirs"))
    actual = (current.get("base"), current.get("ours"), current.get("theirs"))
    return {**current, "fresh": expected == actual, "recorded": {"base": expected[0], "ours": expected[1], "theirs": expected[2]}}


def check_repository(root: Path, base_ref: str, head_ref: str = "HEAD", *, staged: bool = False, write_evidence: bool = False) -> Dict[str, Any]:
    assessment = assess_repository(root, base_ref, head_ref, staged=staged)
    marker_paths = []
    for path in assessment["paths"]:
        text = _index_blob(root, path) if staged else _blob(Path(assessment["repository"]), assessment["ours"], path)
        if text and any(marker in text for marker in ("<<<<<<<", "=======", ">>>>>>>")):
            marker_paths.append(path)
    assessment["conflict_marker_paths"] = marker_paths
    assessment["safe"] = assessment["safe"] and not marker_paths
    if write_evidence:
        assessment["evidence_path"] = write_assessment(Path(assessment["repository"]), assessment).relative_to(Path(assessment["repository"])).as_posix()
    return assessment


def install(root: Path, *, merge_driver: bool = False, ci_github: bool = False, shared: bool = False) -> Dict[str, Any]:
    root = _require_git_root(root)
    hook_result = _run(root, ["rev-parse", "--git-path", "hooks/pre-commit"])
    hook = Path(hook_result.stdout.strip())
    if not hook.is_absolute():
        hook = root / hook
    hook.parent.mkdir(parents=True, exist_ok=True)
    current = hook.read_text() if hook.exists() else "#!/bin/sh\n"
    if HOOK_START not in current:
        block = f"""{HOOK_START}\n# Entigram Git semantic check (explicitly installed).\netg git check --base HEAD --staged\nstatus=$?\nif [ $status -ne 0 ]; then exit $status; fi\n{HOOK_END}\n"""
        hook.write_text(current.rstrip() + "\n\n" + block)
        hook.chmod(hook.stat().st_mode | 0o111)
    result: Dict[str, Any] = {"hook": str(hook), "merge_driver": False, "ci": False}
    if merge_driver:
        _run(root, ["config", "merge.entigram-lds.name", "Entigram safe LDS semantic merge"])
        _run(root, ["config", "merge.entigram-lds.driver", "etg git merge-driver %O %A %B"])
        attributes = (root / ".gitattributes") if shared else _git_path(root, "info/attributes")
        attributes.parent.mkdir(parents=True, exist_ok=True)
        existing = attributes.read_text() if attributes.exists() else ""
        line = "*.lds merge=entigram-lds"
        if line not in existing.splitlines():
            attributes.write_text(existing.rstrip() + "\n" + line + "\n")
        result["merge_driver"] = True
        result["attributes"] = str(attributes)
    if ci_github:
        workflow = root / ".github/workflows/entigram-governance.yml"
        workflow.parent.mkdir(parents=True, exist_ok=True)
        if workflow.exists():
            raise GitGovernanceError(f"refusing to replace existing workflow: {workflow.relative_to(root)}")
        workflow.write_text(GITHUB_WORKFLOW)
        result["ci"] = True
        result["workflow"] = str(workflow.relative_to(root))
    return result


def run_merge_driver(base_path: str, ours_path: str, theirs_path: str) -> int:
    base, ours, theirs = Path(base_path), Path(ours_path), Path(theirs_path)
    merged, _assessment = safe_merge(
        base.read_text() if base.exists() else None,
        ours.read_text() if ours.exists() else None,
        theirs.read_text() if theirs.exists() else None,
    )
    if merged is None:
        return 1
    ours.write_text(merged)
    return 0


GITHUB_WORKFLOW = """name: Entigram governance\n\non:\n  pull_request:\n    branches: [main]\n\npermissions:\n  contents: read\n\njobs:\n  governance:\n    name: Entigram semantic governance\n    runs-on: ubuntu-latest\n    steps:\n      - uses: actions/checkout@v4\n        with:\n          fetch-depth: 0\n      - uses: actions/setup-python@v5\n        with:\n          python-version: '3.12'\n      - run: python -m pip install entigram-ai\n      - name: Check governed merge\n        run: etg git check --base \"${{ github.event.pull_request.base.sha }}\" --head \"${{ github.sha }}\" --json > entigram-governance.json\n      - uses: actions/upload-artifact@v4\n        if: always()\n        with:\n          name: entigram-governance\n          path: entigram-governance.json\n"""
