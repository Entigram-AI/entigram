#!/usr/bin/env python3
"""Version helpers for Entigram builds.

Supports stable releases such as 0.0.1 and 1.100.100 plus PEP 440
snapshot builds such as 0.0.1.dev20260602183000.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path


VERSION_RE = re.compile(
    r"^(0|[1-9][0-9]*)\."
    r"(0|[1-9][0-9]*)\."
    r"(0|[1-9][0-9]*)"
    r"(?:(?:a|b|rc)(0|[1-9][0-9]*))?"
    r"(?:(?:\.post)(0|[1-9][0-9]*))?"
    r"(?:(?:\.dev)(0|[1-9][0-9]*))?$"
)


def validate_version(version: str) -> None:
    if not VERSION_RE.fullmatch(version):
        raise SystemExit(
            "Invalid Entigram version. Use PEP 440-compatible versions like "
            "0.0.1, 1.100.100, or 0.0.1.dev20260602183000."
        )


def set_version(version: str) -> dict[Path, str]:
    validate_version(version)
    originals: dict[Path, str] = {}

    required_replacements = {
        Path("pyproject.toml"): (
            re.compile(r'(?m)^version = "([^"]+)"$'),
            f'version = "{version}"',
        )
    }

    for path, (pattern, replacement) in required_replacements.items():
        text = path.read_text()
        updated, count = pattern.subn(replacement, text, count=1)
        if count != 1:
            raise SystemExit(f"Could not update version in {path}")
        originals[path] = text
        path.write_text(updated)

    metadata_path = Path("server.json")
    if metadata_path.exists():
        text = metadata_path.read_text()
        try:
            metadata = json.loads(text)
        except json.JSONDecodeError as error:
            raise SystemExit(f"Could not parse {metadata_path}: {error}") from error

        metadata["version"] = version
        for package in metadata.get("packages", []):
            if isinstance(package, dict) and "version" in package:
                package["version"] = version

        originals[metadata_path] = text
        metadata_path.write_text(json.dumps(metadata, indent=2) + "\n")

    setup_path = Path("setup.py")
    if setup_path.exists():
        text = setup_path.read_text()
        updated, count = re.subn(r'version="([^"]+)"', f'version="{version}"', text, count=1)
        if count:
            originals[setup_path] = text
            setup_path.write_text(updated)

    return originals


def restore(originals: dict[Path, str]) -> None:
    for path, text in originals.items():
        path.write_text(text)


def cmd_validate(args: argparse.Namespace) -> int:
    validate_version(args.version)
    print(args.version)
    return 0


def cmd_set(args: argparse.Namespace) -> int:
    set_version(args.version)
    print(f"Set Entigram package version to {args.version}")
    return 0


def cmd_build(args: argparse.Namespace) -> int:
    if not args.command:
        raise SystemExit("build requires a command after --")

    originals = set_version(args.version)
    try:
        return subprocess.run(args.command, check=False).returncode
    finally:
        restore(originals)


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate and set Entigram build versions.")
    subparsers = parser.add_subparsers(dest="command_name", required=True)

    validate_parser = subparsers.add_parser("validate")
    validate_parser.add_argument("version")
    validate_parser.set_defaults(func=cmd_validate)

    set_parser = subparsers.add_parser("set")
    set_parser.add_argument("version")
    set_parser.set_defaults(func=cmd_set)

    build_parser = subparsers.add_parser("build")
    build_parser.add_argument("version")
    build_parser.add_argument("command", nargs=argparse.REMAINDER)
    build_parser.set_defaults(func=cmd_build)

    args = parser.parse_args()
    if getattr(args, "command", None) and args.command[0] == "--":
        args.command = args.command[1:]
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
