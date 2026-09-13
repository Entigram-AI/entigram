"""Explicit, local parent-child links between Entigram workspaces.

Folder containment is only a discovery signal. A parent must record an
``allowed`` or ``denied`` decision before a child becomes part of its workspace
tree. The file stays local to the parent in ``.etg/workspace-links.yaml``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

import yaml


LINK_FILE = ".etg/workspace-links.yaml"
DECISIONS = {"allowed", "denied"}
SKIP_DIRECTORIES = {".git", ".venv", "node_modules", "__pycache__"}


def _root(value: str | Path) -> Path:
    return Path(value).expanduser().resolve()


def _link_path(parent: Path) -> Path:
    return parent / LINK_FILE


def _load(parent: Path) -> Dict[str, Any]:
    try:
        value = yaml.safe_load(_link_path(parent).read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        value = {}
    children = value.get("children") if isinstance(value, dict) else []
    return {"version": 1, "children": children if isinstance(children, list) else []}


def _safe_relative_child(parent: Path, value: str | Path) -> tuple[Path, str]:
    child = Path(value).expanduser()
    child = (parent / child if not child.is_absolute() else child).resolve()
    if child == parent or parent not in child.parents:
        raise ValueError("child workspace must be inside the parent workspace")
    if not (child / ".etg" / "entigram.yaml").is_file():
        raise ValueError("child workspace is not Entigram initialized")
    return child, child.relative_to(parent).as_posix()


def discover(parent_dir: str | Path) -> List[Dict[str, str]]:
    """Find initialized descendants and show their existing consent decision."""
    parent = _root(parent_dir)
    document = _load(parent)
    decisions = {
        str(item.get("path")): str(item.get("decision"))
        for item in document["children"]
        if isinstance(item, dict) and item.get("path")
    }
    discovered: List[Dict[str, str]] = []
    # One level at a time is intentional: a parent approves its direct children,
    # and each child controls whether its own descendants are linked.
    for child in parent.iterdir():
        if not child.is_dir() or child.name in SKIP_DIRECTORIES:
            continue
        manifest = child / ".etg" / "entigram.yaml"
        if not manifest.is_file():
            continue
        child = child.resolve()
        relative = child.relative_to(parent).as_posix()
        discovered.append({
            "path": relative,
            "name": child.name,
            "decision": decisions.get(relative, "pending"),
        })
    return sorted(discovered, key=lambda item: item["path"])


def decide(parent_dir: str | Path, child_value: str | Path, decision: str) -> Dict[str, str]:
    """Persist an owner's explicit allow or deny decision for one child."""
    if decision not in DECISIONS:
        raise ValueError("decision must be allowed or denied")
    parent = _root(parent_dir)
    _child, relative = _safe_relative_child(parent, child_value)
    document = _load(parent)
    children = [item for item in document["children"] if isinstance(item, dict) and item.get("path") != relative]
    children.append({"path": relative, "decision": decision})
    document["children"] = sorted(children, key=lambda item: str(item["path"]))
    destination = _link_path(parent)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    return {"path": relative, "decision": decision}


def allowed_children(parent_dir: str | Path) -> List[Dict[str, str]]:
    """Return only descendants that the parent explicitly allowed."""
    return [item for item in discover(parent_dir) if item["decision"] == "allowed"]
