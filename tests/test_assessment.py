import json
import shutil
import sys
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import yaml

from entigram.assessment import (
    AssessmentFinding,
    AssessmentResult,
    AssessmentSubject,
    assessment_decision,
    compute_security_posture,
    load_assessment_adapter_module,
    load_installed_assessment_adapters,
)
from entigram.injector import inject_entigram_manifest
from entigram.mcp_service import EntigramMCPService
from entigram.package_signing import (
    create_package_manifest,
    sign_package_manifest,
    write_package_manifest,
)


ADAPTER_SOURCE = """
from entigram.assessment import AssessmentAdapter, AssessmentFinding, AssessmentResult

class SignedDemoRiskAdapter(AssessmentAdapter):
    name = "signed-demo-risk"
    capabilities = ("artifact-reputation/v1",)

    def assess(self, subject):
        return AssessmentResult(
            adapter=self.name,
            subject=subject,
            capabilities=list(self.capabilities),
            findings=[AssessmentFinding(
                code="DEMO_REPUTATION_FOUND",
                severity="medium",
                title="Demo reputation evidence",
                message="The signed demo adapter returned reputation evidence.",
                evidence={"ref": subject.ref},
                recommendation="Review the evidence before acting.",
                confidence=0.8,
            )],
            metadata={"trusted": False},
        )

def register(registry):
    registry(SignedDemoRiskAdapter.name, SignedDemoRiskAdapter)
"""


