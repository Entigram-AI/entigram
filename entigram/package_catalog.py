import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


@dataclass
class PackageSuggestion:
    name: str
    score: float
    title: str
    description: str
    adapters: List[str]
    source_kinds: List[str]
    assessment_adapters: List[str] = field(default_factory=list)
    security_capabilities: List[str] = field(default_factory=list)


@dataclass
class PackageCatalogIssue:
    package: str
    field: str
    message: str


def load_package_catalog(path: str) -> Dict[str, Any]:
    catalog_path = Path(path).expanduser()
    if not catalog_path.is_file():
        raise FileNotFoundError(f"package catalog not found: {path}")
    catalog = json.loads(catalog_path.read_text())
    if not isinstance(catalog, dict) or not isinstance(catalog.get("packages"), list):
        raise ValueError("package catalog must contain a packages list")
    return catalog


def validate_package_catalog(catalog: Dict[str, Any]) -> List[PackageCatalogIssue]:
    issues = []
    if not isinstance(catalog, dict):
        return [PackageCatalogIssue("<catalog>", "catalog", "package catalog must be an object")]
    packages = catalog.get("packages")
    if not isinstance(packages, list):
        return [PackageCatalogIssue("<catalog>", "packages", "package catalog must contain a packages list")]

    seen_names = set()
    for package in packages:
        if not isinstance(package, dict):
            issues.append(PackageCatalogIssue("<unknown>", "package", "package entry must be an object"))
            continue
        name = package.get("name", "<unknown>")
        if name in seen_names:
            issues.append(PackageCatalogIssue(name, "name", "package names must be unique"))
        seen_names.add(name)
        issues.extend(_validate_package_entry(package, name))
    return issues


def format_package_catalog_issues(issues: Iterable[PackageCatalogIssue]) -> str:
    lines = []
    for issue in issues:
        lines.append(f"{issue.package}: {issue.field}: {issue.message}")
    return "\n".join(lines)


def suggest_packages(catalog: Dict[str, Any], query: str, limit: int = 5) -> List[PackageSuggestion]:
    terms = _tokenize(query)
    suggestions = []
    for package in catalog.get("packages", []):
        score = _score_package(package, terms)
        if score <= 0:
            continue
        suggestions.append(
            PackageSuggestion(
                name=package.get("name", ""),
                score=round(score, 3),
                title=package.get("title", ""),
                description=package.get("description", ""),
                adapters=list(package.get("adapters", [])),
                source_kinds=list(package.get("source_kinds", [])),
                assessment_adapters=list(package.get("assessment_adapters", [])),
                security_capabilities=list(package.get("security_capabilities", [])),
            )
        )
    suggestions.sort(key=lambda suggestion: (-suggestion.score, suggestion.name))
    return suggestions[:limit]


def format_package_suggestions(suggestions: Iterable[PackageSuggestion]) -> str:
    lines = []
    for suggestion in suggestions:
        adapter_text = ", ".join(suggestion.adapters) if suggestion.adapters else "none"
        source_text = ", ".join(suggestion.source_kinds) if suggestion.source_kinds else "unspecified"
        assessment_text = ", ".join(suggestion.assessment_adapters) if suggestion.assessment_adapters else "none"
        capability_text = ", ".join(suggestion.security_capabilities) if suggestion.security_capabilities else "none"
        lines.append(f"{suggestion.name} ({suggestion.score:.3f})")
        lines.append(f"  {suggestion.title}")
        lines.append(f"  {suggestion.description}")
        lines.append(f"  adapters: {adapter_text}")
        lines.append(f"  sources: {source_text}")
        lines.append(f"  assessment adapters: {assessment_text}")
        lines.append(f"  security capabilities: {capability_text}")
    return "\n".join(lines)


def _score_package(package: Dict[str, Any], terms: List[str]) -> float:
    if not terms:
        return 0.0
    weighted_fields = [
        (package.get("name", ""), 4.0),
        (package.get("title", ""), 3.0),
        (package.get("description", ""), 2.0),
        (" ".join(package.get("tags", [])), 2.5),
        (" ".join(package.get("source_kinds", [])), 3.0),
        (" ".join(package.get("adapters", [])), 3.0),
        (" ".join(package.get("assessment_adapters", [])), 3.0),
        (" ".join(package.get("security_capabilities", [])), 3.0),
    ]
    score = 0.0
    for text, weight in weighted_fields:
        tokens = set(_tokenize(text))
        for term in terms:
            if term in tokens:
                score += weight
            elif any(token.startswith(term) or term.startswith(token) for token in tokens):
                score += weight * 0.5
    return score / len(terms)


def _tokenize(value: Optional[str]) -> List[str]:
    if not value:
        return []
    return [token for token in re.split(r"[^a-z0-9]+", value.lower()) if token]


