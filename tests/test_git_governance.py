import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from entigram.git_governance import (
    assess_repository,
    assessment_is_fresh,
    check_repository,
    install,
    resolve_conflict,
    run_merge_driver,
    safe_merge,
    write_assessment,
)


BASE = """ENTITY: Person
ATTRIBUTES:
  - .id (UUID)
  - name (String)
"""


class GitGovernanceTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp()).resolve()
        self.git("init", "-q")
        self.git("config", "user.email", "test@example.com")
        self.git("config", "user.name", "Test")
        (self.root / "schema.lds").write_text(BASE)
        self.git("add", "schema.lds")
        self.git("commit", "-qm", "base")
        self.base_branch = self.git("branch", "--show-current").stdout.strip()

    def tearDown(self):
        def ignore_concurrent_removal(operation, path, exc_info):
            """Tolerate Git removing a packed object during Python 3.10 cleanup."""
            if isinstance(exc_info[1], FileNotFoundError):
                return
            raise exc_info[1]

        shutil.rmtree(self.root, onerror=ignore_concurrent_removal)

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.root), *args], check=True, capture_output=True, text=True)

    def commit_schema(self, branch, body, message):
        self.git("checkout", "-qb", branch)
        (self.root / "schema.lds").write_text(body)
        self.git("add", "schema.lds")
        self.git("commit", "-qm", message)

    def test_disjoint_entity_additions_are_safe(self):
        self.commit_schema("remote", BASE + "\nENTITY: Phone\nATTRIBUTES:\n  - .id (UUID)\n", "remote")
        self.git("checkout", "-q", self.base_branch)
        (self.root / "schema.lds").write_text(BASE + "\nENTITY: Email\nATTRIBUTES:\n  - .id (UUID)\n")
        self.git("add", "schema.lds")
        self.git("commit", "-qm", "local")
        report = check_repository(self.root, "remote")
        self.assertTrue(report["safe"], report)
        self.assertEqual([], report["conflicts"])

    def test_overlapping_entity_change_requires_review(self):
        self.commit_schema("remote", BASE.replace("name (String)", "name (Text)"), "remote")
        self.git("checkout", "-q", self.base_branch)
        (self.root / "schema.lds").write_text(BASE.replace("name (String)", "name (UUID)"))
        self.git("add", "schema.lds")
        self.git("commit", "-qm", "local")
        report = check_repository(self.root, "remote")
        self.assertFalse(report["safe"])
        self.assertEqual("entity", report["conflicts"][0]["kind"])

    def test_disjoint_attribute_additions_to_one_entity_are_safe(self):
        ours = BASE + "  - email (String)\n"
        theirs = BASE + "  - phone (String)\n"
        merged, report = safe_merge(BASE, ours, theirs)
        self.assertTrue(report["safe"], report)
        self.assertIn("email (String)", merged)
        self.assertIn("phone (String)", merged)

    def test_lossy_lds_construct_refuses_automatic_merge(self):
        external = "EXTERNAL_ENTITY: catalog::Person {\n  .id UUID\n}\n"
        merged, report = safe_merge(external, external, external)
        self.assertIsNone(merged)
        self.assertFalse(report["safe"])
        self.assertEqual("syntax", report["conflicts"][0]["kind"])

    def test_resolution_writes_evidence_and_applies_selected_state(self):
        self.commit_schema("remote", BASE.replace("name (String)", "name (Text)"), "remote")
        self.git("checkout", "-q", self.base_branch)
        (self.root / "schema.lds").write_text(BASE.replace("name (String)", "name (UUID)"))
        self.git("add", "schema.lds")
        self.git("commit", "-qm", "local")
        report = assess_repository(self.root, "remote")
        report_path = write_assessment(self.root, report)
        evidence = resolve_conflict(
            self.root,
            report_path.relative_to(self.root).as_posix(),
            report["conflicts"][0]["id"],
            "theirs",
            "Reviewed remote type is authoritative",
            apply=True,
        )
        self.assertTrue(evidence.is_file())
        self.assertIn("name (Text)", (self.root / "schema.lds").read_text())
        self.assertEqual("theirs", json.loads(evidence.read_text())["strategy"])

    def test_stale_resolution_cannot_apply_or_write_evidence(self):
        self.commit_schema("remote", BASE.replace("name (String)", "name (Text)"), "remote")
        self.git("checkout", "-q", self.base_branch)
        (self.root / "schema.lds").write_text(BASE.replace("name (String)", "name (UUID)"))
        self.git("add", "schema.lds")
        self.git("commit", "-qm", "local")
        report = assess_repository(self.root, "remote")
        report_path = write_assessment(self.root, report)
        (self.root / "README.md").write_text("newer state\n")
        self.git("add", "README.md")
        self.git("commit", "-qm", "newer")
        with self.assertRaisesRegex(ValueError, "assessment is stale"):
            resolve_conflict(
                self.root, report_path.relative_to(self.root).as_posix(), report["conflicts"][0]["id"],
                "theirs", "must not apply", apply=True,
            )
        self.assertFalse((self.root / ".etg/evidence/resolutions").exists())

    def test_freshness_detects_new_commit(self):
        self.commit_schema("remote", BASE + "\nENTITY: Phone\nATTRIBUTES:\n  - .id (UUID)\n", "remote")
        self.git("checkout", "-q", self.base_branch)
        (self.root / "schema.lds").write_text(BASE + "\nENTITY: Email\nATTRIBUTES:\n  - .id (UUID)\n")
        self.git("add", "schema.lds")
        self.git("commit", "-qm", "local")
        report = assess_repository(self.root, "remote")
        path = write_assessment(self.root, report)
        self.assertTrue(assessment_is_fresh(self.root, path.relative_to(self.root).as_posix(), "remote")["fresh"])
        (self.root / "README.md").write_text("new commit\n")
        self.git("add", "README.md")
        self.git("commit", "-qm", "new work")
        self.assertFalse(assessment_is_fresh(self.root, path.relative_to(self.root).as_posix(), "remote")["fresh"])

    def test_merge_driver_refuses_semantic_conflict(self):
        base = self.root / "base.lds"
        ours = self.root / "ours.lds"
        theirs = self.root / "theirs.lds"
        base.write_text(BASE)
        ours.write_text(BASE.replace("name (String)", "name (UUID)"))
        theirs.write_text(BASE.replace("name (String)", "name (Text)"))
        self.assertEqual(1, run_merge_driver(str(base), str(ours), str(theirs)))

    def test_staged_check_reads_index_not_unstaged_worktree(self):
        staged = BASE + "\nENTITY: Email\nATTRIBUTES:\n  - .id (UUID)\n"
        (self.root / "schema.lds").write_text(staged)
        self.git("add", "schema.lds")
        (self.root / "schema.lds").write_text("<<<<<<< unresolved\n")
        report = check_repository(self.root, "HEAD", staged=True)
        self.assertTrue(report["safe"], report)
        self.assertEqual(["schema.lds"], report["paths"])

    def test_install_preserves_and_marks_hook(self):
        hook = self.root / ".git/hooks/pre-commit"
        hook.write_text("#!/bin/sh\necho existing\n")
        result = install(self.root, merge_driver=True, ci_github=True)
        self.assertTrue(result["merge_driver"])
        self.assertTrue(result["ci"])
        content = hook.read_text()
        self.assertIn("echo existing", content)
        self.assertIn("entigram git governance", content)
        self.assertTrue((self.root / ".github/workflows/entigram-governance.yml").is_file())


if __name__ == "__main__":
    unittest.main()
