import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from assessment_adapter import DataPrivacyAssessmentAdapter
from entigram.assessment import AssessmentSubject


class TestDataPrivacyAssessmentAdapter(unittest.TestCase):
    def setUp(self):
        self.adapter = DataPrivacyAssessmentAdapter()

    def test_offline_assessment_covers_requested_capabilities(self):
        result = self.adapter.assess(
            AssessmentSubject(
                "data-privacy-profile",
                "customer-inventory",
                {
                    "assets": [
                        {
                            "name": "customer",
                            "fields": [
                                {
                                    "name": "email",
                                    "classification": "personal",
                                    "encrypted_at_rest": True,
                                    "encryption_evidence_ref": "evidence://kms/customer",
                                    "retention_days": 365,
                                    "retention_evidence_ref": "evidence://policy/customer",
                                }
                            ],
                        }
                    ]
                },
            )
        )
        self.assertEqual(
            set(result.capabilities),
            {"pii-detection/v1", "encryption-audit/v1", "retention-policy-check/v1"},
        )
        self.assertEqual(result.findings, [])
        self.assertTrue(result.metadata["offline"])
        self.assertFalse(result.metadata["raw_values_accessed"])

    def test_heuristic_detection_requires_classification_controls_and_retention(self):
        result = self.adapter.assess(
            AssessmentSubject(
                "data-privacy-profile",
                "incomplete",
                {"assets": [{"name": "users", "fields": [{"name": "ssn"}]}]},
            )
        )
        codes = {finding.code for finding in result.findings}
        self.assertIn("PII_FIELD_UNCLASSIFIED", codes)
        self.assertIn("ENCRYPTION_CONTROL_MISSING", codes)
        self.assertIn("RETENTION_POLICY_MISSING", codes)

    def test_custom_rules_are_supported_without_reading_values(self):
        result = self.adapter.assess(
            AssessmentSubject(
                "data-privacy-profile",
                "custom-rule",
                {
                    "assets": [{"name": "orders", "fields": [{"name": "email", "classification": "personal"}]}],
                    "custom_rules": [
                        {
                            "id": "email-encryption",
                            "field_pattern": "orders.email",
                            "require_encryption": True,
                        }
                    ],
                },
            )
        )
        self.assertIn("CUSTOM_PRIVACY_RULE_VIOLATION", {f.code for f in result.findings})

    def test_rejects_raw_values_and_unsafe_input_fields(self):
        with self.assertRaisesRegex(ValueError, "unknown fields"):
            self.adapter.assess(
                AssessmentSubject(
                    "data-privacy-profile",
                    "raw-data",
                    {"assets": [], "records": [{"email": "secret@example.com"}]},
                )
            )

    def test_rejects_invalid_retention_days(self):
        with self.assertRaisesRegex(ValueError, "retention_days"):
            self.adapter.assess(
                AssessmentSubject(
                    "data-privacy-profile",
                    "invalid-retention",
                    {
                        "assets": [
                            {"name": "users", "fields": [{"name": "email", "retention_days": -1}]}
                        ]
                    },
                )
            )


if __name__ == "__main__":
    unittest.main()
