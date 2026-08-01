"""Assessment adapters and capability-aware workspace risk advisories."""

from __future__ import annotations

import importlib.util
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Type


_ADAPTER_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,127}$")
_SUBJECT_TYPE_RE = re.compile(r"^[a-z][a-z0-9._-]{0,127}$")
_CAPABILITY_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}/v[1-9][0-9]*$")
_FINDING_CODE_RE = re.compile(r"^[A-Z][A-Z0-9_-]{1,127}$")
_SEVERITIES = {"info", "low", "medium", "high", "critical"}
_SEVERITY_RANK = {"info": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}
_MODES = {"off", "advisory", "enforce"}
_TRUST_LEVELS = {"trusted", "untrusted"}


class AssessmentConfigurationError(ValueError):
    """Raised when workspace assessment configuration is malformed."""


@dataclass(frozen=True)
class AssessmentSubject:
    subject_type: str
    ref: str
    data: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.subject_type, str) or not _SUBJECT_TYPE_RE.fullmatch(self.subject_type):
            raise ValueError("subject_type must be a lowercase safe identifier")
        if not isinstance(self.ref, str) or not self.ref.strip():
            raise ValueError("subject ref must be a non-empty string")
        if len(self.ref) > 2048:
            raise ValueError("subject ref exceeds 2048 characters")
        if not isinstance(self.data, dict):
            raise ValueError("subject data must be an object")

    def to_dict(self) -> Dict[str, Any]:
        return {"type": self.subject_type, "ref": self.ref}


@dataclass(frozen=True)
class AssessmentFinding:
    code: str
    severity: str
    title: str
    message: str
    framework_refs: List[str] = field(default_factory=list)
    evidence: Dict[str, Any] = field(default_factory=dict)
    recommendation: str = ""
    confidence: float = 1.0

    def __post_init__(self) -> None:
        if not isinstance(self.code, str) or not _FINDING_CODE_RE.fullmatch(self.code):
            raise ValueError("finding code must be an uppercase safe identifier")
        if self.severity not in _SEVERITIES:
            raise ValueError(f"finding severity must be one of {sorted(_SEVERITIES)}")
        if not isinstance(self.title, str) or not self.title.strip():
            raise ValueError("finding title must be a non-empty string")
        if not isinstance(self.message, str) or not self.message.strip():
            raise ValueError("finding message must be a non-empty string")
        if not isinstance(self.framework_refs, list) or not all(
            isinstance(value, str) and value for value in self.framework_refs
        ):
            raise ValueError("framework_refs must be a list of non-empty strings")
        if not isinstance(self.evidence, dict):
            raise ValueError("finding evidence must be an object")
        if not isinstance(self.recommendation, str):
            raise ValueError("finding recommendation must be a string")
        if isinstance(self.confidence, bool) or not isinstance(self.confidence, (int, float)):
            raise ValueError("finding confidence must be a number")
        if not 0.0 <= float(self.confidence) <= 1.0:
            raise ValueError("finding confidence must be between 0 and 1")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "title": self.title,
            "message": self.message,
            "framework_refs": list(self.framework_refs),
            "evidence": self.evidence,
            "recommendation": self.recommendation,
            "confidence": float(self.confidence),
        }


@dataclass(frozen=True)
class AssessmentResult:
    adapter: str
    subject: AssessmentSubject
    capabilities: List[str]
    findings: List[AssessmentFinding] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _validate_adapter_name(self.adapter)
        _validate_capabilities(self.capabilities, "result capabilities")
        if not isinstance(self.findings, list) or not all(
            isinstance(value, AssessmentFinding) for value in self.findings
        ):
            raise ValueError("findings must contain AssessmentFinding values")
        if not isinstance(self.metadata, dict):
            raise ValueError("assessment metadata must be an object")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "adapter": self.adapter,
            "subject": self.subject.to_dict(),
            "capabilities": sorted(set(self.capabilities)),
            "findings": [finding.to_dict() for finding in self.findings],
            "metadata": self.metadata,
        }


