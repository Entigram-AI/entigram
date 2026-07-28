import os
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import yaml


DEFAULT_GOVERNED_ARTIFACT_GLOBS: Tuple[str, ...] = (
    "**/*.py",
    "**/*.pyi",
    "**/*.js",
    "**/*.jsx",
    "**/*.mjs",
    "**/*.cjs",
    "**/*.ts",
    "**/*.tsx",
    "**/*.vue",
    "**/*.svelte",
    "**/*.html",
    "**/*.css",
    "**/*.scss",
    "**/*.sass",
    "**/*.java",
    "**/*.kt",
    "**/*.kts",
    "**/*.go",
    "**/*.rs",
    "**/*.cs",
    "**/*.fs",
    "**/*.fsx",
    "**/*.rb",
    "**/*.php",
    "**/*.swift",
    "**/*.c",
    "**/*.h",
    "**/*.cc",
    "**/*.cpp",
    "**/*.hpp",
    "**/*.scala",
    "**/*.sh",
    "**/*.bash",
    "**/*.zsh",
    "**/*.ps1",
    "**/*.sql",
    "**/*.graphql",
    "**/*.gql",
    "**/*.proto",
    "**/*.tf",
    "**/*.hcl",
    "**/*.toml",
    "**/*.yaml",
    "**/*.yml",
    "**/package.json",
    "**/package-lock.json",
    "**/yarn.lock",
    "**/bun.lock",
    "**/Cargo.lock",
    "**/go.mod",
    "**/go.sum",
    "**/requirements*.txt",
    "**/Pipfile",
    "**/poetry.lock",
    "**/uv.lock",
    "**/Gemfile",
    "**/composer.json",
    "**/Dockerfile",
    "**/Makefile",
)

_IGNORED_ARTIFACT_PARTS = {
    ".git",
    ".etg",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".cache",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".next",
    ".nox",
    ".tox",
    ".gradle",
    ".terraform",
    "build",
    "dist",
    "coverage",
    "target",
    "vendor",
}


WorkspacePath = Union[str, Path]


def load_workspace_manifest(target_dir: WorkspacePath) -> Dict[str, Any]:
    root = Path(target_dir).expanduser().resolve()
    manifest_path = root / ".etg" / "entigram.yaml"
    if not manifest_path.exists():
        return {}
    manifest = yaml.safe_load(manifest_path.read_text()) or {}
    if not isinstance(manifest, dict):
        raise ValueError("workspace manifest must be a YAML object")
    return manifest


def configured_schema_paths(
    target_dir: WorkspacePath,
    *,
    require_existing: bool = True,
) -> Optional[List[Path]]:
    root = Path(target_dir).expanduser().resolve()
    manifest = load_workspace_manifest(root)
    configured = manifest.get("schema_paths")
    if configured is None:
        return None
    if not isinstance(configured, list) or not configured:
        raise ValueError("schema_paths must be a non-empty list of local LDS file paths")

    paths: List[Path] = []
    seen = set()
    for value in configured:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("schema_paths entries must be non-empty strings")
        candidate = Path(value).expanduser()
        resolved = candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()
        _require_workspace_path(root, resolved, f"schema path escapes workspace: {value}")
        if resolved.suffix != ".lds":
            raise ValueError(f"schema path must reference an LDS file: {value}")
        if require_existing and not resolved.is_file():
            raise ValueError(f"schema path does not exist: {value}")
        if resolved not in seen:
            paths.append(resolved)
            seen.add(resolved)
    return paths


def authoritative_schema_paths(
    target_dir: WorkspacePath,
    *,
    require_existing: bool = True,
) -> List[Path]:
    root = Path(target_dir).expanduser().resolve()
    configured = configured_schema_paths(root, require_existing=require_existing)
    if configured is not None:
        return configured

    default = root / "schema.lds"
    if require_existing and not default.is_file():
        return []
    return [default]


def governed_artifact_paths(target_dir: WorkspacePath) -> List[Path]:
    root = Path(target_dir).expanduser().resolve()
    manifest = load_workspace_manifest(root)
    configured = manifest.get("governed_artifact_globs")
    if configured is not None:
        if not isinstance(configured, list) or not configured:
            raise ValueError("governed_artifact_globs must be a non-empty list")
        if not all(isinstance(value, str) and value.strip() for value in configured):
            raise ValueError("governed_artifact_globs entries must be non-empty strings")
        return _globbed_artifact_paths(root, tuple(configured))

    git_paths = _git_artifact_paths(root)
    if git_paths is not None:
        return git_paths
    return _globbed_artifact_paths(root, DEFAULT_GOVERNED_ARTIFACT_GLOBS)


def _git_artifact_paths(root: Path) -> Optional[List[Path]]:
    try:
        result = subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "ls-files",
                "-z",
                "--cached",
                "--others",
                "--exclude-standard",
                "--",
                ".",
            ],
            check=True,
            capture_output=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None

    paths = set()
    for raw_path in result.stdout.split(b"\0"):
        if not raw_path:
            continue
        relative = Path(os.fsdecode(raw_path))
        if relative.is_absolute() or ".." in relative.parts:
            continue
        candidate = root / relative
        if candidate.is_symlink() or not candidate.is_file():
            continue
        path = candidate.resolve()
        try:
            path.relative_to(root)
        except ValueError:
            continue
        normalized = path.relative_to(root)
        if _is_ignored_artifact_path(normalized):
            continue
        paths.add(path)
    return sorted(paths)


def _globbed_artifact_paths(root: Path, patterns: Tuple[str, ...]) -> List[Path]:
    paths = set()
    for pattern in patterns:
        candidate_pattern = Path(pattern)
        if candidate_pattern.is_absolute() or ".." in candidate_pattern.parts:
            raise ValueError(f"governed artifact glob must stay inside workspace: {pattern}")
        for path in root.glob(pattern):
            if path.is_symlink() or not path.is_file():
                continue
            relative = path.relative_to(root)
            if _is_ignored_artifact_path(relative):
                continue
            paths.add(path.resolve())
    return sorted(paths)


def workspace_relative_path(target_dir: WorkspacePath, path: WorkspacePath) -> str:
    root = Path(target_dir).expanduser().resolve()
    resolved = Path(path).expanduser().resolve()
    _require_workspace_path(root, resolved, f"path escapes workspace: {path}")
    return resolved.relative_to(root).as_posix()


def _require_workspace_path(root: Path, path: Path, message: str) -> None:
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError(message) from exc


def _is_ignored_artifact_path(path: Path) -> bool:
    for part in path.parts:
        lowered = part.lower()
        if part in _IGNORED_ARTIFACT_PARTS:
            return True
        if lowered == "site-packages" or lowered.endswith("venv"):
            return True
    return False