class TestAssessmentContract(unittest.TestCase):
    def test_dynamic_adapter_registers_and_assesses(self):
        with tempfile.TemporaryDirectory() as directory:
            module_path = Path(directory, "assessment_adapter.py")
            module_path.write_text(ADAPTER_SOURCE.replace("signed-demo-risk", "dynamic-demo-risk"))

            self.assertEqual(load_assessment_adapter_module(str(module_path)), ["dynamic-demo-risk"])
            subject = AssessmentSubject("sha256", "a" * 64)
            from entigram.assessment import assess_subject

            result = assess_subject("dynamic-demo-risk", subject)
            self.assertEqual(result.capabilities, ["artifact-reputation/v1"])
            self.assertEqual(result.findings[0].code, "DEMO_REPUTATION_FOUND")
            self.assertNotIn("data", result.to_dict()["subject"])

    def test_posture_is_inactive_without_artifact_declaration(self):
        posture = compute_security_posture({"packages": {}}, [])
        self.assertFalse(posture["configured"])
        self.assertEqual(posture["advisories"], [])

    def test_advisory_reports_only_missing_capabilities(self):
        manifest = {
            "external_artifacts": {
                "modalities": ["image", "pdf"],
                "trust": "untrusted",
                "mode": "advisory",
                "required_capabilities": [
                    "artifact-reputation/v1",
                    "visual-prompt-injection-screening/v1",
                ],
            }
        }
        posture = compute_security_posture(manifest, ["artifact-reputation/v1"])

        self.assertFalse(posture["enforcement_blocked"])
        self.assertEqual(
            posture["missing_capabilities"],
            ["visual-prompt-injection-screening/v1"],
        )
        self.assertEqual(len(posture["advisories"]), 1)

    def test_enforce_mode_blocks_only_when_coverage_is_missing(self):
        manifest = {
            "external_artifacts": {
                "modalities": ["image"],
                "trust": "untrusted",
                "mode": "enforce",
                "required_capabilities": ["artifact-reputation/v1"],
            }
        }
        self.assertTrue(compute_security_posture(manifest, [])["enforcement_blocked"])
        self.assertFalse(
            compute_security_posture(manifest, ["artifact-reputation/v1"])["enforcement_blocked"]
        )

    def test_malformed_capability_is_rejected(self):
        manifest = {
            "external_artifacts": {
                "modalities": ["image"],
                "trust": "untrusted",
                "required_capabilities": ["artifact-reputation"],
            }
        }
        with self.assertRaisesRegex(ValueError, "capability-name/v1"):
            compute_security_posture(manifest, [])

    def test_assessment_decision_separates_execution_from_safety(self):
        result = AssessmentResult(
            adapter="decision-demo",
            subject=AssessmentSubject("sha256", "a" * 64),
            capabilities=["artifact-reputation/v1"],
            findings=[
                AssessmentFinding(
                    code="DEMO_HIGH_FINDING",
                    severity="high",
                    title="High-risk evidence",
                    message="The assessment found high-risk evidence.",
                    recommendation="Keep the subject isolated.",
                )
            ],
        )
        posture = compute_security_posture(
            {
                "external_artifacts": {
                    "modalities": ["image"],
                    "trust": "untrusted",
                    "mode": "advisory",
                    "required_capabilities": ["artifact-reputation/v1"],
                }
            },
            ["artifact-reputation/v1"],
        )

        decision = assessment_decision(result, posture)
        self.assertEqual(decision["decision"], "review_required")
        self.assertFalse(decision["safe_to_process"])
        self.assertEqual(decision["max_severity"], "high")
        self.assertIn("HIGH_FINDING", decision["reason_codes"])

    def test_assessment_decision_requires_all_installed_capabilities_to_be_exercised(self):
        result = AssessmentResult(
            adapter="decision-demo",
            subject=AssessmentSubject("sha256", "b" * 64),
            capabilities=["artifact-reputation/v1"],
        )
        posture = compute_security_posture(
            {
                "external_artifacts": {
                    "modalities": ["image"],
                    "trust": "untrusted",
                    "mode": "advisory",
                    "required_capabilities": [
                        "artifact-reputation/v1",
                        "visual-prompt-injection-screening/v1",
                    ],
                }
            },
            [
                "artifact-reputation/v1",
                "visual-prompt-injection-screening/v1",
            ],
        )

        decision = assessment_decision(result, posture)
        self.assertEqual(decision["decision"], "review_required")
        self.assertEqual(
            decision["required_capabilities_unassessed"],
            ["visual-prompt-injection-screening/v1"],
        )
        self.assertIn("REQUIRED_CAPABILITY_NOT_ASSESSED", decision["reason_codes"])

    def test_assessment_decision_allows_only_clean_complete_assessment(self):
        result = AssessmentResult(
            adapter="decision-demo",
            subject=AssessmentSubject("sha256", "c" * 64),
            capabilities=["artifact-reputation/v1"],
        )
        posture = compute_security_posture(
            {
                "external_artifacts": {
                    "modalities": ["image"],
                    "trust": "untrusted",
                    "mode": "advisory",
                    "required_capabilities": ["artifact-reputation/v1"],
                }
            },
            ["artifact-reputation/v1"],
        )

        decision = assessment_decision(result, posture)
        self.assertEqual(decision["decision"], "allow")
        self.assertTrue(decision["safe_to_process"])


