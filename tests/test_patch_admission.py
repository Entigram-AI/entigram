"""Unit and adversarial tests for patch admission inspection."""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from entigram.governance.patch_admission import (
    extract_files_from_diff,
    inspect_patch,
    normalize_path,
)


class TestPatchAdmission(unittest.TestCase):
    def test_normalize_path(self):
        self.assertEqual(normalize_path("./foo/bar.py"), "foo/bar.py")
        self.assertEqual(normalize_path("foo\\bar\\baz.py"), "foo/bar/baz.py")
        self.assertEqual(normalize_path("tests/../views.py"), "views.py")
        self.assertEqual(normalize_path("tests_other/foo.py"), "tests_other/foo.py")

    def test_extract_files_from_diff(self):
        diff = """diff --git a/foo.py b/foo.py
--- a/foo.py
+++ b/foo.py
@@ -1 +1 @@
-a = 1
+a = 2
--- a/tests/test_foo.py\t2026-01-01
+++ b/tests/test_foo.py\t2026-01-01
@@ -1 +1 @@
-def test(): pass
+def test(): assert True
"""
        files = extract_files_from_diff(diff)
        self.assertIn("foo.py", files)
        self.assertIn("tests/test_foo.py", files)
        self.assertNotIn("/dev/null", files)

    def test_inspect_patch_in_repo(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            subprocess.run(["git", "init"], cwd=tmppath, check=True, capture_output=True)
            subprocess.run(
                ["git", "config", "user.name", "Test"], cwd=tmppath, check=True, capture_output=True
            )
            subprocess.run(
                ["git", "config", "user.email", "test@example.com"], cwd=tmppath, check=True, capture_output=True
            )

            # Initial commit
            src_file = tmppath / "main.py"
            src_file.write_text("def hello(): return 'world'\n", encoding="utf-8")
            subprocess.run(["git", "add", "main.py"], cwd=tmppath, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "initial"], cwd=tmppath, check=True, capture_output=True)

            # Test 1: Empty patch
            res = inspect_patch(tmppath)
            self.assertFalse(res["ok"])
            self.assertIn("EMPTY_PATCH", res["codes"])

            # Test 2: Untracked scratch only
            scratch = tmppath / "scratch.py"
            scratch.write_text("x = 1\n", encoding="utf-8")
            res = inspect_patch(tmppath)
            self.assertFalse(res["ok"])
            self.assertIn("UNTRACKED_SCRATCH_ONLY", res["codes"])
            scratch.unlink()

            # Test 3: Protected file modification (e.g., test file)
            test_file = tmppath / "tests" / "test_main.py"
            test_file.parent.mkdir(parents=True, exist_ok=True)
            test_file.write_text("def test(): pass\n", encoding="utf-8")
            subprocess.run(["git", "add", "tests/test_main.py"], cwd=tmppath, check=True, capture_output=True)
            res = inspect_patch(tmppath, protected_paths=["tests/"])
            self.assertFalse(res["ok"])
            self.assertIn("FORBIDDEN_FILE_MODIFICATION", res["codes"])

            # Test 4: Protected path evasion tests
            # tests_other/ should NOT trigger protected_paths=["tests/"]
            subprocess.run(["git", "reset", "--hard", "HEAD"], cwd=tmppath, check=True, capture_output=True)
            other_file = tmppath / "tests_other" / "util.py"
            other_file.parent.mkdir(parents=True, exist_ok=True)
            other_file.write_text("def util(): pass\n", encoding="utf-8")
            subprocess.run(["git", "add", "tests_other/util.py"], cwd=tmppath, check=True, capture_output=True)
            res = inspect_patch(tmppath, protected_paths=["tests/"])
            self.assertTrue(res["ok"], f"tests_other should not be blocked: {res}")

            # Test 5: Syntax error in source file
            subprocess.run(["git", "reset", "--hard", "HEAD"], cwd=tmppath, check=True, capture_output=True)
            src_file.write_text("def broken_syntax(:\n", encoding="utf-8")
            res = inspect_patch(tmppath)
            self.assertFalse(res["ok"])
            self.assertIn("SYNTAX_ERROR", res["codes"])

            # Test 6: Valid source edit
            subprocess.run(["git", "reset", "--hard", "HEAD"], cwd=tmppath, check=True, capture_output=True)
            src_file.write_text("def hello(): return 'updated world'\n", encoding="utf-8")
            res = inspect_patch(tmppath)
            self.assertTrue(res["ok"])
            self.assertEqual(res["decision"], "admit")
            self.assertTrue(len(res["diff_sha256"]) == 64)


if __name__ == "__main__":
    unittest.main()