class AssessmentAdapter:
    """Base class for package-provided, read-only assessments."""

    name = "base"
    capabilities: Sequence[str] = ()

    def assess(self, subject: AssessmentSubject) -> AssessmentResult:
        raise NotImplementedError


_ADAPTERS: Dict[str, Type[AssessmentAdapter]] = {}
_LOADED_MODULES: Dict[Path, List[str]] = {}


def register_assessment_adapter(name: str, adapter_cls: Type[AssessmentAdapter]) -> None:
    _validate_adapter_name(name)
    if not isinstance(adapter_cls, type) or not issubclass(adapter_cls, AssessmentAdapter):
        raise TypeError("adapter_cls must inherit AssessmentAdapter")
    if adapter_cls.name != name:
        raise ValueError("adapter class name must match its registered name")
    _validate_capabilities(list(adapter_cls.capabilities), "adapter capabilities")
    existing = _ADAPTERS.get(name)
    if existing is not None and existing is not adapter_cls:
        raise ValueError(f"assessment adapter is already registered: {name}")
    _ADAPTERS[name] = adapter_cls


def load_assessment_adapter_module(module_path: str, *, replace_existing: bool = False) -> List[str]:
    path = Path(module_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"assessment adapter module not found: {module_path}")
    if path in _LOADED_MODULES:
        return list(_LOADED_MODULES[path])

    module_name = f"entigram_dynamic_assessment_adapter_{abs(hash(path))}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"could not load assessment adapter module: {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not hasattr(module, "register"):
        raise ValueError("assessment adapter module must define register(register_assessment_adapter)")

    registered_names = []

    def register(name: str, adapter_cls: Type[AssessmentAdapter]) -> None:
        if replace_existing:
            _validate_adapter_name(name)
            if not isinstance(adapter_cls, type) or not issubclass(adapter_cls, AssessmentAdapter):
                raise TypeError("adapter_cls must inherit AssessmentAdapter")
            if adapter_cls.name != name:
                raise ValueError("adapter class name must match its registered name")
            _validate_capabilities(list(adapter_cls.capabilities), "adapter capabilities")
            _ADAPTERS[name] = adapter_cls
        else:
            register_assessment_adapter(name, adapter_cls)
        registered_names.append(name)

    module.register(register)
    registered = sorted(set(registered_names))
    if not registered:
        raise ValueError("assessment adapter module did not register any adapters")
    _LOADED_MODULES[path] = registered
    return list(registered)


def available_assessment_adapters() -> List[str]:
    return sorted(_ADAPTERS)


def assessment_adapter_capabilities(name: str) -> List[str]:
    adapter_cls = _ADAPTERS.get(name)
    if adapter_cls is None:
        raise ValueError(f"Unknown assessment adapter: {name}")
    return sorted(set(adapter_cls.capabilities))


def assess_subject(adapter: str, subject: AssessmentSubject) -> AssessmentResult:
    adapter_cls = _ADAPTERS.get(adapter)
    if adapter_cls is None:
        raise ValueError(f"Unknown assessment adapter: {adapter}")
    result = adapter_cls().assess(subject)
    if not isinstance(result, AssessmentResult):
        raise TypeError("assessment adapter must return AssessmentResult")
    if result.adapter != adapter:
        raise ValueError("assessment result adapter does not match requested adapter")
    declared = set(adapter_cls.capabilities)
    reported = set(result.capabilities)
    if not reported.issubset(declared):
        raise ValueError("assessment result reports undeclared capabilities")
    return result


def assessment_decision(
    result: AssessmentResult,
    security_posture: Mapping[str, Any],
) -> Dict[str, Any]:
    """Return a conservative safety decision distinct from execution success."""
    if not isinstance(result, AssessmentResult):
        raise TypeError("result must be an AssessmentResult")
    if not isinstance(security_posture, Mapping):
        raise TypeError("security_posture must be an object")

    severities = [finding.severity for finding in result.findings]
    max_severity = max(severities, key=_SEVERITY_RANK.get) if severities else "none"
    valid = security_posture.get("valid") is True
    enforcement_blocked = security_posture.get("enforcement_blocked") is True
    active = security_posture.get("active") is True
    required = set(security_posture.get("required_capabilities") or [])
    missing = set(security_posture.get("missing_capabilities") or [])
    exercised = set(result.capabilities)
    unassessed = sorted(required - missing - exercised) if active else []

    reason_codes = []
    if not valid:
        reason_codes.append("INVALID_SECURITY_POSTURE")
    if enforcement_blocked:
        reason_codes.append("POLICY_ENFORCEMENT_BLOCKED")
    if missing:
        reason_codes.append("MISSING_REQUIRED_CAPABILITY")
    if unassessed:
        reason_codes.append("REQUIRED_CAPABILITY_NOT_ASSESSED")
    if max_severity == "critical":
        reason_codes.append("CRITICAL_FINDING")
    elif max_severity == "high":
        reason_codes.append("HIGH_FINDING")
    elif result.findings:
        reason_codes.append("FINDING_REQUIRES_REVIEW")

    if not valid or enforcement_blocked or max_severity == "critical":
        decision = "blocked"
        recommended_action = (
            "Do not process the subject. Keep it isolated until the blocking "
            "configuration, capability, or critical finding is resolved."
        )
    elif missing or unassessed or result.findings:
        decision = "review_required"
        recommended_action = (
            "Keep the subject isolated and require human review before any "
            "artifact-derived output can trigger a state-changing action."
        )
    else:
        decision = "allow"
        recommended_action = (
            "No assessment finding or required-capability gap was reported; "
            "continue applying the workspace's standard trust controls."
        )

    return {
        "decision": decision,
        "safe_to_process": decision == "allow",
        "human_review_required": decision != "allow",
        "max_severity": max_severity,
        "reason_codes": reason_codes,
        "required_capabilities_unassessed": unassessed,
        "recommended_action": recommended_action,
    }


def load_installed_assessment_adapters(target_dir: Path) -> Dict[str, Any]:
    """Discover assessment metadata without executing workspace package code.

    Package signatures currently prove artifact integrity, not that the signer is
    an Entigram-trusted publisher. Until publisher trust and process isolation
    are available, installed adapters are intentionally reported as unavailable.
    An operator may still review and run a local module through the explicit CLI
    acknowledgement path.
    """

    from entigram.package_signing import verify_package

    root = Path(target_dir).expanduser().resolve()
    packages_root = (root / ".etg" / "packages").resolve()
    loaded: List[Dict[str, Any]] = []
    excluded: List[Dict[str, str]] = []
    if not packages_root.is_dir():
        return {"adapters": [], "capabilities": [], "packages": [], "excluded": []}

    for manifest_path in sorted(packages_root.rglob("package.manifest.json")):
        package_dir = manifest_path.parent.resolve()
        try:
            if package_dir != packages_root and packages_root not in package_dir.parents:
                raise ValueError("signed package path escapes the workspace package directory")
            verification = verify_package(str(package_dir), require_signature=True)
            manifest = json.loads(manifest_path.read_text())
            package_name = manifest.get("package") or package_dir.name
            if not verification.ok:
                excluded.append({"package": package_name, "reason": "; ".join(verification.errors)})
                continue
            metadata = manifest.get("metadata") or {}
            module_ref = metadata.get("assessment_module")
            declared_adapters = metadata.get("assessment_adapters") or []
            declared_capabilities = metadata.get("security_capabilities") or []
            if not module_ref:
                continue
            _validate_string_list(declared_adapters, "assessment_adapters")
            _validate_capabilities(declared_capabilities, "security_capabilities")
            module_path = _resolve_package_module(package_dir, package_name, module_ref)
            loaded.append(
                {
                    "package": package_name,
                    "adapters": sorted(declared_adapters),
                    "capabilities": sorted(declared_capabilities),
                    "module": str(module_path.relative_to(package_dir)),
                    "executable": False,
                }
            )
            excluded.append(
                {
                    "package": package_name,
                    "reason": (
                        "executable assessment adapters are disabled until trusted "
                        "publisher verification and process isolation are configured"
                    ),
                }
            )
        except Exception as exc:
            package_name = package_dir.name
            try:
                package_name = json.loads(manifest_path.read_text()).get("package") or package_name
            except Exception:
                pass
            excluded.append({"package": package_name, "reason": str(exc)})

    return {
        "adapters": [],
        "capabilities": [],
        "packages": loaded,
        "excluded": excluded,
    }


def assess_with_installed_adapter(
    target_dir: Path,
    adapter: str,
    subject: AssessmentSubject,
) -> AssessmentResult:
    installed = load_installed_assessment_adapters(target_dir)
    if adapter not in installed["adapters"]:
        raise ValueError(
            "installed assessment package execution is disabled; review the module and "
            "use CLI --adapter-module with --allow-executable-adapter for an explicit local run"
        )
    return assess_subject(adapter, subject)


def workspace_security_posture(
    target_dir: Path,
    *,
    provided_capabilities: Optional[Iterable[str]] = None,
) -> Dict[str, Any]:
    root = Path(target_dir).expanduser().resolve()
    manifest_path = root / ".etg" / "entigram.yaml"
    if not manifest_path.is_file():
        return _inactive_posture(root)

    try:
        import yaml

        manifest = yaml.safe_load(manifest_path.read_text()) or {}
        if not isinstance(manifest, dict):
            raise AssessmentConfigurationError("workspace manifest must be an object")
        if provided_capabilities is None:
            installed = load_installed_assessment_adapters(root)
            capabilities = installed["capabilities"]
        else:
            capabilities = sorted(set(provided_capabilities))
            _validate_capabilities(capabilities, "provided capabilities", allow_empty=True)
        return compute_security_posture(manifest, capabilities, root=root)
    except AssessmentConfigurationError as exc:
        return _invalid_posture(str(exc))


def compute_security_posture(
    manifest: Mapping[str, Any],
    provided_capabilities: Iterable[str],
    *,
    root: Optional[Path] = None,
) -> Dict[str, Any]:
    config = manifest.get("external_artifacts")
    if config is None:
        return _inactive_posture(root)
    if not isinstance(config, dict):
        raise AssessmentConfigurationError("external_artifacts must be an object")

    allowed_keys = {"modalities", "trust", "mode", "required_capabilities"}
    unknown = sorted(set(config) - allowed_keys)
    if unknown:
        raise AssessmentConfigurationError(f"external_artifacts contains unknown fields: {', '.join(unknown)}")

    modalities = config.get("modalities")
    _validate_string_list(modalities, "external_artifacts.modalities")
    if any(not _SUBJECT_TYPE_RE.fullmatch(value) for value in modalities):
        raise AssessmentConfigurationError("external_artifacts.modalities contains an invalid identifier")

    trust = config.get("trust")
    if trust not in _TRUST_LEVELS:
        raise AssessmentConfigurationError(f"external_artifacts.trust must be one of {sorted(_TRUST_LEVELS)}")
    mode = config.get("mode", "advisory")
    if mode not in _MODES:
        raise AssessmentConfigurationError(f"external_artifacts.mode must be one of {sorted(_MODES)}")
    required = config.get("required_capabilities")
    _validate_capabilities(required, "external_artifacts.required_capabilities")
    provided = sorted(set(provided_capabilities))
    _validate_capabilities(provided, "provided capabilities", allow_empty=True)

    active = mode != "off" and trust == "untrusted"
    missing = sorted(set(required) - set(provided)) if active else []
    advisories = [_missing_capability_advisory(value, modalities, mode) for value in missing]
    return {
        "configured": True,
        "valid": True,
        "active": active,
        "mode": mode,
        "trust": trust,
        "modalities": sorted(set(modalities)),
        "required_capabilities": sorted(set(required)),
        "provided_capabilities": provided,
        "missing_capabilities": missing,
        "enforcement_blocked": mode == "enforce" and bool(missing),
        "advisories": advisories,
    }


def _inactive_posture(root: Optional[Path] = None) -> Dict[str, Any]:
    advisories = []
    if root is not None:
        detected = detect_workspace_technologies(root)
        advisories = _technology_advisories(detected)
    return {
        "configured": False,
        "valid": True,
        "active": False,
        "mode": "off",
        "required_capabilities": [],
        "provided_capabilities": [],
        "missing_capabilities": [],
        "enforcement_blocked": False,
        "advisories": advisories,
        "detected_technologies": (
            [t["technology"] for t in (detected if root is not None else [])]
        ),
    }


# ---------------------------------------------------------------------------
# Workspace technology detection
# ---------------------------------------------------------------------------

_TECHNOLOGY_SIGNALS: List[Dict[str, Any]] = [
    {
        "technology": "web-frontend",
        "label": "Web Frontend",
        "signals": ["package.json", "next.config.js", "next.config.mjs", "next.config.ts",
                    "vite.config.js", "vite.config.ts", "angular.json", "nuxt.config.ts"],
        "frameworks": ["OWASP Top 10", "OWASP ASVS"],
        "recommended_checks": [
            "Cross-site scripting (XSS) prevention",
            "Content Security Policy (CSP) headers",
            "Dependency vulnerability scanning (npm audit)",
            "Client-side input validation",
        ],
    },
    {
        "technology": "web-api",
        "label": "Web API / Backend",
        "signals": ["app.py", "main.py", "manage.py", "server.py",
                    "requirements.txt", "Gemfile", "go.mod", "pom.xml",
                    "build.gradle", "Cargo.toml"],
        "frameworks": ["OWASP Top 10", "OWASP API Security Top 10"],
        "recommended_checks": [
            "Authentication and authorization controls",
            "Input validation and injection prevention",
            "Rate limiting and abuse prevention",
            "Secrets management (no hardcoded credentials)",
        ],
    },
    {
        "technology": "container",
        "label": "Container / Infrastructure",
        "signals": ["Dockerfile", "docker-compose.yml", "docker-compose.yaml",
                    "kubernetes", "k8s", "helm"],
        "frameworks": ["CIS Docker Benchmark", "OWASP Docker Security"],
        "recommended_checks": [
            "Base image provenance and vulnerability scanning",
            "Least-privilege container configuration",
            "No secrets baked into images",
            "Network policy and resource limits",
        ],
    },
    {
        "technology": "infrastructure-as-code",
        "label": "Infrastructure as Code",
        "signals": ["main.tf", "terraform", "pulumi", "cloudformation",
                    "cdk.json", "serverless.yml"],
        "frameworks": ["CIS Cloud Benchmarks", "NIST 800-53"],
        "recommended_checks": [
            "Least-privilege IAM policies",
            "Encryption at rest and in transit",
            "Network segmentation and security groups",
            "State file protection",
        ],
    },
    {
        "technology": "mobile-app",
        "label": "Mobile Application",
        "signals": ["android", "ios", "AndroidManifest.xml",
                    "Info.plist", "pubspec.yaml", "expo"],
        "frameworks": ["OWASP MASVS", "OWASP Mobile Top 10"],
        "recommended_checks": [
            "Secure local storage",
            "Certificate pinning",
            "Sensitive data exposure in logs",
            "Binary protection and tamper detection",
        ],
    },
]


def detect_workspace_technologies(root: Path) -> List[Dict[str, Any]]:
    """Detect workspace technologies by checking for signal files/directories."""
    detected = []
    for tech in _TECHNOLOGY_SIGNALS:
        matched_signals = []
        for signal in tech["signals"]:
            candidate = root / signal
            if candidate.exists():
                matched_signals.append(signal)
        if matched_signals:
            detected.append({
                "technology": tech["technology"],
                "label": tech["label"],
                "matched_signals": matched_signals,
                "frameworks": tech["frameworks"],
                "recommended_checks": tech["recommended_checks"],
            })
    return detected


def _technology_advisories(detected: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Generate informational advisories for detected technologies without security config."""
    advisories = []
    for tech in detected:
        advisories.append({
            "code": "ETG-RISK-UNCONFIGURED-TECHNOLOGY",
            "severity": "info",
            "technology": tech["technology"],
            "label": tech["label"],
            "matched_signals": tech["matched_signals"],
            "message": (
                f"Workspace contains {tech['label'].lower()} artifacts but no "
                f"security assessment configuration. Relevant frameworks: "
                f"{', '.join(tech['frameworks'])}."
            ),
            "recommended_checks": tech["recommended_checks"],
            "free_mitigations": [
                f"Configure external_artifacts in .etg/entigram.yaml to enable "
                f"assessment-driven security posture for {tech['label'].lower()} workloads.",
                f"Review {tech['frameworks'][0]} guidelines for your technology stack.",
                "Run dependency and vulnerability scanning as part of your CI pipeline.",
            ],
            "compatible_frameworks": tech["frameworks"],
        })
    return advisories


def _invalid_posture(detail: str) -> Dict[str, Any]:
    return {
        "configured": True,
        "valid": False,
        "active": False,
        "mode": "invalid",
        "required_capabilities": [],
        "provided_capabilities": [],
        "missing_capabilities": [],
        "enforcement_blocked": True,
        "advisories": [
            {
                "code": "ETG-RISK-CONFIG-INVALID",
                "severity": "high",
                "message": f"Artifact-risk configuration is invalid: {detail}",
                "free_mitigations": [
                    "Correct .etg/entigram.yaml before processing untrusted artifacts.",
                ],
                "compatible_packages": [],
            }
        ],
    }


def _missing_capability_advisory(capability: str, modalities: Sequence[str], mode: str) -> Dict[str, Any]:
    providers = {
        "artifact-reputation/v1": ["@entigram/artifact-risk"],
        "cyber-framework-assessment/v1": ["@entigram/cyber-risk-frameworks"],
        "incident-readiness-assessment/v1": ["@entigram/incident-resilience"],
    }
    mitigations = [
        "Treat artifact-derived content as untrusted data, never as agent instructions.",
        "Use read-only tooling and an isolated processing environment.",
        "Require human approval before artifact-derived output can trigger state-changing actions.",
    ]
    if capability == "artifact-reputation/v1":
        mitigations.insert(0, "Hash artifacts locally and avoid uploading sensitive files to public analysis services.")
    return {
        "code": "ETG-RISK-MISSING-CAPABILITY",
        "severity": "high" if mode == "enforce" else "medium",
        "capability": capability,
        "modalities": sorted(set(modalities)),
        "message": f"No signed installed assessment package provides {capability}.",
        "free_mitigations": mitigations,
        "compatible_packages": providers.get(capability, []),
        "provider_note": "Any signed community or third-party package may satisfy this open capability contract.",
    }


def _resolve_package_module(package_dir: Path, package_name: str, module_ref: Any) -> Path:
    if not isinstance(module_ref, str) or not module_ref:
        raise ValueError("assessment_module must be a non-empty string")
    relative = module_ref
    prefix = package_name.rstrip("/") + "/"
    if relative.startswith(prefix):
        relative = relative[len(prefix):]
    candidate = (package_dir / relative).resolve()
    if candidate != package_dir and package_dir not in candidate.parents:
        raise ValueError("assessment_module escapes the signed package directory")
    if not candidate.is_file():
        raise FileNotFoundError(f"assessment module not found in signed package: {relative}")
    return candidate


def _validate_adapter_name(name: Any) -> None:
    if not isinstance(name, str) or not _ADAPTER_NAME_RE.fullmatch(name):
        raise ValueError("adapter name must be a safe identifier")


def _validate_string_list(value: Any, field_name: str) -> None:
    if not isinstance(value, list) or not value or not all(isinstance(item, str) and item for item in value):
        raise AssessmentConfigurationError(f"{field_name} must be a non-empty list of strings")


def _validate_capabilities(value: Any, field_name: str, *, allow_empty: bool = False) -> None:
    if allow_empty and value == []:
        return
    _validate_string_list(value, field_name)
    if any(not _CAPABILITY_RE.fullmatch(item) for item in value):
        raise AssessmentConfigurationError(
            f"{field_name} values must use the form capability-name/v1"
        )
