"""Execute shared hooks against real main and linked Git worktrees."""

import json
import os
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from entigram.injector import inject_entigram_manifest
from entigram.workspace_lifecycle import (
    GIT_CHECKIN_GUARD_END,
    GIT_CHECKIN_GUARD_START,
    PAUSE_GIT_HOOK_END,
    PAUSE_GIT_HOOK_START,
    WorkspaceLifecycleError,
    establish_active_change_baseline,
    install_workspace_git_checkin_guard,
    pause_workspace,
    remove_workspace_git_checkin_guard,
    resume_workspace,
    workspace_git_checkin_guard_status,
)


class TestPortableGitGuards(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.main = self.root / "main workspace"
        self.linked = self.root / "linked workspace"
        self.main.mkdir()
        self.env = dict(os.environ)
        for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE", "GIT_PREFIX"):
            self.env.pop(name, None)
        self.env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
        self.git(self.main, "init", "--quiet")
        self.git(self.main, "config", "user.name", "Fixture")
        self.git(self.main, "config", "user.email", "fixture@example.invalid")
        (self.main / "data.txt").write_text("base\n")
        self.git(self.main, "add", "data.txt")
        self.git(self.main, "commit", "--quiet", "-m", "Fixture base")
        self.git(self.main, "worktree", "add", "--quiet", "-b", "linked-fixture", str(self.linked))
        for workspace in (self.main, self.linked):
            self.assertTrue(inject_entigram_manifest(str(workspace), ["Entigram Schemas"], "Codex"))
            manifest_path = workspace / ".etg/entigram.yaml"
            manifest = yaml.safe_load(manifest_path.read_text())
            manifest.setdefault("lifecycle", {})["change_budget"] = {"max_changed_files": 1, "warn_changed_files": 1}
            manifest_path.write_text(yaml.safe_dump(manifest, sort_keys=False))
        hook = Path(self.git(self.main, "rev-parse", "--git-path", "hooks/pre-commit").stdout.strip())
        self.hook = hook if hook.is_absolute() else self.main / hook
        linked_hook = Path(self.git(self.linked, "rev-parse", "--git-path", "hooks/pre-commit").stdout.strip())
        self.assertEqual(linked_hook.resolve(), self.hook.resolve())
        self.hook.unlink(missing_ok=True)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        wrapper = self.bin / "etg"
        wrapper.write_text("#!/bin/sh\nexec " + shlex.quote(sys.executable) + " -m entigram.cli_runner.etg_cli \"$@\"\n")
        wrapper.chmod(0o755)
        source = Path(__file__).resolve().parents[1]
        self.env.update(PATH=str(self.bin) + os.pathsep + os.defpath, PYTHONPATH=str(source))
        self.log = self.root / "custom-hook.log"
        self.env["ENTIGRAM_TEST_HOOK_LOG"] = str(self.log)

    def tearDown(self):
        self.temporary.cleanup()

    def git(self, workspace, *arguments, check=True):
        return subprocess.run(["git", "-C", str(workspace), *arguments], env=self.env,
                              text=True, capture_output=True, check=check, timeout=30)

    def run_hook(self, workspace, env=None):
        return subprocess.run([str(self.hook)], cwd=workspace, env=env or self.env,
                              text=True, capture_output=True, timeout=30)

    def baseline(self, workspace):
        establish_active_change_baseline(workspace, reason="portable hook fixture")

    def custom_hook(self, exit_code=0):
        content = ("#!/bin/sh\n\n# Owner hook retains its spacing.\n"
                   "printf '%s\\n' \"$PWD\" >> \"$ENTIGRAM_TEST_HOOK_LOG\"\n"
                   + (f"exit {exit_code}\n" if exit_code else "") + "\n")
        self.hook.write_text(content)
        self.hook.chmod(0o750)
        return content

    def test_shared_hook_checks_current_worktree_with_dirty_states_reversed(self):
        custom = self.custom_hook()
        installed = install_workspace_git_checkin_guard(self.main)
        self.assertTrue(installed["changed"])
        for workspace in (self.main, self.linked):
            self.baseline(workspace)
        (self.linked / "data.txt").write_text("linked is dirty\n")

        self.assertEqual(self.run_hook(self.main).returncode, 0)
        self.assertNotEqual(self.run_hook(self.linked).returncode, 0)
        self.git(self.main, "commit", "--quiet", "--allow-empty", "-m", "Current main permitted")

        self.baseline(self.linked)
        (self.main / "data.txt").write_text("main is dirty\n")
        self.assertNotEqual(self.run_hook(self.main).returncode, 0)
        self.assertEqual(self.run_hook(self.linked).returncode, 0)
        self.git(self.linked, "commit", "--quiet", "--allow-empty", "-m", "Current linked permitted")
        self.assertTrue(self.hook.read_text().endswith(custom.partition("\n")[2]))
        self.assertEqual(stat.S_IMODE(self.hook.stat().st_mode), 0o750)
        self.assertEqual(set(self.log.read_text().splitlines()), {str(self.main), str(self.linked)})

    def test_paused_shared_guard_checks_active_other_worktree_and_preserves_mode(self):
        custom = self.custom_hook()
        install_workspace_git_checkin_guard(self.main)
        self.baseline(self.main)
        pause_workspace(self.linked, max_changed_files=1)
        (self.linked / "data.txt").write_text("paused modification\n")
        (self.linked / "additional.txt").write_text("over the paused budget\n")

        self.assertNotEqual(self.run_hook(self.linked).returncode, 0)
        active = self.run_hook(self.main)
        self.assertEqual(active.returncode, 0, active.stderr)
        self.assertEqual(stat.S_IMODE(self.hook.stat().st_mode), 0o750)
        resume_workspace(self.linked)
        self.assertNotIn(PAUSE_GIT_HOOK_START, self.hook.read_text())
        self.assertTrue(self.hook.read_text().endswith(custom.partition("\n")[2]))

    def test_upgrade_existing_legacy_block_is_idempotent_from_either_worktree(self):
        custom = self.custom_hook()
        legacy = legacy_guard(self.main) + "\n" + custom.partition("\n")[2]
        self.hook.write_text("#!/bin/sh\n" + legacy)
        result = install_workspace_git_checkin_guard(self.linked)
        self.assertTrue(result["changed"])
        self.assertNotIn(str(self.main), self.hook.read_text())
        before, mtime = self.hook.read_bytes(), self.hook.stat().st_mtime_ns
        self.assertFalse(install_workspace_git_checkin_guard(self.main)["changed"])
        self.assertFalse(install_workspace_git_checkin_guard(self.linked)["changed"])
        self.assertEqual(self.hook.read_bytes(), before)
        self.assertEqual(self.hook.stat().st_mtime_ns, mtime)
        self.assertEqual(stat.S_IMODE(self.hook.stat().st_mode), 0o750)

    def test_old_pause_backup_survives_live_guard_upgrade_without_being_rewritten(self):
        custom = self.custom_hook()
        install_workspace_git_checkin_guard(self.main)
        pause_workspace(self.linked)
        backup_path = self.linked / ".etg/lifecycle/pause-backup.json"
        backup = json.loads(backup_path.read_text())
        original = "#!/bin/sh\n" + legacy_guard(self.main) + "\n" + custom.partition("\n")[2]
        paused = "#!/bin/sh\n" + legacy_guard(self.linked, paused=True) + "\n" + original.partition("\n")[2]
        backup["git_hook"].update(original_content=original, paused_content=paused)
        backup_path.write_text(json.dumps(backup))
        self.hook.write_text(paused)
        backup_bytes = backup_path.read_bytes()

        self.assertTrue(install_workspace_git_checkin_guard(self.main)["changed"])
        self.assertEqual(backup_path.read_bytes(), backup_bytes)
        resume_workspace(self.linked)

        restored = self.hook.read_text()
        self.assertNotIn(PAUSE_GIT_HOOK_START, restored)
        self.assertNotIn(str(self.main), restored)
        self.assertNotIn(str(self.linked), restored)
        self.assertIn('change-status --dir "$entigram_worktree_root" --enforce', restored)
        self.assertTrue(restored.endswith(custom.partition("\n")[2]))
        self.assertEqual(stat.S_IMODE(self.hook.stat().st_mode), 0o750)

    def test_new_permanent_guard_survives_resume_of_pause_only_hook(self):
        for existing in (None, "#!/bin/sh\necho custom-hook\n"):
            with self.subTest(existing=existing):
                self.hook.unlink(missing_ok=True)
                if existing:
                    self.hook.write_text(existing)
                    self.hook.chmod(0o750)
                pause_workspace(self.linked)
                install_workspace_git_checkin_guard(self.main)
                resume_workspace(self.linked)
                self.assertTrue(self.hook.is_file())
                self.assertIn(GIT_CHECKIN_GUARD_START, self.hook.read_text())
                self.assertNotIn(PAUSE_GIT_HOOK_START, self.hook.read_text())
                if existing:
                    self.assertTrue(self.hook.read_text().endswith(existing.partition("\n")[2]))
                    self.assertEqual(stat.S_IMODE(self.hook.stat().st_mode), 0o750)

    def test_unknown_owned_edits_or_markers_are_not_overwritten(self):
        for content in ("#!/bin/sh\n" + legacy_guard(self.main).replace("--enforce", "--json"),
                        "#!/bin/sh\n" + GIT_CHECKIN_GUARD_START + "\nexit 0\n",
                        "#!/bin/sh\n" + legacy_guard(self.main) + legacy_guard(self.main)):
            with self.subTest(content=content):
                self.hook.write_text(content)
                self.hook.chmod(0o750)
                before, mtime = self.hook.read_bytes(), self.hook.stat().st_mtime_ns
                with self.assertRaises(WorkspaceLifecycleError) as rejected:
                    install_workspace_git_checkin_guard(self.linked)
                self.assertEqual(rejected.exception.code, "GIT_CHECKIN_GUARD_CHANGED")
                self.assertEqual(self.hook.read_bytes(), before)
                self.assertEqual(self.hook.stat().st_mtime_ns, mtime)
                self.assertEqual(stat.S_IMODE(self.hook.stat().st_mode), 0o750)

    def test_failed_root_or_tool_resolution_blocks_before_external_hook(self):
        self.custom_hook()
        install_workspace_git_checkin_guard(self.main)
        self.baseline(self.main)
        nonrepo = self.run_hook(self.root)
        self.assertNotEqual(nonrepo.returncode, 0)
        self.assertIn("cannot resolve", nonrepo.stderr)
        no_git = dict(self.env, PATH=str(self.bin))
        self.assertNotEqual(self.run_hook(self.main, no_git).returncode, 0)
        git_only = self.root / "git-only"
        git_only.mkdir()
        (git_only / "git").symlink_to(shutil.which("git"))
        no_etg = dict(self.env, PATH=str(git_only))
        missing = self.run_hook(self.main, no_etg)
        self.assertNotEqual(missing.returncode, 0)
        self.assertIn("requires the etg", missing.stderr)
        self.assertFalse(self.log.exists())

    def test_external_hook_exit_and_crlf_text_are_preserved(self):
        custom = "#!/bin/sh\r\n\r\n# Exact owner hook text.\r\nexit 7\r\n"
        # Keep the shell shebang native; the external suffix deliberately uses CRLF.
        custom = custom.replace("#!/bin/sh\r\n", "#!/bin/sh\n", 1)
        self.hook.write_bytes(custom.encode())
        self.hook.chmod(0o750)
        install_workspace_git_checkin_guard(self.main)
        self.assertTrue(self.hook.read_bytes().endswith(custom.partition("\n")[2].encode()))
        self.assertEqual(stat.S_IMODE(self.hook.stat().st_mode), 0o750)
        # A normal LF custom script retains its exit semantics as well.
        self.custom_hook(exit_code=7)
        install_workspace_git_checkin_guard(self.main)
        self.baseline(self.main)
        self.assertEqual(self.run_hook(self.main).returncode, 7)

    def test_owned_portable_crlf_block_is_repaired_without_changing_external_crlf(self):
        custom = b"#!/bin/sh\n# External suffix retains CRLF.\r\n"
        self.hook.write_bytes(custom)
        self.hook.chmod(0o750)
        install_workspace_git_checkin_guard(self.main)
        portable = self.hook.read_bytes()
        start = portable.index(GIT_CHECKIN_GUARD_START.encode())
        end = portable.index((GIT_CHECKIN_GUARD_END + "\n").encode()) + len(GIT_CHECKIN_GUARD_END) + 1
        self.hook.write_bytes(portable[:start] + portable[start:end].replace(b"\n", b"\r\n") + portable[end:])

        self.assertTrue(install_workspace_git_checkin_guard(self.linked)["changed"])

        self.assertEqual(self.hook.read_bytes(), portable)
        self.assertTrue(self.hook.read_bytes().endswith(custom.partition(b"\n")[2]))
        self.baseline(self.main)
        self.assertEqual(self.run_hook(self.main).returncode, 0)

    def test_nonexecutable_existing_hook_refuses_install_and_pause_without_mutation(self):
        custom = self.custom_hook()
        self.hook.chmod(0o640)
        manifest_path = self.linked / ".etg/entigram.yaml"
        manifest = manifest_path.read_bytes()
        for operation in (lambda: install_workspace_git_checkin_guard(self.main),
                          lambda: pause_workspace(self.linked)):
            with self.assertRaises(WorkspaceLifecycleError) as rejected:
                operation()
            self.assertEqual(rejected.exception.code, "GIT_HOOK_NOT_EXECUTABLE")
            self.assertEqual(self.hook.read_text(), custom)
            self.assertEqual(stat.S_IMODE(self.hook.stat().st_mode), 0o640)
            self.assertEqual(manifest_path.read_bytes(), manifest)
            self.assertFalse((self.linked / ".etg/lifecycle/pause-backup.json").exists())
        self.hook.chmod(0o750)
        install_workspace_git_checkin_guard(self.main)
        self.hook.chmod(0o640)
        portable = self.hook.read_bytes()
        self.assertFalse(workspace_git_checkin_guard_status(self.main)["installed"])
        with self.assertRaises(WorkspaceLifecycleError):
            install_workspace_git_checkin_guard(self.linked)
        self.assertEqual(self.hook.read_bytes(), portable)
        self.assertEqual(stat.S_IMODE(self.hook.stat().st_mode), 0o640)

    def test_hook_writes_preserve_bytes_with_windows_default_newline_translation(self):
        custom = b"#!/bin/sh\n# Owner suffix keeps CRLF.\r\n\r\n"
        self.hook.write_bytes(custom)
        self.hook.chmod(0o750)
        fdopen = os.fdopen

        def windows_fdopen(fd, mode, *arguments, **keywords):
            if "b" not in mode and keywords.get("newline") is None:
                keywords["newline"] = "\r\n"
            return fdopen(fd, mode, *arguments, **keywords)

        with patch("entigram.workspace_lifecycle.os.fdopen", side_effect=windows_fdopen):
            install_workspace_git_checkin_guard(self.main)
            portable = self.hook.read_bytes()
            self.assertTrue(portable.endswith(custom.partition(b"\n")[2]))
            self.assertNotIn(b"\r\r\n", portable)
            pause_workspace(self.linked)
            self.assertTrue(self.hook.read_bytes().endswith(custom.partition(b"\n")[2]))
            self.assertNotIn(b"\r\r\n", self.hook.read_bytes())
            resume_workspace(self.linked)
            self.assertEqual(self.hook.read_bytes(), portable)
            remove_workspace_git_checkin_guard(self.main)
        self.assertEqual(self.hook.read_bytes(), custom)
        self.assertEqual(stat.S_IMODE(self.hook.stat().st_mode), 0o750)


def legacy_guard(root, paused=False):
    """Exact guard format emitted by released 2.26.1, independent of new renderer."""
    start, end = ((PAUSE_GIT_HOOK_START, PAUSE_GIT_HOOK_END) if paused
                  else (GIT_CHECKIN_GUARD_START, GIT_CHECKIN_GUARD_END))
    comment = ("Entigram installs this guard only while workspace governance is paused." if paused
               else "Portable Entigram backstop for agents without a native lifecycle adapter.")
    name = "Entigram paused-workspace guard" if paused else "Entigram lifecycle guard"
    command = "pause-status" if paused else "change-status"
    variable = "entigram_pause_status" if paused else "entigram_change_status"
    message = ("Entigram requires a resume and hydrate check-in before this commit." if paused
               else "Entigram requires a handoff and current status before this commit.")
    return (f"{start}\n# {comment}\nif ! command -v etg >/dev/null 2>&1; then\n"
            f'  echo "{name} requires the etg command." >&2\n  exit 1\nfi\n'
            f"etg {command} --dir {shlex.quote(str(root))} --enforce\n{variable}=$?\n"
            f"if [ ${variable} -ne 0 ]; then\n"
            f'  echo "{message}" >&2\n  exit ${variable}\nfi\n{end}\n')


if __name__ == "__main__":
    unittest.main()
