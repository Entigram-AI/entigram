#!/usr/bin/env python3
"""Update a Homebrew formula from PyPI release metadata."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import textwrap
import time
import urllib.request
from pathlib import Path
from typing import Any


FORMULA_SOURCE_RE = re.compile(
    r'(?m)^(  url ")[^"]+\.tar\.gz("\n  sha256 ")[0-9a-f]{64}(")$'
)
DEPENDENCY_LINE_RE = re.compile(r'(?m)^  depends_on "([^"]+)".*$')
HOMEBREW_PYTHON_VERSION = "3.14"
RESOURCE_RE = re.compile(r'^\s*resource "([^"]+)" do')
NATIVE_DEPENDENCY_BY_RESOURCE = {
    "cryptography": "cryptography",
    "cffi": "cffi",
    "pycparser": "pycparser",
    "pydantic": "pydantic",
    "pydantic-core": "pydantic",
    "pydantic_core": "pydantic",
    "annotated-types": "pydantic",
    "annotated_types": "pydantic",
    "rpds-py": "rpds-py",
    "rpds_py": "rpds-py",
}
DEPENDENCY_ORDER = ["cffi", "cryptography", "pycparser", "pydantic", "rpds-py"]
NATIVE_DEPENDENCIES = frozenset(DEPENDENCY_ORDER)
SETUPTOOLS_RESOURCE = '''resource "setuptools" do
  url "https://files.pythonhosted.org/packages/4f/db/cfac1baf10650ab4d1c111714410d2fbb77ac5a616db26775db562c8fab2/setuptools-82.0.1.tar.gz"
  sha256 "7d872682c5d01cfde07da7bccc7b65469d3dca203318515ada1de5eda35efbf9"
end'''


def package_resource_names(package_name: str) -> set[str]:
    """Return likely poet resource names for the package being installed from buildpath."""
    return {
        package_name,
        package_name.replace("-", "_"),
        package_name.replace("_", "-"),
    }


def load_pypi_release(
    package_name: str,
    version: str,
    *,
    attempts: int = 12,
    sleep_seconds: int = 10,
) -> dict[str, Any]:
    url = f"https://pypi.org/pypi/{package_name}/{version}/json"
    last_error: Exception | None = None

    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(url, timeout=30) as response:
                return json.load(response)
        except Exception as exc:  # pragma: no cover - exercised in GitHub Actions
            last_error = exc
            if attempt == attempts:
                break
            time.sleep(sleep_seconds)

    raise RuntimeError(f"Could not load PyPI metadata for {package_name}=={version}: {last_error}")


def load_release_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def select_sdist(release: dict[str, Any]) -> tuple[str, str]:
    seen_files = []
    for file_info in release.get("urls", []):
        filename = file_info.get("filename", "")
        if filename:
            seen_files.append(filename)
        if file_info.get("packagetype") != "sdist":
            continue
        if not filename.endswith(".tar.gz"):
            continue
        sha256 = file_info.get("digests", {}).get("sha256")
        url = file_info.get("url")
        if url and sha256:
            return url, sha256

    files = ", ".join(seen_files) if seen_files else "none"
    raise RuntimeError(
        "PyPI metadata did not include a source .tar.gz with a sha256 digest "
        f"(files seen: {files})"
    )


def load_pypi_sdist(
    package_name: str,
    version: str,
    *,
    attempts: int = 18,
    sleep_seconds: int = 10,
) -> tuple[str, str]:
    """Wait for PyPI release metadata to expose the uploaded source archive."""
    last_error: Exception | None = None

    for attempt in range(1, attempts + 1):
        try:
            release = load_pypi_release(package_name, version, attempts=1)
            return select_sdist(release)
        except Exception as exc:  # pragma: no cover - retry path is unit-tested with mocks
            last_error = exc
            if attempt == attempts:
                break
            print(
                f"PyPI sdist metadata for {package_name}=={version} not ready "
                f"({exc}); retrying in {sleep_seconds}s "
                f"({attempt}/{attempts})...",
                file=sys.stderr,
            )
            time.sleep(sleep_seconds)

    raise RuntimeError(
        f"Could not find PyPI sdist metadata for {package_name}=={version} "
        f"after {attempts} attempts: {last_error}"
    )



def update_formula_source(formula_path: Path, source_url: str, sha256: str) -> None:
    text = formula_path.read_text()

    def replacement(match: re.Match[str]) -> str:
        return f"{match.group(1)}{source_url}{match.group(2)}{sha256}{match.group(3)}"

    updated, count = FORMULA_SOURCE_RE.subn(replacement, text, count=1)
    if count != 1:
        raise RuntimeError(f"Could not update top-level source URL/checksum in {formula_path}")

    formula_path.write_text(updated)


def update_formula_python_runtime(formula_path: Path) -> None:
    text = formula_path.read_text()
    updated, dep_count = re.subn(
        r'depends_on "python@\d+\.\d+"',
        f'depends_on "python@{HOMEBREW_PYTHON_VERSION}"',
        text,
        count=1,
    )
    updated, venv_count = re.subn(
        r'virtualenv_create\(libexec, "python\d+\.\d+"\)',
        f'virtualenv_create(libexec, "python{HOMEBREW_PYTHON_VERSION}")',
        updated,
        count=1,
    )
    if dep_count != 1 or venv_count != 1:
        raise RuntimeError(f"Could not update Python runtime in {formula_path}")
    formula_path.write_text(updated)


def filter_native_resources(
    resources_text: str,
    excluded_resource_names: set[str] | None = None,
) -> tuple[str, list[str]]:
    """
    Removes poet resource blocks that Homebrew should satisfy with bottled
    native formulas instead of source-building Rust/C extensions in the etg
    virtualenv.
    """
    filtered_lines = []
    native_deps = set()
    excluded_resource_names = excluded_resource_names or set()
    skip_mode = False

    for line in resources_text.splitlines():
        match = RESOURCE_RE.match(line)
        if match:
            resource_name = match.group(1)
            native_dep = NATIVE_DEPENDENCY_BY_RESOURCE.get(resource_name)
            if resource_name in excluded_resource_names:
                skip_mode = True
            elif native_dep:
                native_deps.add(native_dep)
                skip_mode = True

        if not skip_mode:
            filtered_lines.append(line)

        if skip_mode and line.strip() == "end":
            skip_mode = False

    cleaned_resources_text = textwrap.dedent("\n".join(filtered_lines)).strip()
    cleaned_resources_text = re.sub(
        r"\n[ \t]*\n(?:[ \t]*\n)+",
        "\n\n",
        cleaned_resources_text,
    )
    ordered_deps = [dep for dep in DEPENDENCY_ORDER if dep in native_deps]
    return cleaned_resources_text, ordered_deps


def render_formula_block(dependency_lines: list[str], resources_text: str) -> str:
    resources_text = textwrap.dedent(resources_text).strip()
    if 'resource "setuptools" do' not in resources_text:
        resources_text = SETUPTOOLS_RESOURCE + ("\n\n" + resources_text if resources_text else "")
    indented_resources = [
        "  " + line if line else ""
        for line in resources_text.splitlines()
    ]
    sections = []
    if dependency_lines:
        sections.append("\n".join(dependency_lines))
    if indented_resources:
        sections.append("\n".join(indented_resources))
    return "\n\n".join(sections) + "\n\n"


def render_dependency_block(native_deps: list[str], resources_text: str) -> str:
    dependency_lines = [f'  depends_on "{dep}"' for dep in native_deps]
    return render_formula_block(dependency_lines, resources_text)


def replace_formula_resources(
    formula_text: str,
    package_name: str,
    resources_text: str,
) -> str:
    """Replace generated resources and return brew-style-compliant formula text."""
    cleaned_resources_text, native_deps = filter_native_resources(
        resources_text,
        excluded_resource_names=package_resource_names(package_name),
    )
    install_idx = formula_text.find("  def install\n")
    dependency_matches = list(
        DEPENDENCY_LINE_RE.finditer(formula_text, 0, install_idx)
    )
    if install_idx == -1 or not dependency_matches:
        raise RuntimeError("Could not find markers to inject resources into formula")

    dependency_lines = {
        match.group(1): match.group(0)
        for match in dependency_matches
        if match.group(1) not in NATIVE_DEPENDENCIES
    }
    expected_python = f"python@{HOMEBREW_PYTHON_VERSION}"
    if expected_python not in dependency_lines:
        raise RuntimeError("Could not find markers to inject resources into formula")

    dependency_lines.update(
        {dependency: f'  depends_on "{dependency}"' for dependency in native_deps}
    )
    ordered_dependency_lines = [
        dependency_lines[name]
        for name in sorted(dependency_lines, key=str.casefold)
    ]
    generated_block = render_formula_block(
        ordered_dependency_lines,
        cleaned_resources_text,
    )
    return (
        formula_text[: dependency_matches[0].start()]
        + generated_block
        + formula_text[install_idx:]
    )


def update_resources(formula_path: Path, package_name: str, version: str) -> None:
    # Use poet to get the resources
    print("Installing package and poet in a temporary virtualenv...")
    subprocess.run([sys.executable, "-m", "venv", ".poet-venv"], check=True)
    # Install from the local repository root instead of PyPI to avoid race conditions
    repo_root = str(Path(__file__).resolve().parent.parent)
    subprocess.run([".poet-venv/bin/pip", "install", repo_root, "homebrew-pypi-poet", "setuptools<70"], check=True)
    
    print("Generating resources with poet...")
    result = subprocess.run([".poet-venv/bin/poet", package_name], capture_output=True, text=True, check=True)
    resources_text = result.stdout.strip()
    updated_text = replace_formula_resources(
        formula_path.read_text(),
        package_name,
        resources_text,
    )
    formula_path.write_text(updated_text)

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Update a Homebrew formula from PyPI metadata.")
    parser.add_argument("formula", type=Path)
    parser.add_argument("--package-name", default="entigram-ai")
    parser.add_argument("--version", required=True)
    parser.add_argument("--pypi-json", type=Path)
    parser.add_argument("--attempts", type=int, default=12)
    parser.add_argument("--sleep-seconds", type=int, default=10)
    args = parser.parse_args(argv)

    if args.pypi_json:
        release = load_release_json(args.pypi_json)
        source_url, sha256 = select_sdist(release)
    else:
        source_url, sha256 = load_pypi_sdist(
            args.package_name,
            args.version,
            attempts=args.attempts,
            sleep_seconds=args.sleep_seconds,
        )

    update_formula_source(args.formula, source_url, sha256)
    update_formula_python_runtime(args.formula)
    update_resources(args.formula, args.package_name, args.version)
    print(f"Updated {args.formula} to {source_url}")
    print(f"SHA256: {sha256}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
