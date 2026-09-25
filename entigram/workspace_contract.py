import fnmatch
import os
import subprocess
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple, Union

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
    nested_roots = _nested_workspace_roots(root)
    manifest = load_workspace_manifest(root)
    configured = manifest.get("governed_artifact_globs")
    if configured is not None:
        if not isinstance(configured, list) or not configured:
            raise ValueError("governed_artifact_globs must be a non-empty list")
        if not all(isinstance(value, str) and value.strip() for value in configured):
            raise ValueError("governed_artifact_globs entries must be non-empty strings")
        paths = _globbed_artifact_paths(root, tuple(configured), nested_roots)
        return _with_nested_workspace_proxies(root, paths, nested_roots)

    git_paths = _git_artifact_paths(root, nested_roots)
    if git_paths is not None:
        return _with_nested_workspace_proxies(root, git_paths, nested_roots)
    paths = _globbed_artifact_paths(root, DEFAULT_GOVERNED_ARTIFACT_GLOBS, nested_roots)
    return _with_nested_workspace_proxies(root, paths, nested_roots)


def _nested_workspace_roots(root: Path) -> List[Path]:
    """Return direct child workspaces without traversing their contents."""
    try:
        children = list(root.iterdir())
    except OSError:
        return []
    return sorted(
        (child.resolve() for child in children
         if child.is_dir() and (child / ".etg" / "entigram.yaml").is_file()),
        key=lambda child: child.as_posix(),
    )


def _with_nested_workspace_proxies(
    root: Path,
    paths: List[Path],
    nested_roots: Optional[List[Path]] = None,
) -> List[Path]:
    """Exclude child contents and retain one registered manifest per child."""
    children = nested_roots if nested_roots is not None else _nested_workspace_roots(root)
    retained = {
        path.resolve()
        for path in paths
        if not any(child in path.resolve().parents for child in children)
    }
    # The child manifest carries its registered Entigram contract fingerprint.
    # It changes when the child is checked in, without pulling its source tree
    # into the parent workspace's hydration or delivery-status inventory.
    retained.update(child / ".etg" / "entigram.yaml" for child in children)
    return sorted(retained)


def load_etgignore_patterns(root: Path) -> List[str]:
    """Load exclusion patterns from a root .etgignore file if present."""
    etgignore = root / ".etgignore"
    if not etgignore.is_file():
        return []
    try:
        lines = etgignore.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    patterns: List[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            patterns.append(stripped)
    return patterns


def _matches_ignore_pattern(relative_path: Path, pattern: str) -> bool:
    posix_path = relative_path.as_posix()
    clean_pattern = pattern.rstrip("/")
    if pattern.endswith("/"):
        if any(part == clean_pattern for part in relative_path.parts):
            return True
        if posix_path == clean_pattern or posix_path.startswith(f"{clean_pattern}/"):
            return True
    if fnmatch.fnmatch(posix_path, clean_pattern):
        return True
    if any(fnmatch.fnmatch(part, clean_pattern) for part in relative_path.parts):
        return True
    if fnmatch.fnmatch(posix_path, f"*/{clean_pattern}") or fnmatch.fnmatch(posix_path, f"{clean_pattern}/*"):
        return True
    return False


def _git_artifact_paths(
    root: Path,
    nested_roots: Optional[List[Path]] = None,
) -> Optional[List[Path]]:
    git_cmd = [
        "git",
        "-C",
        str(root),
        "ls-files",
        "-z",
        "--cached",
        "--others",
        "--exclude-standard",
    ]
    etgignore = root / ".etgignore"
    if etgignore.is_file():
        git_cmd.extend(["--exclude-from", str(etgignore)])
    git_cmd.extend(["--", "."])
    # Child workspaces are independent Git repositories. Exclude their trees
    # during Git inventory rather than filtering an already-enumerated list.
    # Their manifests are retained as proxies below.
    for child in nested_roots or _nested_workspace_roots(root):
        relative = child.relative_to(root).as_posix()
        git_cmd.append(f":(exclude){relative}/**")
    try:
        result = subprocess.run(
            git_cmd,
            check=True,
            capture_output=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None

    etg_patterns = load_etgignore_patterns(root)
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
        if _is_ignored_artifact_path(normalized, etg_patterns):
            continue
        paths.add(path)
    return sorted(paths)


def _globbed_artifact_paths(
    root: Path,
    patterns: Tuple[str, ...],
    nested_roots: Optional[List[Path]] = None,
) -> List[Path]:
    etg_patterns = load_etgignore_patterns(root)
    child_roots = set(nested_roots or _nested_workspace_roots(root))
    paths = set()
    for pattern in patterns:
        candidate_pattern = Path(pattern)
        if candidate_pattern.is_absolute() or ".." in candidate_pattern.parts:
            raise ValueError(f"governed artifact glob must stay inside workspace: {pattern}")
    for directory, subdirectories, filenames in os.walk(root):
        directory_path = Path(directory)
        subdirectories[:] = [
            name for name in subdirectories
            if (directory_path / name).resolve() not in child_roots
        ]
        for filename in filenames:
            path = directory_path / filename
            if path.is_symlink() or not path.is_file():
                continue
            relative = path.relative_to(root)
            if not any(_matches_governed_glob(relative, pattern) for pattern in patterns):
                continue
            if _is_ignored_artifact_path(relative, etg_patterns):
                continue
            paths.add(path.resolve())
    return sorted(paths)


def _matches_governed_glob(relative_path: Path, pattern: str) -> bool:
    """Match a relative path with ``Path.glob``-compatible leading ``**/``."""
    return relative_path.match(pattern) or (
        pattern.startswith("**/") and relative_path.match(pattern[3:])
    )


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


def _is_ignored_artifact_path(
    path: Path,
    extra_patterns: Optional[Iterable[str]] = None,
) -> bool:
    for part in path.parts:
        lowered = part.lower()
        if part in _IGNORED_ARTIFACT_PARTS:
            return True
        if lowered == "site-packages" or lowered.endswith("venv"):
            return True
    if extra_patterns:
        for pattern in extra_patterns:
            if _matches_ignore_pattern(path, pattern):
                return True
    return False
