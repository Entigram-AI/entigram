import unittest
import shutil
from pathlib import Path
import yaml
from entigram.governance.sentinel import SentinelScanner

class TestSentinelScanner(unittest.TestCase):
    def setUp(self):
        self.test_dir = Path("test_sentinel_workspace")
        self.packages_dir = self.test_dir / "packages"
        if self.test_dir.exists():
            shutil.rmtree(self.test_dir)
        self.packages_dir.mkdir(parents=True)
        
        # 1. Setup a standard package with a known vulnerability
        aws_dir = self.packages_dir / "AWS"
        aws_dir.mkdir()
        (aws_dir / "schema.lds").write_text("ENTITY Bucket { id UUID }")
        self.aws_dir = aws_dir
        
        # 2. Setup a custom package with a heuristic vulnerability
        custom_dir = self.packages_dir / "MyCustomAuth"
        custom_dir.mkdir()
        (custom_dir / "schema.lds").write_text("ENTITY User { id UUID, password String }")
        
        self.scanner = SentinelScanner(str(self.test_dir))
        self.scanner.global_registry_cache = self.test_dir / "registry_cache"

    def tearDown(self):
        if self.test_dir.exists():
            shutil.rmtree(self.test_dir)

    def test_scan_standard_package(self):
        self.scanner.vulnerability_db["AWS"] = [{"id": "CVE-TEST-001", "severity": "HIGH", "description": "Test"}]
        results = self.scanner.scan_package("AWS")
        self.assertTrue(results["is_standard"])
        vulns = results["vulnerabilities"]
        self.assertTrue(any(v["id"] == "CVE-TEST-001" for v in vulns))

    def test_scan_custom_package(self):
        results = self.scanner.scan_package("MyCustomAuth")
        self.assertFalse(results["is_standard"])
        vulns = results["vulnerabilities"]
        self.assertTrue(any(v["id"] == "SNTNL-CUST-001" for v in vulns))

    def test_bypass_custom_package(self):
        # Authorize bypass
        success = self.scanner.authorize_bypass("MyCustomAuth", "SNTNL-CUST-001", "It is hashed in application layer")
        self.assertTrue(success)
        
        # Rescan
        results = self.scanner.scan_package("MyCustomAuth")
        vulns = results["vulnerabilities"]
        self.assertFalse(any(v["id"] == "SNTNL-CUST-001" for v in vulns))
        self.assertIn("SNTNL-CUST-001", results["bypassed"])

    def test_reject_bypass_standard_package(self):
        self.scanner.vulnerability_db["AWS"] = [{"id": "CVE-TEST-001", "severity": "HIGH", "description": "Test"}]
        success = self.scanner.authorize_bypass("AWS", "CVE-TEST-001", "I don't care about encryption")
        self.assertFalse(success)
        
        results = self.scanner.scan_package("AWS")
        self.assertTrue(any(v["id"] == "CVE-TEST-001" for v in results["vulnerabilities"]))

    def test_standard_package_suppression_is_occurrence_specific_and_auditable(self):
        schema_path = self.aws_dir / "schema.lds"
        schema_path.write_text(
            "ENTITY Bucket { id UUID }\n"
            "/* Demo fixture one */\n"
            "/* Demo fixture two */\n"
        )

        initial = self.scanner.scan_package("AWS")
        demo_findings = [
            finding
            for finding in initial["vulnerabilities"]
            if finding["id"] == "SNTNL-RULE-005"
        ]
        self.assertEqual(2, len(demo_findings))
        first = demo_findings[0]

        success = self.scanner.authorize_suppression(
            package_name="AWS",
            vulnerability_id=first["id"],
            fingerprint=first["fingerprint"],
            rationale="Reviewed fixture documentation; no customer data is present.",
            authorized_by="Security Reviewer",
            expires_at="2099-12-31",
        )
        self.assertTrue(success)

        rescanned = self.scanner.scan_package("AWS")
        active_demo_findings = [
            finding
            for finding in rescanned["vulnerabilities"]
            if finding["id"] == "SNTNL-RULE-005"
        ]
        self.assertEqual(1, len(active_demo_findings))
        self.assertEqual(1, len(rescanned["suppressed"]))
        self.assertEqual(first["fingerprint"], rescanned["suppressed"][0]["fingerprint"])
        self.assertEqual(
            "Reviewed fixture documentation; no customer data is present.",
            rescanned["suppressed"][0]["suppression"]["rationale"],
        )

        suppression_payload = yaml.safe_load(
            (self.aws_dir / ".sentinel-suppressions.yaml").read_text()
        )
        self.assertEqual(1, suppression_payload["version"])
        self.assertEqual("Security Reviewer", suppression_payload["suppressions"][0]["authorized_by"])

        with schema_path.open("a") as schema_file:
            schema_file.write("/* Demo fixture three */\n")
        final_scan = self.scanner.scan_package("AWS")
        final_active_demo_findings = [
            finding
            for finding in final_scan["vulnerabilities"]
            if finding["id"] == "SNTNL-RULE-005"
        ]
        self.assertEqual(2, len(final_active_demo_findings))

        schema_path.write_text(
            "ENTITY Bucket { id UUID }\n"
            "/* Demo fixture one changed after review */\n"
            "/* Demo fixture two */\n"
            "/* Demo fixture three */\n"
        )
        changed_scan = self.scanner.scan_package("AWS")
        changed_demo_findings = [
            finding
            for finding in changed_scan["vulnerabilities"]
            if finding["id"] == "SNTNL-RULE-005"
        ]
        self.assertEqual(3, len(changed_demo_findings))
        self.assertEqual([], changed_scan["suppressed"])

    def test_suppression_rejects_unknown_fingerprint(self):
        success = self.scanner.authorize_suppression(
            package_name="AWS",
            vulnerability_id="SNTNL-RULE-005",
            fingerprint=f"sha256:{'0' * 64}",
            rationale="Not a current finding.",
            authorized_by="Security Reviewer",
        )
        self.assertFalse(success)
        self.assertFalse((self.aws_dir / ".sentinel-suppressions.yaml").exists())

if __name__ == "__main__":
    unittest.main()