class TestSignedAssessmentMCP(unittest.TestCase):
    def setUp(self):
        self.test_dir = Path(tempfile.mkdtemp())
        inject_entigram_manifest(str(self.test_dir), ["Entigram Schemas"], "Codex")
        (self.test_dir / "schema.lds").write_text("ENTITY: Demo\n  - id (String)\n")
        manifest_path = self.test_dir / ".etg" / "entigram.yaml"
        manifest = yaml.safe_load(manifest_path.read_text())
        manifest["external_artifacts"] = {
            "modalities": ["image"],
            "trust": "untrusted",
            "mode": "advisory",
            "required_capabilities": ["artifact-reputation/v1"],
        }
        manifest_path.write_text(yaml.safe_dump(manifest, sort_keys=True))

        self.package_dir = self.test_dir / ".etg" / "packages" / "@entigram" / "demo-risk"
        self.package_dir.mkdir(parents=True)
        (self.package_dir / "assessment_adapter.py").write_text(ADAPTER_SOURCE)
        metadata = {
            "name": "@entigram/demo-risk",
            "assessment_module": "@entigram/demo-risk/assessment_adapter.py",
            "assessment_adapters": ["signed-demo-risk"],
            "security_capabilities": ["artifact-reputation/v1"],
        }
        write_package_manifest(
            str(self.package_dir),
            create_package_manifest(str(self.package_dir), metadata),
        )
        sign_package_manifest(
            str(self.package_dir),
            key_path=str(self.test_dir / "signing-key.pem"),
        )
        self.service = EntigramMCPService(str(self.test_dir))

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    def test_signed_installed_package_is_discovered_but_not_executed(self):
        installed = load_installed_assessment_adapters(self.test_dir)
        self.assertEqual(installed["adapters"], [])
        self.assertEqual(installed["capabilities"], [])
        self.assertEqual(installed["packages"][0]["adapters"], ["signed-demo-risk"])
        self.assertFalse(installed["packages"][0]["executable"])
        self.assertIn("publisher", installed["excluded"][0]["reason"])

        output = json.loads(self.service.get_assessment_capabilities())
        self.assertTrue(output["ok"])
        self.assertEqual(
            output["security_posture"]["missing_capabilities"],
            ["artifact-reputation/v1"],
        )

    def test_mcp_refuses_to_execute_installed_adapter(self):
        output = json.loads(
            self.service.assess(
                json.dumps(
                    {
                        "adapter": "signed-demo-risk",
                        "subject_type": "sha256",
                        "subject": "b" * 64,
                    }
                )
            )
        )
        self.assertFalse(output["ok"])
        self.assertEqual(output["error"]["code"], "ASSESSMENT_FAILED")
        self.assertIn("execution is disabled", output["error"]["message"])

    def test_mcp_policy_does_not_override_execution_boundary(self):
        manifest_path = self.test_dir / ".etg" / "entigram.yaml"
        manifest = yaml.safe_load(manifest_path.read_text())
        manifest["external_artifacts"]["mode"] = "enforce"
        manifest["external_artifacts"]["required_capabilities"].append(
            "visual-prompt-injection-screening/v1"
        )
        manifest_path.write_text(yaml.safe_dump(manifest, sort_keys=True))

        output = json.loads(
            self.service.assess(
                json.dumps(
                    {
                        "adapter": "signed-demo-risk",
                        "subject_type": "sha256",
                        "subject": "e" * 64,
                    }
                )
            )
        )
        self.assertFalse(output["ok"])
        self.assertEqual(output["error"]["code"], "ASSESSMENT_FAILED")

    def test_mcp_rejects_module_path_and_unknown_adapter(self):
        unknown_field = json.loads(
            self.service.assess(
                json.dumps(
                    {
                        "adapter": "signed-demo-risk",
                        "subject_type": "sha256",
                        "subject": "c" * 64,
                        "adapter_module": "/tmp/unsafe.py",
                    }
                )
            )
        )
        self.assertEqual(unknown_field["error"]["code"], "UNKNOWN_FIELD")

        unknown_adapter = json.loads(
            self.service.assess(
                json.dumps(
                    {
                        "adapter": "not-installed",
                        "subject_type": "sha256",
                        "subject": "d" * 64,
                    }
                )
            )
        )
        self.assertEqual(unknown_adapter["error"]["code"], "ASSESSMENT_FAILED")


