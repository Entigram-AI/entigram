"""Versioned governance scorecard evaluation for Entigram implementations.

The scorecard is deliberately evidence-oriented. It scores implementation
capabilities, not claims: a report identifies a versioned rubric, assigns each
dimension a 0--5 rating, and points to artifacts that a reviewer can inspect.
That makes the same profile usable for Entigram releases and alternative
implementations without conflating a self-assessment with an independent audit.
"""

import json
from pathlib import Path
from typing import Any, Dict, List


class GovernanceBenchmarkError(ValueError):
    """Raised when a benchmark profile or evidence report is malformed."""


def evaluate_governance_report(report_path: Path) -> Dict[str, Any]:
    """Validate and score an implementation report against its local profile."""
    report_file = Path(report_path).expanduser().resolve()
    report = _load_json_object(report_file, "benchmark report")
    profile_reference = report.get("profile")
    if not isinstance(profile_reference, str) or not profile_reference:
        raise GovernanceBenchmarkError("Benchmark report requires a non-empty 'profile' path.")
    profile_file = (report_file.parent / profile_reference).resolve()
    profile = _load_json_object(profile_file, "benchmark profile")

    dimensions = profile.get("dimensions")
    if not isinstance(dimensions, list) or not dimensions:
        raise GovernanceBenchmarkError("Benchmark profile requires a non-empty dimensions array.")
    expected = {}
    total_weight = 0.0
    for dimension in dimensions:
        if not isinstance(dimension, dict):
            raise GovernanceBenchmarkError("Each benchmark dimension must be an object.")
        identifier = dimension.get("id")
        weight = dimension.get("weight")
        if not isinstance(identifier, str) or not identifier:
            raise GovernanceBenchmarkError("Each benchmark dimension requires an id.")
        if identifier in expected:
            raise GovernanceBenchmarkError(f"Duplicate benchmark dimension: {identifier}")
        if isinstance(weight, bool) or not isinstance(weight, (int, float)) or weight <= 0:
            raise GovernanceBenchmarkError(f"Benchmark dimension {identifier} needs a positive weight.")
        expected[identifier] = dimension
        total_weight += float(weight)
    if round(total_weight, 6) != 100.0:
        raise GovernanceBenchmarkError("Benchmark profile dimension weights must total 100.")

    ratings = report.get("ratings")
    if not isinstance(ratings, dict):
        raise GovernanceBenchmarkError("Benchmark report requires a ratings object.")
    missing = sorted(set(expected) - set(ratings))
    unknown = sorted(set(ratings) - set(expected))
    if missing or unknown:
        details = []
        if missing:
            details.append("missing: " + ", ".join(missing))
        if unknown:
            details.append("unknown: " + ", ".join(unknown))
        raise GovernanceBenchmarkError("Benchmark ratings do not match profile dimensions (" + "; ".join(details) + ").")

    scored_dimensions: List[Dict[str, Any]] = []
    weighted_total = 0.0
    evidenced_dimensions = 0
    for identifier, dimension in expected.items():
        rating = ratings[identifier]
        if not isinstance(rating, dict):
            raise GovernanceBenchmarkError(f"Rating for {identifier} must be an object.")
        score = rating.get("score")
        if isinstance(score, bool) or not isinstance(score, (int, float)) or not 0 <= score <= 5:
            raise GovernanceBenchmarkError(f"Rating for {identifier} must have a score from 0 through 5.")
        evidence = rating.get("evidence", [])
        if not isinstance(evidence, list) or not all(isinstance(item, str) and item for item in evidence):
            raise GovernanceBenchmarkError(f"Rating for {identifier} has invalid evidence.")
        if score >= 3 and not evidence:
            raise GovernanceBenchmarkError(
                f"Rating for {identifier} needs inspectable evidence at score 3 or above."
            )
        if evidence:
            evidenced_dimensions += 1
        contribution = float(dimension["weight"]) * float(score) / 5.0
        weighted_total += contribution
        scored_dimensions.append(
            {
                "id": identifier,
                "title": dimension.get("title", identifier),
                "weight": dimension["weight"],
                "score": score,
                "contribution": round(contribution, 2),
                "evidence": evidence,
            }
        )

    implementation = report.get("implementation", {})
    if not isinstance(implementation, dict):
        raise GovernanceBenchmarkError("Benchmark report implementation must be an object.")
    return {
        "ok": True,
        "profile": {
            "id": profile.get("id"),
            "version": profile.get("version"),
            "path": str(profile_file),
        },
        "implementation": implementation,
        "summary": {
            "governance_score": round(weighted_total, 1),
            "maximum_score": 100.0,
            "evidence_coverage": round(100 * evidenced_dimensions / len(expected), 1),
            "dimension_count": len(expected),
            "assessment": report.get("assessment", "self-assessed"),
        },
        "dimensions": scored_dimensions,
    }


def format_governance_report(result: Dict[str, Any]) -> str:
    """Render a compact human-readable scorecard."""
    summary = result["summary"]
    implementation = result.get("implementation") or {}
    name = implementation.get("name", "Unnamed implementation")
    lines = [
        f"Governance benchmark: {name}",
        f"Score: {summary['governance_score']}/{summary['maximum_score']} "
        f"({summary['assessment']}; evidence coverage {summary['evidence_coverage']}%)",
    ]
    for dimension in result["dimensions"]:
        lines.append(
            f"- {dimension['title']}: {dimension['score']}/5 "
            f"({dimension['contribution']}/{dimension['weight']})"
        )
    return "\n".join(lines)


def _load_json_object(path: Path, label: str) -> Dict[str, Any]:
    try:
        value = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise GovernanceBenchmarkError(f"Unable to read {label} at {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise GovernanceBenchmarkError(f"{label.capitalize()} at {path} must be a JSON object.")
    return value
