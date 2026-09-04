"""Opt-in deterministic evidence collection for a pending Git patch.

This inspects a patch; callers still use ``etg action validate`` to authorize
the consequential submission. It does not claim semantic correctness.
"""
from __future__ import annotations

import ast
import hashlib
import subprocess
from pathlib import Path


def inspect_patch(workspace: Path, *, protected_paths: tuple[str, ...] = ()) -> dict:
    def run(*args: str) -> str:
        return subprocess.run(args, cwd=workspace, text=True, capture_output=True, check=True).stdout
    diff = run("git", "diff", "HEAD")
    status = run("git", "status", "--porcelain")
    files = []
    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            files.append(line[6:])
        elif line.startswith("--- a/"):
            files.append(line[6:])
    files = list(dict.fromkeys(files))
    protected = [path for path in files if any(path.startswith(prefix) for prefix in protected_paths)]
    syntax_errors = []
    for path in files:
        candidate = workspace / path
        if path.endswith(".py") and candidate.is_file():
            try:
                ast.parse(candidate.read_text(encoding="utf-8"))
            except SyntaxError as exc:
                syntax_errors.append({"path": path, "error": str(exc)})
    codes = []
    if not diff.strip(): codes.append("EMPTY_PATCH")
    if protected: codes.append("PROTECTED_PATH")
    if syntax_errors: codes.append("SYNTAX_ERROR")
    return {"ok": not codes, "decision": "admit" if not codes else "deny", "codes": codes,
            "diff_sha256": hashlib.sha256(diff.encode()).hexdigest(), "changed_files": files,
            "status": status, "protected_files": protected, "syntax_errors": syntax_errors,
            "scope": "structural_only"}