class TestAssessmentCLI(unittest.TestCase):
    def test_cli_loads_explicit_module_and_emits_structured_result(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inject_entigram_manifest(str(root), ["Entigram Schemas"], "Codex")
            (root / "schema.lds").write_text("ENTITY: Demo\n  - id (String)\n")
            module_path = root / "assessment_adapter.py"
            module_path.write_text(ADAPTER_SOURCE.replace("signed-demo-risk", "cli-demo-risk"))
            output = StringIO()
            argv = [
                "etg",
                "assess",
                "--adapter",
                "cli-demo-risk",
                "--adapter-module",
                str(module_path),
                "--allow-executable-adapter",
                "--subject-type",
                "sha256",
                "--subject",
                "f" * 64,
                "--dir",
                str(root),
                "--json",
            ]
            from entigram.cli_runner.etg_cli import main

            with patch.object(sys, "argv", argv), patch("sys.stdout", output):
                with self.assertRaises(SystemExit) as exit_status:
                    main()

            self.assertEqual(exit_status.exception.code, 3)

            payload = json.loads(output.getvalue())
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["assessment"]["adapter"], "cli-demo-risk")
            self.assertEqual(payload["security_posture"]["mode"], "off")
            self.assertEqual(payload["decision"], "review_required")
            self.assertFalse(payload["safe_to_process"])

    def test_cli_plain_text_leads_with_decision_and_recommendation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inject_entigram_manifest(str(root), ["Entigram Schemas"], "Codex")
            (root / "schema.lds").write_text("ENTITY: Demo\n  - id (String)\n")
            module_path = root / "assessment_adapter.py"
            module_path.write_text(ADAPTER_SOURCE.replace("signed-demo-risk", "plain-demo-risk"))
            output = StringIO()
            argv = [
                "etg",
                "assess",
                "--adapter",
                "plain-demo-risk",
                "--adapter-module",
                str(module_path),
                "--allow-executable-adapter",
                "--subject-type",
                "sha256",
                "--subject",
                "f" * 64,
                "--dir",
                str(root),
            ]
            from entigram.cli_runner.etg_cli import main

            with patch.object(sys, "argv", argv), patch("sys.stdout", output):
                with self.assertRaises(SystemExit) as exit_status:
                    main()

            self.assertEqual(exit_status.exception.code, 3)

            rendered = output.getvalue()
            self.assertIn("Decision: REVIEW_REQUIRED", rendered)
            self.assertIn("Safe to process: no", rendered)
            self.assertIn("The signed demo adapter returned reputation evidence.", rendered)
            self.assertIn("Recommendation: Review the evidence before acting.", rendered)
            self.assertIn("Recommended action:", rendered)

    def test_cli_subject_file_blocks_when_bytes_change_during_assessment(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inject_entigram_manifest(str(root), ["Entigram Schemas"], "Codex")
            (root / "schema.lds").write_text("ENTITY: Demo\n  - id (String)\n")
            artifact = root / "artifact.bin"
            artifact.write_bytes(b"original")
            module_path = root / "assessment_adapter.py"
            module_path.write_text(ADAPTER_SOURCE.replace("signed-demo-risk", "file-demo-risk"))
            output = StringIO()
            argv = [
                "etg",
                "assess",
                "--adapter",
                "file-demo-risk",
                "--adapter-module",
                str(module_path),
                "--allow-executable-adapter",
                "--subject-type",
                "sha256",
                "--subject-file",
                str(artifact),
                "--dir",
                str(root),
                "--json",
            ]
            from entigram.cli_runner.etg_cli import main

            with (
                patch.object(sys, "argv", argv),
                patch("sys.stdout", output),
                patch(
                    "entigram.cli_runner.etg_cli._sha256_file",
                    side_effect=["a" * 64, "b" * 64],
                ),
            ):
                with self.assertRaises(SystemExit) as exit_status:
                    main()

            self.assertEqual(exit_status.exception.code, 2)
            payload = json.loads(output.getvalue())
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["decision"], "blocked")
            self.assertFalse(payload["safe_to_process"])
            self.assertIn("SUBJECT_CHANGED_DURING_ASSESSMENT", payload["reason_codes"])


