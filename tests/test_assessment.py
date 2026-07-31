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


if __name__ == "__main__":
    unittest.main()
