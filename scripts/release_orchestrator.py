#!/usr/bin/env python3
"""Release Orchestrator for Entigram.

Discovers merged PRs and commits since the latest release tag, determines the
appropriate SemVer bump (major, minor, patch), compiles categorized release notes
in Keep-a-Changelog format, and applies version updates directly without requiring
an intermediate release-candidate PR.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any, Optional

# Regex for Conventional Commit subject lines:
# Examples:
#   feat(api)!: add breaking endpoint (#102)
#   fix: resolve edge case in parser (#105)
#   chore(main): release 2.21.1 (#107)
CONVENTIONAL_COMMIT_RE = re.compile(
    r"^(?P<type>[a-zA-Z0-9_-]+)(?:\((?P<scope>[^)]+)\))?(?P<breaking>!)?:\s*(?P<description>.+)$"
)

PR_NUMBER_RE = re.compile(r"\(#(?P<pr>\d+)\)")
MERGE_PR_RE = re.compile(r"Merge pull request #(?P<pr>\d+)")
SEMVER_TAG_RE = re.compile(r"^v?(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)$")


@dataclass
class CommitInfo:
    commit_hash: str
    short_hash: str
    author: str
    subject: str
    body: str
    pr_number: Optional[str]
    commit_type: Optional[str]
    scope: Optional[str]
    description: str
    is_breaking: bool
    breaking_description: Optional[str]


def run_git(args: list[str], cwd: Path) -> str:
    result = subprocess.run(
        ["git"] + args,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def get_latest_tag(repo_dir: Path) -> Optional[str]:
    """Return the most recent SemVer tag reachable from HEAD, or None."""
    try:
        raw_tag = run_git(["describe", "--tags", "--abbrev=0", "--match", "v*.*.*"], cwd=repo_dir)
        if raw_tag and SEMVER_TAG_RE.match(raw_tag):
            return raw_tag
    except subprocess.CalledProcessError:
        pass

    # Fallback: inspect all tags sorted by creatordate
    try:
        tags_output = run_git(["tag", "--sort=-creatordate"], cwd=repo_dir)
        for line in tags_output.splitlines():
            tag = line.strip()
            if tag and SEMVER_TAG_RE.match(tag):
                return tag
    except subprocess.CalledProcessError:
        pass

    return None


def get_current_project_version(repo_dir: Path) -> str:
    """Read the current version from pyproject.toml."""
    pyproject_path = repo_dir / "pyproject.toml"
    if not pyproject_path.is_file():
        raise FileNotFoundError(f"pyproject.toml not found at {pyproject_path}")
    content = pyproject_path.read_text(encoding="utf-8")
    match = re.search(r'(?m)^version = "([^"]+)"$', content)
    if not match:
        raise ValueError("Could not find version in pyproject.toml")
    return match.group(1)


def parse_commit_record(record: str) -> Optional[CommitInfo]:
    """Parse a git log record separated by unit separators (\\x1f)."""
    fields = record.split("\x1f")
    if len(fields) < 4:
        return None
    commit_hash, author, subject, body = fields[0].strip(), fields[1].strip(), fields[2].strip(), fields[3].strip()
    if not commit_hash or not subject:
        return None

    short_hash = commit_hash[:7]

    # Check for PR number in subject or body
    pr_number = None
    pr_match = PR_NUMBER_RE.search(subject)
    if pr_match:
        pr_number = pr_match.group("pr")
    else:
        body_pr_match = MERGE_PR_RE.search(body)
        if body_pr_match:
            pr_number = body_pr_match.group("pr")

    # Clean description from trailing PR number, e.g. "my feature (#102)" -> "my feature"
    clean_subject = PR_NUMBER_RE.sub("", subject).strip()

    # Check for conventional commit syntax
    conv_match = CONVENTIONAL_COMMIT_RE.match(clean_subject)
    is_breaking = False
    breaking_desc = None
    commit_type = None
    scope = None
    description = clean_subject

    if conv_match:
        commit_type = conv_match.group("type").lower()
        scope = conv_match.group("scope")
        if conv_match.group("breaking"):
            is_breaking = True
        description = conv_match.group("description").strip()

    # Check body for BREAKING CHANGE: / BREAKING-CHANGE:
    breaking_match = re.search(r"(?im)^BREAKING[ -]CHANGE:\s*(.+)$", body)
    if breaking_match:
        is_breaking = True
        breaking_desc = breaking_match.group(1).strip()
    elif is_breaking:
        breaking_desc = description

    return CommitInfo(
        commit_hash=commit_hash,
        short_hash=short_hash,
        author=author,
        subject=subject,
        body=body,
        pr_number=pr_number,
        commit_type=commit_type,
        scope=scope,
        description=description,
        is_breaking=is_breaking,
        breaking_description=breaking_desc,
    )


def get_commits_since_tag(repo_dir: Path, tag: Optional[str]) -> list[CommitInfo]:
    """List all parsed commits between tag and HEAD."""
    git_range = f"{tag}..HEAD" if tag else "HEAD"
    # Format: %H<US>%an<US>%s<US>%b<RS>
    log_format = "%H%x1f%an%x1f%s%x1f%b%x1e"
    try:
        raw_output = run_git(["log", git_range, f"--format={log_format}"], cwd=repo_dir)
    except subprocess.CalledProcessError as exc:
        print(f"Warning: git log failed: {exc}", file=sys.stderr)
        return []

    commits: list[CommitInfo] = []
    for record in raw_output.split("\x1e"):
        if not record.strip():
            continue
        info = parse_commit_record(record)
        if info:
            # Ignore release commits from previous runs
            if info.subject.startswith("chore(main): release") or info.subject.startswith("chore: release"):
                continue
            commits.append(info)
    return commits


def determine_bump_type(
    commits: list[CommitInfo],
    override: Optional[str] = None,
) -> tuple[Optional[str], bool]:
    """Determine the bump type ('major', 'minor', 'patch', or None) and whether release is needed."""
    if override and override.lower() in ("major", "minor", "patch"):
        return override.lower(), True

    if not commits:
        return None, False

    # Check for breaking changes
    if any(c.is_breaking for c in commits):
        return "major", True

    # Check for features
    if any(c.commit_type == "feat" for c in commits):
        return "minor", True

    # Check for fixes or other meaningful changes
    meaningful_types = {"fix", "perf", "refactor"}
    if any(c.commit_type in meaningful_types for c in commits):
        return "patch", True

    # If all commits are chore/docs/ci/test, check if any changes exist
    has_non_trivial_commits = any(
        c.commit_type not in {"chore", "docs", "ci", "test", "build"}
        for c in commits
    )
    if has_non_trivial_commits:
        return "patch", True

    return None, False


def bump_version(current_version: str, bump_type: str) -> str:
    """Calculate the next SemVer version string."""
    match = SEMVER_TAG_RE.match(current_version)
    if not match:
        raise ValueError(f"Current version '{current_version}' is not valid SemVer")

    major = int(match.group("major"))
    minor = int(match.group("minor"))
    patch = int(match.group("patch"))

    if bump_type == "major":
        return f"{major + 1}.0.0"
    elif bump_type == "minor":
        return f"{major}.{minor + 1}.0"
    elif bump_type == "patch":
        return f"{major}.{minor}.{patch + 1}"
    else:
        raise ValueError(f"Unknown bump type: {bump_type}")


CATEGORY_HEADERS = [
    ("breaking", "### ⚠ BREAKING CHANGES"),
    ("feat", "### Features"),
    ("fix", "### Bug Fixes"),
    ("perf", "### Performance Improvements"),
    ("refactor", "### Refactoring"),
    ("docs", "### Documentation"),
    ("misc", "### Miscellaneous"),
]


def compile_release_notes(
    commits: list[CommitInfo],
    prev_tag: Optional[str],
    next_version: str,
    repo_url: str = "https://github.com/Entigram-AI/entigram",
    today: Optional[date] = None,
) -> str:
    """Compile formatted markdown release notes matching Keep-a-Changelog."""
    today_str = (today or date.today()).isoformat()
    clean_prev = prev_tag if prev_tag else f"v{next_version}"
    compare_link = f"[{next_version}]({repo_url}/compare/{clean_prev}...v{next_version})"
    header = f"## {compare_link} ({today_str})\n\n"

    categorized: dict[str, list[str]] = {key: [] for key, _ in CATEGORY_HEADERS}

    for c in commits:
        pr_link = f" ([#{c.pr_number}]({repo_url}/issues/{c.pr_number}))" if c.pr_number else ""
        commit_link = f" ([{c.short_hash}]({repo_url}/commit/{c.commit_hash}))"
        scope_prefix = f"**{c.scope}:** " if c.scope else ""

        if c.is_breaking:
            desc = c.breaking_description or c.description
            item = f"* {scope_prefix}{desc}{pr_link}{commit_link}"
            categorized["breaking"].append(item)

        item = f"* {scope_prefix}{c.description}{pr_link}{commit_link}"
        if c.commit_type == "feat":
            categorized["feat"].append(item)
        elif c.commit_type == "fix":
            categorized["fix"].append(item)
        elif c.commit_type == "perf":
            categorized["perf"].append(item)
        elif c.commit_type == "refactor":
            categorized["refactor"].append(item)
        elif c.commit_type == "docs":
            categorized["docs"].append(item)
        elif not c.is_breaking:
            categorized["misc"].append(item)

    sections: list[str] = []
    for key, title in CATEGORY_HEADERS:
        items = categorized[key]
        if items:
            section_body = "\n".join(items)
            sections.append(f"{title}\n\n{section_body}\n")

    if not sections:
        sections.append("* Maintenance release with dependency and internal updates.\n")

    return header + "\n".join(sections).rstrip() + "\n"


def update_changelog(repo_dir: Path, release_notes: str) -> None:
    """Prepend release notes into CHANGELOG.md under # Changelog."""
    changelog_path = repo_dir / "CHANGELOG.md"
    if not changelog_path.is_file():
        changelog_path.write_text(f"# Changelog\n\n{release_notes}\n", encoding="utf-8")
        return

    content = changelog_path.read_text(encoding="utf-8")
    header_match = re.search(r"(?m)^# Changelog\s*\n+", content)
    if header_match:
        pos = header_match.end()
        # If there is an Unreleased section, place this after Unreleased
        unreleased_match = re.search(r"(?m)^## Unreleased[\s\S]*?(?=\n## |\Z)", content[pos:])
        if unreleased_match:
            insert_pos = pos + unreleased_match.end()
            new_content = (
                content[:insert_pos].rstrip()
                + "\n\n"
                + release_notes.strip()
                + "\n\n"
                + content[insert_pos:].lstrip()
            )
        else:
            new_content = (
                content[:pos]
                + release_notes.strip()
                + "\n\n"
                + content[pos:].lstrip()
            )
    else:
        new_content = f"# Changelog\n\n{release_notes.strip()}\n\n" + content

    changelog_path.write_text(new_content, encoding="utf-8")