class TestTechnologyDetection(unittest.TestCase):
    """Tests for workspace technology detection and proactive advisories."""

    def test_web_frontend_detected_by_package_json(self):
        from entigram.assessment import detect_workspace_technologies

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "package.json").write_text("{}")
            detected = detect_workspace_technologies(root)
            techs = [t["technology"] for t in detected]
            self.assertIn("web-frontend", techs)
            match = next(t for t in detected if t["technology"] == "web-frontend")
            self.assertIn("package.json", match["matched_signals"])
            self.assertIn("OWASP Top 10", match["frameworks"])

    def test_api_backend_detected_by_requirements_txt(self):
        from entigram.assessment import detect_workspace_technologies

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "requirements.txt").write_text("flask\n")
            detected = detect_workspace_technologies(root)
            techs = [t["technology"] for t in detected]
            self.assertIn("web-api", techs)
            match = next(t for t in detected if t["technology"] == "web-api")
            self.assertIn("OWASP API Security Top 10", match["frameworks"])

    def test_container_detected_by_dockerfile(self):
        from entigram.assessment import detect_workspace_technologies

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "Dockerfile").write_text("FROM python:3.13\n")
            detected = detect_workspace_technologies(root)
            techs = [t["technology"] for t in detected]
            self.assertIn("container", techs)

    def test_no_signals_produces_no_detections(self):
        from entigram.assessment import detect_workspace_technologies

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "README.md").write_text("hello")
            detected = detect_workspace_technologies(root)
            self.assertEqual(detected, [])

    def test_inactive_posture_with_web_frontend_emits_advisory(self):
        from entigram.assessment import workspace_security_posture

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            etg_dir = root / ".etg"
            etg_dir.mkdir()
            (etg_dir / "entigram.yaml").write_text(
                yaml.dump({"status": "initialized", "packages": {}})
            )
            (root / "package.json").write_text("{}")
            posture = workspace_security_posture(root)
            self.assertFalse(posture["configured"])
            self.assertFalse(posture["enforcement_blocked"])
            self.assertGreater(len(posture["advisories"]), 0)
            advisory = posture["advisories"][0]
            self.assertEqual(advisory["code"], "ETG-RISK-UNCONFIGURED-TECHNOLOGY")
            self.assertEqual(advisory["severity"], "warning")
            self.assertFalse(advisory["acknowledged"])
            self.assertTrue(advisory["user_dismissable"])
            self.assertIn("web frontend", advisory["message"].lower())
            self.assertIn("OWASP Top 10", advisory["compatible_frameworks"])
            self.assertIn("web-frontend", posture.get("detected_technologies", []))
            # Custom adapter mitigation should always be offered
            adapter_mitigations = [m for m in advisory["free_mitigations"] if "adapter" in m.lower()]
            self.assertGreater(len(adapter_mitigations), 0)

    def test_inactive_posture_without_signals_has_no_advisories(self):
        from entigram.assessment import workspace_security_posture

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            etg_dir = root / ".etg"
            etg_dir.mkdir()
            (etg_dir / "entigram.yaml").write_text(
                yaml.dump({"status": "initialized", "packages": {}})
            )
            posture = workspace_security_posture(root)
            self.assertFalse(posture["configured"])
            self.assertEqual(posture["advisories"], [])

    def test_multiple_technologies_produce_multiple_advisories(self):
        from entigram.assessment import workspace_security_posture

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            etg_dir = root / ".etg"
            etg_dir.mkdir()
            (etg_dir / "entigram.yaml").write_text(
                yaml.dump({"status": "initialized", "packages": {}})
            )
            (root / "package.json").write_text("{}")
            (root / "Dockerfile").write_text("FROM node:20\n")
            (root / "requirements.txt").write_text("django\n")
            posture = workspace_security_posture(root)
            techs = posture.get("detected_technologies", [])
            self.assertIn("web-frontend", techs)
            self.assertIn("web-api", techs)
            self.assertIn("container", techs)
            self.assertEqual(len(posture["advisories"]), 3)

    def test_configured_posture_does_not_duplicate_tech_advisories(self):
        """When external_artifacts is configured, tech advisories should not appear."""
        manifest = {
            "external_artifacts": {
                "modalities": ["image"],
                "trust": "untrusted",
                "mode": "advisory",
                "required_capabilities": ["artifact-reputation/v1"],
            }
        }
        posture = compute_security_posture(manifest, ["artifact-reputation/v1"])
        self.assertTrue(posture["configured"])
        codes = [a.get("code") for a in posture["advisories"]]
        self.assertNotIn("ETG-RISK-UNCONFIGURED-TECHNOLOGY", codes)

    def test_suppressed_advisory_still_shown_as_acknowledged(self):
        """Suppressed findings are always shown, never hidden."""
        from entigram.assessment import workspace_security_posture

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            etg_dir = root / ".etg"
            etg_dir.mkdir()
            (etg_dir / "entigram.yaml").write_text(
                yaml.dump({"status": "initialized", "packages": {}})
            )
            (root / "package.json").write_text("{}")
            # Write suppression
            (etg_dir / "assessment-suppressions.yaml").write_text(
                yaml.dump({
                    "web-frontend": {
                        "rationale": "Static marketing site, no dynamic user input.",
                        "suppressed_by": "tech-lead",
                    }
                })
            )
            posture = workspace_security_posture(root)
            # Advisory is still present, never hidden
            self.assertGreater(len(posture["advisories"]), 0)
            advisory = posture["advisories"][0]
            self.assertEqual(advisory["severity"], "warning")
            self.assertTrue(advisory["acknowledged"])
            self.assertEqual(advisory["suppressed_by"], "tech-lead")
            self.assertIn("Static marketing site", advisory["suppression_rationale"])

    def test_suppression_without_rationale_is_ignored(self):
        """Suppressions require a rationale — empty ones are rejected."""
        from entigram.assessment import workspace_security_posture

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            etg_dir = root / ".etg"
            etg_dir.mkdir()
            (etg_dir / "entigram.yaml").write_text(
                yaml.dump({"status": "initialized", "packages": {}})
            )
            (root / "package.json").write_text("{}")
            (etg_dir / "assessment-suppressions.yaml").write_text(
                yaml.dump({"web-frontend": {"rationale": ""}})
            )
            posture = workspace_security_posture(root)
            advisory = posture["advisories"][0]
            # Suppression with empty rationale is not honored
            self.assertFalse(advisory["acknowledged"])


