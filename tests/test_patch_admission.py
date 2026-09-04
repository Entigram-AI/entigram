import subprocess
import tempfile
import unittest
from pathlib import Path

from entigram.governance.patch_admission import inspect_patch


class PatchAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        subprocess.run(["git", "init"], cwd=self.root, check=True, capture_output=True)
        (self.root / "app.py").write_text("value = 1\n")
        subprocess.run(["git", "add", "app.py"], cwd=self.root, check=True)
        subprocess.run(["git", "-c", "user.email=a@b.c", "-c", "user.name=t", "commit", "-m", "init"], cwd=self.root, check=True, capture_output=True)

    def test_admits_syntax_valid_source_diff(self):
        (self.root / "app.py").write_text("value = 2\n")
        result = inspect_patch(self.root, protected_paths=("tests/",))
        self.assertTrue(result["ok"])
        self.assertEqual("structural_only", result["scope"])

    def test_denies_protected_or_invalid_patch(self):
        (self.root / "tests").mkdir()
        (self.root / "tests" / "test_app.py").write_text("def broken(\n")
        subprocess.run(["git", "add", "tests/test_app.py"], cwd=self.root, check=True)
        result = inspect_patch(self.root, protected_paths=("tests/",))
        self.assertFalse(result["ok"])
        self.assertIn("PROTECTED_PATH", result["codes"])
        self.assertIn("SYNTAX_ERROR", result["codes"])

    def test_denies_deletion_of_a_protected_file(self):
        (self.root / "tests").mkdir()
        protected = self.root / "tests" / "test_app.py"
        protected.write_text("pass\n")
        subprocess.run(["git", "add", "tests/test_app.py"], cwd=self.root, check=True)
        subprocess.run(["git", "-c", "user.email=a@b.c", "-c", "user.name=t", "commit", "-m", "test"], cwd=self.root, check=True, capture_output=True)
        protected.unlink()
        result = inspect_patch(self.root, protected_paths=("tests/",))
        self.assertFalse(result["ok"])
        self.assertIn("PROTECTED_PATH", result["codes"])


if __name__ == "__main__":
    unittest.main()