def update_release_please_manifest(repo_dir: Path, version: str) -> None:
    """Keep .release-please-manifest.json in sync for tools/tests that inspect it."""
    manifest_path = repo_dir / ".release-please-manifest.json"
    manifest: dict[str, Any] = {}
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            manifest = {}
    manifest["."] = version
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def apply_version_bump(repo_dir: Path, next_version: str, release_notes: str) -> list[Path]:
    """Update all version files, release manifest, and CHANGELOG.md."""
    sys.path.insert(0, str(repo_dir))
    try:
        from scripts.versioning import set_version
        set_version(next_version)
    finally:
        if str(repo_dir) in sys.path:
            sys.path.remove(str(repo_dir))

    update_release_please_manifest(repo_dir, next_version)
    update_changelog(repo_dir, release_notes)

    changed = [
        repo_dir / "pyproject.toml",
        repo_dir / ".release-please-manifest.json",
        repo_dir / "CHANGELOG.md",
    ]
    server_json = repo_dir / "server.json"
    if server_json.is_file():
        changed.append(server_json)
    return changed


def set_github_output(outputs: dict[str, Any]) -> None:
    """Export variables to $GITHUB_OUTPUT if running inside GitHub Actions."""
    gh_output_path = os.environ.get("GITHUB_OUTPUT")
    if not gh_output_path:
        return
    with open(gh_output_path, "a", encoding="utf-8") as f:
        for key, value in outputs.items():
            if isinstance(value, bool):
                val_str = "true" if value else "false"
            else:
                val_str = str(value)
            f.write(f"{key}={val_str}\n")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Orchestrate direct GitHub Releases from merged PRs."
    )
    parser.add_argument(
        "--repo-dir",
        type=Path,
        default=Path.cwd(),
        help="Path to repository root (default: cwd)",
    )
    parser.add_argument(
        "--repo-url",
        default="https://github.com/Entigram-AI/entigram",
        help="GitHub repository web URL for links",
    )
    parser.add_argument(
        "--plan",
        action="store_true",
        help="Plan release without modifying files (default)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply version bump, update manifest, and prepend release notes to CHANGELOG.md",
    )
    parser.add_argument(
        "--bump",
        choices=["auto", "patch", "minor", "major"],
        default="auto",
        help="Override bump type (default: auto)",
    )
    parser.add_argument(
        "--notes-file",
        type=Path,
        help="Write compiled release notes to this file",
    )
    parser.add_argument(
        "--set-github-output",
        action="store_true",
        help="Export plan outputs to GITHUB_OUTPUT environment file",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    repo_dir = args.repo_dir.resolve()

    latest_tag = get_latest_tag(repo_dir)
    current_version = get_current_project_version(repo_dir)
    commits = get_commits_since_tag(repo_dir, latest_tag)

    bump_override = None if args.bump == "auto" else args.bump
    bump_type, release_needed = determine_bump_type(commits, bump_override)

    if release_needed and bump_type:
        next_version = bump_version(current_version, bump_type)
    else:
        next_version = current_version

    tag_name = f"v{next_version}"
    release_notes = compile_release_notes(
        commits=commits,
        prev_tag=latest_tag,
        next_version=next_version,
        repo_url=args.repo_url,
    )

    if args.notes_file:
        args.notes_file.parent.mkdir(parents=True, exist_ok=True)
        args.notes_file.write_text(release_notes, encoding="utf-8")

    plan_summary = {
        "latest_tag": latest_tag,
        "current_version": current_version,
        "next_version": next_version,
        "tag_name": tag_name,
        "bump_type": bump_type,
        "release_needed": release_needed,
        "commits_count": len(commits),
    }

    if args.set_github_output:
        set_github_output({
            "release_needed": release_needed,
            "current_version": current_version,
            "next_version": next_version,
            "tag_name": tag_name,
            "bump_type": bump_type or "none",
            "latest_tag": latest_tag or "",
            "commits_count": len(commits),
        })

    if args.apply:
        if not release_needed:
            print("No release needed based on commits since latest tag.")
            return 0
        changed_files = apply_version_bump(repo_dir, next_version, release_notes)
        print(f"Applied release {next_version} ({bump_type}). Updated files:")
        for path in changed_files:
            print(f"  - {path.relative_to(repo_dir)}")
        return 0

    # Default output: print JSON plan
    print(json.dumps(plan_summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