class TestAssessmentCatalog(unittest.TestCase):
    """Tests for the assessment package catalog and request-access flow."""

    def test_catalog_returns_all_packages(self):
        from entigram.assessment import get_assessment_catalog

        catalog = get_assessment_catalog()
        self.assertGreater(len(catalog), 0)
        packages = [p["package"] for p in catalog]
        self.assertIn("@entigram/api-security", packages)
        self.assertIn("@entigram/data-privacy", packages)
        for pkg in catalog:
            self.assertIn("capabilities", pkg)
            self.assertIn("frameworks", pkg)
            self.assertIn("tier", pkg)
            self.assertIn("status", pkg)
            self.assertIn(pkg["status"], ("published", "coming_soon", "preview"))

    def test_workspace_aware_catalog_recommends_relevant_packages(self):
        from entigram.assessment import build_assessment_catalog

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "package.json").write_text("{}")
            (root / "Dockerfile").write_text("FROM node:20\n")
            catalog = build_assessment_catalog(root)
            recommended_names = [p["package"] for p in catalog["recommended_packages"]]
            self.assertIn("@entigram/web-security", recommended_names)
            self.assertIn("@entigram/container-security", recommended_names)
            for pkg in catalog["recommended_packages"]:
                self.assertTrue(pkg["relevant_to_workspace"])
                self.assertGreater(len(pkg["matched_technologies"]), 0)

    def test_workspace_aware_catalog_with_no_signals(self):
        from entigram.assessment import build_assessment_catalog

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            catalog = build_assessment_catalog(root)
            self.assertEqual(catalog["recommended_packages"], [])
            self.assertGreater(len(catalog["other_packages"]), 0)

    def test_request_access_records_to_file(self):
        from entigram.assessment import record_package_access_request

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / ".etg").mkdir()
            record = record_package_access_request(root, "@entigram/api-security")
            self.assertEqual(record["package"], "@entigram/api-security")
            self.assertEqual(record["tier"], "standard")
            self.assertEqual(record["status"], "published")
            self.assertIn("requested_at", record)
            self.assertIn("issue_url", record)
            self.assertTrue(record["issue_url"].startswith("https://github.com/Entigram-AI/entigram/issues/new"))
            request_file = root / ".etg" / "access_requests" / "entigram_api-security.json"
            self.assertTrue(request_file.is_file())
            import json
            persisted = json.loads(request_file.read_text())
            self.assertEqual(persisted["package"], "@entigram/api-security")
            self.assertEqual(persisted["status"], "published")
            # Verify demand ledger was appended
            demand_file = root / ".etg" / "access_requests" / "demand_ledger.jsonl"
            self.assertTrue(demand_file.is_file())
            lines = demand_file.read_text().strip().split("\n")
            self.assertEqual(len(lines), 1)
            entry = json.loads(lines[0])
            self.assertEqual(entry["package"], "@entigram/api-security")
            self.assertEqual(entry["status"], "published")

    def test_request_access_coming_soon_package(self):
        from entigram.assessment import record_package_access_request

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / ".etg").mkdir()
            record = record_package_access_request(root, "@entigram/data-privacy")
            self.assertEqual(record["package"], "@entigram/data-privacy")
            self.assertEqual(record["tier"], "professional")
            self.assertEqual(record["status"], "coming_soon")
            # Demand is still tracked even for unpublished packages
            import json
            demand_file = root / ".etg" / "access_requests" / "demand_ledger.jsonl"
            self.assertTrue(demand_file.is_file())
            entry = json.loads(demand_file.read_text().strip())
            self.assertEqual(entry["package"], "@entigram/data-privacy")
            self.assertEqual(entry["status"], "coming_soon")

    def test_demand_ledger_appends_multiple_requests(self):
        from entigram.assessment import record_package_access_request

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / ".etg").mkdir()
            record_package_access_request(root, "@entigram/api-security")
            record_package_access_request(root, "@entigram/web-security")
            record_package_access_request(root, "@entigram/data-privacy")
            import json
            demand_file = root / ".etg" / "access_requests" / "demand_ledger.jsonl"
            lines = demand_file.read_text().strip().split("\n")
            self.assertEqual(len(lines), 3)
            packages = [json.loads(line)["package"] for line in lines]
            self.assertEqual(packages, [
                "@entigram/api-security",
                "@entigram/web-security",
                "@entigram/data-privacy",
            ])

    def test_request_access_rejects_unknown_package(self):
        from entigram.assessment import record_package_access_request

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / ".etg").mkdir()
            with self.assertRaises(ValueError) as ctx:
                record_package_access_request(root, "@entigram/does-not-exist")
            self.assertIn("Unknown package", str(ctx.exception))

    def test_request_access_rejects_invalid_package_name(self):
        from entigram.assessment import record_package_access_request

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / ".etg").mkdir()
            with self.assertRaises(ValueError) as ctx:
                record_package_access_request(root, "bad-name")
            self.assertIn("Invalid package name", str(ctx.exception))

