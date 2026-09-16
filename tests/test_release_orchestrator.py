import datetime
import json
from pathlib import Path
import tempfile
import unittest

from scripts.release_orchestrator import (
    CommitInfo,
    bump_version,
    compile_release_notes,
    determine_bump_type,
    parse_commit_record,
    update_changelog,
    update_release_please_manifest,
)


class TestReleaseOrchestrator(unittest.TestCase):
    def test_parse_conventional_feat_with_pr(self):
        record = "abc1234567890\x1fAlice\x1ffeat(auth): add OAuth2 provider (#120)\x1fDetails about provider"
        info = parse_commit_record(record)
        self.assertIsNotNone(info)
        self.assertEqual(info.short_hash, "abc1234")
        self.assertEqual(info.author, "Alice")
        self.assertEqual(info.commit_type, "feat")
        self.assertEqual(info.scope, "auth")
        self.assertEqual(info.description, "add OAuth2 provider")
        self.assertEqual(info.pr_number, "120")
        self.assertFalse(info.is_breaking)

    def test_parse_conventional_breaking_exclamation(self):
        record = "def4567890123\x1fBob\x1ffix(api)!: remove deprecated v1 endpoint (#121)\x1fMigration notes"
        info = parse_commit_record(record)
        self.assertIsNotNone(info)
        self.assertEqual(info.commit_type, "fix")
        self.assertEqual(info.scope, "api")
        self.assertTrue(info.is_breaking)
        self.assertEqual(info.pr_number, "121")

    def test_parse_breaking_change_in_body(self):
        body = "Some description\n\nBREAKING CHANGE: The config format has changed."
        record = f"7890123456789\x1fCharlie\x1frefactor: rewrite configuration loader (#122)\x1f{body}"
        info = parse_commit_record(record)
        self.assertIsNotNone(info)
        self.assertTrue(info.is_breaking)
        self.assertEqual(info.breaking_description, "The config format has changed.")

    def test_determine_bump_type(self):
        # Empty commits
        self.assertEqual(determine_bump_type([]), (None, False))

        # Only chores
        chore = CommitInfo(
            commit_hash="1111111",
            short_hash="1111111",
            author="Dev",
            subject="chore: bump dependencies",
            body="",
            pr_number=None,
            commit_type="chore",
            scope=None,
            description="bump dependencies",
            is_breaking=False,
            breaking_description=None,
        )
        self.assertEqual(determine_bump_type([chore]), (None, False))

        # Fix commit -> patch
        fix = CommitInfo(
            commit_hash="2222222",
            short_hash="2222222",
            author="Dev",
            subject="fix: resolve timeout (#123)",
            body="",
            pr_number="123",
            commit_type="fix",
            scope=None,
            description="resolve timeout",
            is_breaking=False,
            breaking_description=None,
        )
        self.assertEqual(determine_bump_type([chore, fix]), ("patch", True))

        # Feat commit -> minor
        feat = CommitInfo(
            commit_hash="3333333",
            short_hash="3333333",
            author="Dev",
            subject="feat: new feature (#124)",
            body="",
            pr_number="124",
            commit_type="feat",
            scope=None,
            description="new feature",
            is_breaking=False,
            breaking_description=None,
        )
        self.assertEqual(determine_bump_type([chore, fix, feat]), ("minor", True))

        # Breaking commit -> major
        breaking = CommitInfo(
            commit_hash="4444444",
            short_hash="4444444",
            author="Dev",
            subject="feat!: breaking change (#125)",
            body="",
            pr_number="125",
            commit_type="feat",
            scope=None,
            description="breaking change",
            is_breaking=True,
            breaking_description="breaking change",
        )
        self.assertEqual(determine_bump_type([chore, fix, feat, breaking]), ("major", True))

        # Override takes precedence
        self.assertEqual(determine_bump_type([feat], override="patch"), ("patch", True))
        self.assertEqual(determine_bump_type([], override="major"), ("major", True))

    def test_bump_version(self):
        self.assertEqual(bump_version("2.21.1", "patch"), "2.21.2")
        self.assertEqual(bump_version("2.21.1", "minor"), "2.22.0")
        self.assertEqual(bump_version("2.21.1", "major"), "3.0.0")
        with self.assertRaises(ValueError):
            bump_version("invalid", "minor")
        with self.assertRaises(ValueError):
            bump_version("1.0.0", "unknown")

    def test_compile_release_notes(self):
        commits = [
            CommitInfo(
                commit_hash="abcdef123456",
                short_hash="abcdef1",
                author="Dev",
                subject="feat(governance): add direct release action (#108)",
                body="",
                pr_number="108",
                commit_type="feat",
                scope="governance",
                description="add direct release action",
                is_breaking=False,
                breaking_description=None,
            ),
            CommitInfo(
                commit_hash="123456abcdef",
                short_hash="123456a",
                author="Dev",
                subject="fix: handle empty tag list (#109)",
                body="",
                pr_number="109",
                commit_type="fix",
                scope=None,
                description="handle empty tag list",
                is_breaking=False,
                breaking_description=None,
            ),
        ]
        fixed_date = datetime.date(2026, 9, 16)
        notes = compile_release_notes(
            commits=commits,
            prev_tag="v2.21.1",
            next_version="2.22.0",
            repo_url="https://github.com/Entigram-AI/entigram",
            today=fixed_date,
        )
        self.assertIn("## [2.22.0](https://github.com/Entigram-AI/entigram/compare/v2.21.1...v2.22.0) (2026-09-16)", notes)
        self.assertIn("### Features", notes)
        self.assertIn("* **governance:** add direct release action ([#108](https://github.com/Entigram-AI/entigram/issues/108)) ([abcdef1](https://github.com/Entigram-AI/entigram/commit/abcdef123456))", notes)
        self.assertIn("### Bug Fixes", notes)
        self.assertIn("* handle empty tag list ([#109](https://github.com/Entigram-AI/entigram/issues/109)) ([123456a](https://github.com/Entigram-AI/entigram/commit/123456abcdef))", notes)

    def test_update_changelog_and_manifest(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            changelog = tmp_path / "CHANGELOG.md"
            changelog.write_text("# Changelog\n\n## Unreleased\n\n* Some unreleased work\n\n## [1.0.0] (2026-01-01)\n\n* Initial\n", encoding="utf-8")

            release_notes = "## [1.1.0] (2026-09-16)\n\n### Features\n\n* Cool feature\n"
            update_changelog(tmp_path, release_notes)

            updated = changelog.read_text(encoding="utf-8")
            self.assertIn("## Unreleased", updated)
            self.assertIn("## [1.1.0] (2026-09-16)", updated)
            self.assertIn("## [1.0.0] (2026-01-01)", updated)
            # Ensure 1.1.0 appears before 1.0.0
            self.assertTrue(updated.index("1.1.0") < updated.index("1.0.0"))

            update_release_please_manifest(tmp_path, "1.1.0")
            manifest_file = tmp_path / ".release-please-manifest.json"
            self.assertTrue(manifest_file.is_file())
            manifest_data = json.loads(manifest_file.read_text(encoding="utf-8"))
            self.assertEqual(manifest_data.get("."), "1.1.0")


if __name__ == "__main__":
    unittest.main()
