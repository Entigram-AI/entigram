import unittest

from entigram.package_catalog import suggest_packages, validate_package_catalog


class TestPackageCatalog(unittest.TestCase):
    def test_suggest_packages_ranks_matching_standard_package(self):
        catalog = {
            "packages": [
                package_entry(
                    "@entigram/aws",
                    "AWS",
                    "AWS Glue and cloud catalog discovery",
                    ["aws", "glue", "cloud"],
                    ["glue-catalog"],
                    ["aws-glue-catalog"],
                ),
                package_entry(
                    "@entigram/salesforce",
                    "Salesforce",
                    "Salesforce describe metadata discovery",
                    ["crm", "salesforce"],
                    ["salesforce-describe"],
                    ["salesforce-describe"],
                ),
            ]
        }

        suggestions = suggest_packages(catalog, "salesforce crm describe")

        self.assertEqual(suggestions[0].name, "@entigram/salesforce")
        self.assertEqual(suggestions[0].adapters, ["salesforce-describe"])

    def test_validate_package_catalog_accepts_provenance_metadata(self):
        catalog = {
            "packages": [
                package_entry(
                    "@entigram/postgres",
                    "PostgreSQL",
                    "PostgreSQL discovery package",
                    ["postgres", "database"],
                    ["postgres-information-schema"],
                    ["postgres-information-schema"],
                )
            ]
        }

        self.assertEqual(validate_package_catalog(catalog), [])

    def test_validate_package_catalog_requires_license_and_provenance(self):
        catalog = {
            "packages": [
                {
                    "name": "@entigram/postgres",
                    "title": "PostgreSQL",
                    "description": "PostgreSQL discovery package",
                    "tags": ["postgres", "database"],
                    "source_kinds": ["postgres-information-schema"],
                    "adapters": ["postgres-information-schema"],
                    "adapter_module": "@entigram/postgres/source_adapter.py",
                }
            ]
        }

        issues = validate_package_catalog(catalog)
        fields = {issue.field for issue in issues}

        self.assertIn("license", fields)
        self.assertIn("publisher", fields)
        self.assertIn("provenance", fields)
        self.assertIn("certification", fields)

    def test_validate_package_catalog_accepts_assessment_metadata(self):
        package = package_entry(
            "@entigram/artifact-risk",
            "Artifact Risk",
            "Artifact reputation assessment",
            ["security", "reputation"],
            [],
            [],
        )
        package.pop("adapter_module")
        package.pop("source_kinds")
        package.pop("adapters")
        package.update(
            {
                "assessment_module": "@entigram/artifact-risk/assessment_adapter.py",
                "assessment_adapters": ["virustotal-hash"],
                "security_capabilities": ["artifact-reputation/v1"],
            }
        )

        self.assertEqual(validate_package_catalog({"packages": [package]}), [])

    def test_assessment_metadata_requires_versioned_capabilities(self):
        package = package_entry(
            "@entigram/artifact-risk",
            "Artifact Risk",
            "Artifact reputation assessment",
            ["security", "reputation"],
            [],
            [],
        )
        package.pop("adapter_module")
        package.pop("source_kinds")
        package.pop("adapters")
        package.update(
            {
                "assessment_module": "assessment_adapter.py",
                "assessment_adapters": ["virustotal-hash"],
                "security_capabilities": ["artifact-reputation"],
            }
        )

        fields = {issue.field for issue in validate_package_catalog({"packages": [package]})}
        self.assertIn("security_capabilities", fields)

    def test_assessment_metadata_rejects_unsafe_module_path(self):
        package = package_entry(
            "@entigram/artifact-risk",
            "Artifact Risk",
            "Artifact reputation assessment",
            ["security", "reputation"],
            [],
            [],
        )
        package.pop("adapter_module")
        package.pop("source_kinds")
        package.pop("adapters")
        package.update(
            {
                "assessment_module": "../unsafe.py",
                "assessment_adapters": ["virustotal-hash"],
                "security_capabilities": ["artifact-reputation/v1"],
            }
        )

        fields = {issue.field for issue in validate_package_catalog({"packages": [package]})}
        self.assertIn("assessment_module", fields)


def package_entry(name, title, description, tags, source_kinds, adapters):
    return {
        "name": name,
        "title": title,
        "description": description,
        "tags": tags,
        "source_kinds": source_kinds,
        "adapters": adapters,
        "adapter_module": f"{name}/source_adapter.py",
        "license": {
            "spdx": "Apache-2.0",
            "notice_required": True,
        },
        "publisher": {
            "name": "Entigram",
            "namespace": "@entigram",
        },
        "provenance": {
            "source_repository": "https://github.com/entigram/entigram-standard-packages",
            "package_path": name,
            "release_channel": "standard",
            "signed": True,
        },
        "certification": {
            "status": "community",
            "compatibility": "entigram>=1.7",
            "test_evidence": ["mock-endpoint"],
            "trademark_use": "nominative",
        },
    }


if __name__ == "__main__":
    unittest.main()
