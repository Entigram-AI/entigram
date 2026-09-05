"""Opt-in deterministic evidence collection for a pending Git patch.

This inspects a patch; callers still use ``etg action validate`` to authorize
the consequential submission. It does not claim semantic correctness.
"""
from __future__ import annotations

import ast
import hashlib
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Sequence


DEFAULT_PROTECTED_PATTERNS = (
    r"^tests?/.*",
    r".*_test\.py$",
    r"^test_.*\.py$",
    r"^setup\.(py|cfg)$",
    r"^pyproject\.toml$",
    r"^\.github/.*",
    r"^tox\.ini$",
)


def normalize_path(path: str) -> str:
    """Normalize relative path separators and collapse dots."""
    normalized = path.replace("\\", "/").strip()
    if normalized.startswith("./"):
        normalized = normalized[2:]
    parts = []
    for part in normalized.split("/"):
        if part == "..":
            if parts:
                parts.pop()
        elif part and part != ".":
            parts.append(part)
    return "/".join(parts)


def extract_files_from_diff(diff_text: str) -> List[str]:
    """Extract list of modified, added, or deleted files from unified git diff."""
    files: List[str] = []
    for line in diff_text.splitlines():
        path = None
        if line.startswith("+++ b/"):
            path = line[6:]
        elif line.startswith("--- a/"):
            path = line[6:]
        elif line.startswith("diff --git a/"):
            parts = line.split(" b/")
            if len(parts) >= 2:
                path = parts[1]

        if path:
            # Strip trailing tab or timestamp if present (unified diff format)
            path = path.split("\t")[0].strip()
            if path and path != "/dev/null" and path not in files:
                files.append(normalize_path(path))
    return files


def inspect_patch(
    workspace: Path | str,
    *,
    protected_paths: Sequence[str] = (),
) -> Dict[str, Any]:
    """Inspect git state in workspace and evaluate admission invariants.

    Returns structured evidence dictionary with ok, decision, codes, diff_sha256,
    diff_bytes, changed_files, untracked_files, protected_files, and syntax_errors.
    """
    workspace = Path(workspace).expanduser().resolve()

    def run_git(*args: str) -> str:
        res = subprocess.run(
            ["git", *args],
            cwd=workspace,
            text=True,
            capture_output=True,
        )
        return res.stdout

    try:
        diff = run_git("diff", "HEAD")
        status = run_git("status", "--porcelain")
    except Exception as exc:
        return {
            "ok": False,
            "decision": "deny",
            "codes": ["COMMAND_ERROR"],
            "error": str(exc),
            "diff_sha256": "",
            "diff_bytes": 0,
            "changed_files": [],
            "untracked_files": [],
            "protected_files": [],
            "syntax_errors": [],
            "scope": "structural_only",
        }

    modified_files: List[str] = []
    untracked_files: List[str] = []

    for line in status.splitlines():
        line = line.strip()
        if not line:
            continue
        st = line[:2].strip()
        filepath = normalize_path(line[2:].strip())
        if st == "??":
            untracked_files.append(filepath)
        else:
            modified_files.append(filepath)

    diff_files = extract_files_from_diff(diff)
    all_changed_files = list(dict.fromkeys(modified_files + diff_files))

    # Determine protected files
    protected_hits: List[str] = []
    patterns = list(DEFAULT_PROTECTED_PATTERNS)

    for file_path in all_changed_files:
        is_protected = False
        for prefix in protected_paths:
            norm_prefix = normalize_path(prefix)
            if not norm_prefix:
                continue
            prefix_dir = norm_prefix if norm_prefix.endswith("/") else norm_prefix + "/"
            if file_path == norm_prefix or file_path.startswith(prefix_dir):
                is_protected = True
                break

        if not is_protected:
            for pat in patterns:
                if re.match(pat, file_path):
                    is_protected = True
                    break

        if is_protected and file_path not in protected_hits:
            protected_hits.append(file_path)

    # Check syntax errors for Python files
    syntax_errors: List[Dict[str, str]] = []
    for file_path in all_changed_files:
        if file_path.endswith(".py"):
            candidate = workspace / file_path
            if candidate.is_file():
                try:
                    ast.parse(candidate.read_text(encoding="utf-8"))
                except SyntaxError as exc:
                    syntax_errors.append({"path": file_path, "error": str(exc)})

    codes: List[str] = []
    diff_bytes = len(diff.encode("utf-8"))
    diff_sha256 = hashlib.sha256(diff.encode("utf-8")).hexdigest() if diff else ""

    if not diff.strip():
        if untracked_files and not modified_files:
            codes.append("UNTRACKED_SCRATCH_ONLY")
        else:
            codes.append("EMPTY_PATCH")

    if protected_hits:
        codes.append("FORBIDDEN_FILE_MODIFICATION")

    if syntax_errors:
        codes.append("SYNTAX_ERROR")

    ok = len(codes) == 0
    decision = "admit" if ok else "deny"

    return {
        "ok": ok,
        "decision": decision,
        "codes": codes,
        "diff_sha256": diff_sha256,
        "diff_bytes": diff_bytes,
        "changed_files": all_changed_files,
        "untracked_files": untracked_files,
        "protected_files": protected_hits,
        "syntax_errors": syntax_errors,
        "status": status,
        "scope": "structural_only",
    }