class TestBuildPackageIssueUrl(unittest.TestCase):

    def test_published_package_url_structure(self):
        from entigram.assessment import _build_package_issue_url
        from urllib.parse import urlparse, parse_qs, unquote

        entry = {
            "package": "@entigram/api-security",
            "description": "API security assessments",
            "tier": "standard",
            "status": "published",
            "capabilities": ["injection-detection/v1"],
            "frameworks": ["OWASP Top 10"],
            "technologies": ["web-api"],
        }
        url = _build_package_issue_url(entry)
        parsed = urlparse(url)
        self.assertEqual(parsed.scheme, "https")
        self.assertIn("github.com", parsed.netloc)
        self.assertIn("/issues/new", parsed.path)
        qs = parse_qs(parsed.query)
        self.assertEqual(qs["title"][0], "Package Request: @entigram/api-security")
        self.assertIn("package-request", unquote(qs["labels"][0]))
        self.assertNotIn("coming-soon", unquote(qs["labels"][0]))
        body = qs["body"][0]
        self.assertIn("injection-detection/v1", body)
        self.assertIn("OWASP Top 10", body)
        self.assertIn("Published", body)

    def test_coming_soon_package_has_extra_label(self):
        from entigram.assessment import _build_package_issue_url
        from urllib.parse import urlparse, parse_qs, unquote

        entry = {
            "package": "@entigram/data-privacy",
            "description": "PII detection",
            "tier": "professional",
            "status": "coming_soon",
            "capabilities": ["pii-detection/v1"],
            "frameworks": ["GDPR Art. 32"],
            "technologies": ["data-processing"],
        }
        url = _build_package_issue_url(entry)
        parsed = urlparse(url)
        qs = parse_qs(parsed.query)
        labels = unquote(qs["labels"][0])
        self.assertIn("coming-soon", labels)
        body = qs["body"][0]
        self.assertIn("Coming Soon", body)

    def test_preview_status_renders_correctly(self):
        from entigram.assessment import _build_package_issue_url
        from urllib.parse import urlparse, parse_qs

        entry = {
            "package": "@entigram/test-pkg",
            "description": "Preview package",
            "tier": "standard",
            "status": "preview",
            "capabilities": ["test/v1"],
            "frameworks": ["Test"],
            "technologies": ["web-api"],
        }
        url = _build_package_issue_url(entry)
        parsed = urlparse(url)
        qs = parse_qs(parsed.query)
        body = qs["body"][0]
        self.assertIn("Preview", body)
        self.assertNotIn("Published", body)

    def test_workspace_detection_included_in_body(self):
        from entigram.assessment import _build_package_issue_url
        from urllib.parse import urlparse, parse_qs

        entry = {
            "package": "@entigram/api-security",
            "description": "API security",
            "tier": "standard",
            "status": "published",
            "capabilities": ["injection-detection/v1"],
            "frameworks": ["OWASP Top 10"],
            "technologies": ["web-api"],
        }
        techs = [{"label": "Web API", "technology": "web-api",
                   "matched_signals": ["requirements.txt", "app.py"]}]
        url = _build_package_issue_url(entry, detected_technologies=techs)
        parsed = urlparse(url)
        qs = parse_qs(parsed.query)
        body = qs["body"][0]
        self.assertIn("Workspace Detection Context", body)
        self.assertIn("requirements.txt", body)

    def test_url_capped_at_max_length(self):
        from entigram.assessment import _build_package_issue_url, _MAX_ISSUE_URL_LENGTH

        entry = {
            "package": "@entigram/api-security",
            "description": "API security",
            "tier": "standard",
            "status": "published",
            "capabilities": ["injection-detection/v1"],
            "frameworks": ["OWASP Top 10"],
            "technologies": ["web-api"],
        }
        # Generate massive detection context to force URL over limit
        huge_techs = [
            {"label": f"Tech {i}", "technology": "web-api",
             "matched_signals": [f"signal_{j}.py" for j in range(50)]}
            for i in range(20)
        ]
        url = _build_package_issue_url(entry, detected_technologies=huge_techs)
        self.assertLessEqual(len(url), _MAX_ISSUE_URL_LENGTH)
        # Workspace context should have been stripped
        self.assertNotIn("Workspace Detection Context", url)


if __name__ == "__main__":
    unittest.main()

