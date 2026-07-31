import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class RepositoryOwnershipTests(unittest.TestCase):
    def test_active_repository_references_use_organization_owner(self):
        expected_references = {
            "pyproject.toml": (
                "https://github.com/Entigram-AI/entigram",
                "https://github.com/Entigram-AI/entigram/issues",
            ),
            "entigram/registry.py": (
                "git@github.com:Entigram-AI/entigram-standard-packages.git",
            ),
            ".github/workflows/release-please.yml": (
                "repository: Entigram-AI/homebrew-entigram",
            ),
        }

        for relative_path, references in expected_references.items():
            contents = (ROOT / relative_path).read_text()
            with self.subTest(path=relative_path):
                self.assertNotIn("github.com/nyabutid/entigram", contents)
                for reference in references:
                    self.assertIn(reference, contents)

    def test_release_has_one_authoritative_publish_workflow(self):
        self.assertTrue((ROOT / ".github/workflows/release-please.yml").is_file())
        self.assertFalse((ROOT / ".github/workflows/release.yml").exists())


if __name__ == "__main__":
    unittest.main()