def _validate_package_entry(package: Dict[str, Any], name: str) -> List[PackageCatalogIssue]:
    issues = []
    required_text_fields = ["name", "title", "description"]
    for field in required_text_fields:
        if not isinstance(package.get(field), str) or not package.get(field):
            issues.append(PackageCatalogIssue(name, field, "must be a non-empty string"))

    # adapter_module is only required when present (domain-only packages have no adapter).
    if "adapter_module" in package:
        if not isinstance(package["adapter_module"], str) or not package["adapter_module"]:
            issues.append(PackageCatalogIssue(name, "adapter_module", "must be a non-empty string"))

    if "assessment_module" in package:
        if not isinstance(package["assessment_module"], str) or not package["assessment_module"]:
            issues.append(PackageCatalogIssue(name, "assessment_module", "must be a non-empty string"))
        elif (
            Path(package["assessment_module"]).is_absolute()
            or ".." in Path(package["assessment_module"]).parts
            or not package["assessment_module"].endswith(".py")
        ):
            issues.append(
                PackageCatalogIssue(
                    name,
                    "assessment_module",
                    "must be a safe relative Python module path",
                )
            )
        for field in ["assessment_adapters", "security_capabilities"]:
            value = package.get(field)
            if not isinstance(value, list) or not value or not all(
                isinstance(item, str) and item for item in value
            ):
                issues.append(PackageCatalogIssue(name, field, "must be a non-empty list of strings"))
        adapters = package.get("assessment_adapters")
        if isinstance(adapters, list) and any(
            not isinstance(item, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,127}", item)
            for item in adapters
        ):
            issues.append(
                PackageCatalogIssue(
                    name,
                    "assessment_adapters",
                    "values must be safe adapter identifiers",
                )
            )
        capabilities = package.get("security_capabilities")
        if isinstance(capabilities, list):
            invalid = [
                item for item in capabilities
                if not isinstance(item, str) or not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,127}/v[1-9][0-9]*", item)
            ]
            if invalid:
                issues.append(
                    PackageCatalogIssue(
                        name,
                        "security_capabilities",
                        "values must use the form capability-name/v1",
                    )
                )
    elif "assessment_adapters" in package or "security_capabilities" in package:
        issues.append(
            PackageCatalogIssue(
                name,
                "assessment_module",
                "is required when assessment metadata is declared",
            )
        )

    # tags are always required; source_kinds and adapters only when the package declares an adapter.
    tags_value = package.get("tags")
    if not isinstance(tags_value, list) or not tags_value or not all(isinstance(t, str) and t for t in tags_value):
        issues.append(PackageCatalogIssue(name, "tags", "must be a non-empty list of strings"))

    for field in ["source_kinds", "adapters"]:
        value = package.get(field)
        if value is not None:
            if not isinstance(value, list) or not value or not all(isinstance(item, str) and item for item in value):
                issues.append(PackageCatalogIssue(name, field, "must be a non-empty list of strings"))

    issues.extend(_validate_license(package.get("license"), name))
    issues.extend(_validate_publisher(package.get("publisher"), name))
    issues.extend(_validate_provenance(package.get("provenance"), name))
    issues.extend(_validate_certification(package.get("certification"), name))
    return issues


def _validate_license(value: Any, name: str) -> List[PackageCatalogIssue]:
    issues = []
    if not isinstance(value, dict):
        return [PackageCatalogIssue(name, "license", "must be an object")]
    if not isinstance(value.get("spdx"), str) or not value.get("spdx"):
        issues.append(PackageCatalogIssue(name, "license.spdx", "must be a non-empty SPDX identifier"))
    if value.get("notice_required") is not True:
        issues.append(PackageCatalogIssue(name, "license.notice_required", "must be true for standard packages"))
    return issues


def _validate_publisher(value: Any, name: str) -> List[PackageCatalogIssue]:
    issues = []
    if not isinstance(value, dict):
        return [PackageCatalogIssue(name, "publisher", "must be an object")]
    for field in ["name", "namespace"]:
        if not isinstance(value.get(field), str) or not value.get(field):
            issues.append(PackageCatalogIssue(name, f"publisher.{field}", "must be a non-empty string"))
    return issues


def _validate_provenance(value: Any, name: str) -> List[PackageCatalogIssue]:
    issues = []
    if not isinstance(value, dict):
        return [PackageCatalogIssue(name, "provenance", "must be an object")]
    for field in ["source_repository", "package_path", "release_channel"]:
        if not isinstance(value.get(field), str) or not value.get(field):
            issues.append(PackageCatalogIssue(name, f"provenance.{field}", "must be a non-empty string"))
    if value.get("signed") is not True:
        issues.append(PackageCatalogIssue(name, "provenance.signed", "must be true for catalog-tracked packages"))
    return issues


def _validate_certification(value: Any, name: str) -> List[PackageCatalogIssue]:
    issues = []
    if not isinstance(value, dict):
        return [PackageCatalogIssue(name, "certification", "must be an object")]
    status = value.get("status")
    if status not in {"community", "certified", "premium"}:
        issues.append(PackageCatalogIssue(name, "certification.status", "must be community, certified, or premium"))
    if not isinstance(value.get("compatibility"), str) or not value.get("compatibility"):
        issues.append(PackageCatalogIssue(name, "certification.compatibility", "must be a non-empty string"))
    evidence = value.get("test_evidence")
    if not isinstance(evidence, list) or not evidence or not all(isinstance(item, str) and item for item in evidence):
        issues.append(PackageCatalogIssue(name, "certification.test_evidence", "must be a non-empty list of strings"))
    trademark = value.get("trademark_use")
    if trademark not in {"none", "nominative", "certified"}:
        issues.append(PackageCatalogIssue(name, "certification.trademark_use", "must be none, nominative, or certified"))
    return issues
