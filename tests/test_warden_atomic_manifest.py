import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

import yaml

from entigram.governance.warden import Warden


class TestAtomicWardenManifest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.manifest = self.root / ".etg" / "entigram.yaml"
        self.manifest.parent.mkdir()
        self.schema = self.root / "schema.lds"
        self.schema.write_text("ENTITY: WorkItem { id UUID PK }\n")
        initial = {"status": "initialized", "schema_paths": ["schema.lds"],
                   "retained": {"owner": "fixture"}, "last_locked": "2000-01-01 00:00:00"}
        self.manifest.write_text(yaml.safe_dump(initial))
        self.warden = Warden(str(self.root))
        self.fingerprint = self.warden.generate_fingerprint()
        initial["integrity_fingerprint"] = self.fingerprint
        self.manifest.write_text(yaml.safe_dump(initial))
        self.manifest.chmod(0o640)
        self.mode = stat.S_IMODE(self.manifest.stat().st_mode)
        self.original = self.manifest.read_bytes()

    def operation(self, name):
        if name == "lock":
            return self.warden.lock_fingerprint(require_existing_match=True,
                                                expected_fingerprint=self.fingerprint)
        return self.warden.unlock()

    def assert_unchanged(self):
        self.assertEqual(self.original, self.manifest.read_bytes())
        self.assertEqual(self.mode, stat.S_IMODE(self.manifest.stat().st_mode))
        self.assertEqual([self.manifest], list(self.manifest.parent.iterdir()),
                         "An unsuccessful write must not leave a temporary manifest")

    def assert_concurrent_read_is_complete(self, operation):
        serializing = threading.Event()
        release = threading.Event()
        errors = []
        original_dump = yaml.dump

        def paused_dump(*args, **kwargs):
            serializing.set()
            if not release.wait(5):
                raise TimeoutError("private serialization barrier was not released")
            return original_dump(*args, **kwargs)

        def write():
            try:
                self.operation(operation)
            except BaseException as error:
                errors.append(error)

        with patch("yaml.dump", side_effect=paused_dump):
            writer = threading.Thread(target=write)
            writer.start()
            try:
                self.assertTrue(serializing.wait(5), "Writer did not reach serialization")
                observed = self.manifest.read_bytes()
                parsed_during_write = yaml.safe_load(observed)
            finally:
                release.set()
                writer.join(5)
        self.assertFalse(writer.is_alive(), "Private writer did not finish")
        self.assertEqual([], errors)
        self.assertIsInstance(parsed_during_write, dict, "A concurrent reader saw a partial manifest")
        self.assertEqual(self.original, observed, "Publication must retain the complete old document until ready")
        updated = yaml.safe_load(self.manifest.read_bytes())
        self.assertEqual({"owner": "fixture"}, updated["retained"])
        self.assertEqual(self.mode, stat.S_IMODE(self.manifest.stat().st_mode))
        if operation == "lock":
            self.assertEqual(self.fingerprint, updated["integrity_fingerprint"])
            self.assertNotEqual("2000-01-01 00:00:00", updated["last_locked"])
            self.assertNotIn("integrity_unlock", updated)
            self.assertTrue(self.warden.verify_integrity(emit_human=False))
        else:
            self.assertNotIn("integrity_fingerprint", updated)
            self.assertNotIn("last_locked", updated)
            self.assertEqual(self.fingerprint, updated["integrity_unlock"]["previous_fingerprint"])
            self.assertTrue(self.warden.has_pending_contract_change())

    def test_lock_keeps_complete_old_manifest_visible_during_serialization(self):
        self.assert_concurrent_read_is_complete("lock")

    def test_unlock_keeps_complete_old_manifest_visible_during_serialization(self):
        self.assert_concurrent_read_is_complete("unlock")

    def test_serialization_failure_preserves_original_for_lock_and_unlock(self):
        def fail_after_partial_output(value, stream=None, **kwargs):
            if stream is not None:
                stream.write("incomplete: [")
            raise OSError("private serialization failure")

        for operation in ("lock", "unlock"):
            self.manifest.write_bytes(self.original)
            with self.subTest(operation=operation), patch("yaml.dump", side_effect=fail_after_partial_output):
                with self.assertRaisesRegex(OSError, "private serialization failure"):
                    self.operation(operation)
                self.assert_unchanged()

    def test_prepublication_fsync_and_replace_failures_preserve_original(self):
        for operation in ("lock", "unlock"):
            for boundary in ("fsync", "replace"):
                self.manifest.write_bytes(self.original)
                with self.subTest(operation=operation, boundary=boundary), \
                        patch("os." + boundary, side_effect=OSError("private " + boundary + " failure")):
                    with self.assertRaisesRegex(OSError, "private " + boundary + " failure"):
                        self.operation(operation)
                    self.assert_unchanged()

    @unittest.skipIf(os.name != "posix" or os.geteuid() == 0, "requires ordinary POSIX file permissions")
    def test_directory_write_permission_does_not_bypass_readonly_manifest(self):
        self.manifest.chmod(0o440)
        for operation in ("lock", "unlock"):
            with self.subTest(operation=operation), self.assertRaises(PermissionError):
                self.operation(operation)
            self.assertEqual(self.original, self.manifest.read_bytes())
            self.assertEqual(0o440, stat.S_IMODE(self.manifest.stat().st_mode))

    def test_followed_manifest_symlink_is_retained_and_updates_its_target(self):
        target = self.root / "linked-manifest.yaml"
        self.manifest.rename(target)
        try:
            self.manifest.symlink_to(target)
        except (NotImplementedError, OSError) as error:
            self.skipTest(f"Manifest symlink fixture is unavailable: {error}")
        link_inode = self.manifest.lstat().st_ino
        for operation in ("unlock", "lock"):
            self.operation(operation)
            self.assertTrue(self.manifest.is_symlink())
            self.assertEqual(link_inode, self.manifest.lstat().st_ino)
            self.assertEqual(str(target), os.readlink(self.manifest))
            self.assertEqual(target.read_bytes(), self.manifest.read_bytes())
            self.assertEqual(self.mode, stat.S_IMODE(target.stat().st_mode))
        self.assertTrue(self.warden.verify_integrity(emit_human=False))

    def test_existing_fingerprint_guards_still_reject_before_writing(self):
        self.schema.write_text("ENTITY: Changed { id UUID PK }\n")
        for arguments in ({"require_existing_match": True}, {"expected_fingerprint": self.fingerprint}):
            with self.subTest(arguments=arguments), self.assertRaises(RuntimeError):
                self.warden.lock_fingerprint(**arguments)
            self.assert_unchanged()

    def write_while_access_changes(self, operation, change):
        synced = threading.Event()
        release = threading.Event()
        errors = []
        original_fsync = os.fsync

        def pause_after_sync(fd):
            original_fsync(fd)
            synced.set()
            if not release.wait(5):
                raise TimeoutError("private access-change barrier was not released")

        def write():
            try:
                self.operation(operation)
            except BaseException as error:
                errors.append(error)

        with patch("os.fsync", side_effect=pause_after_sync):
            writer = threading.Thread(target=write)
            writer.start()
            try:
                self.assertTrue(synced.wait(5), f"Writer did not sync temporary data: {errors}")
                change()
            finally:
                release.set()
                writer.join(5)
        self.assertFalse(writer.is_alive())
        self.assertEqual(1, len(errors), "Access drift must stop publication")
        self.assertIsInstance(errors[0], OSError)

    @unittest.skipIf(os.name != "posix", "requires POSIX mode changes")
    def test_mode_tightening_after_fsync_is_not_restored_away(self):
        for operation in ("lock", "unlock"):
            self.manifest.chmod(self.mode)
            with self.subTest(operation=operation):
                self.write_while_access_changes(operation, lambda: self.manifest.chmod(0o440))
                self.assertEqual(self.original, self.manifest.read_bytes())
                self.assertEqual(0o440, stat.S_IMODE(self.manifest.stat().st_mode))

    @unittest.skipUnless(sys.platform == "darwin", "requires macOS ACLs")
    def test_acl_tightening_after_fsync_is_not_restored_away(self):
        for operation in ("lock", "unlock"):
            subprocess.run(["/bin/chmod", "-N", str(self.manifest)], check=True, capture_output=True)
            def tighten():
                subprocess.run(["/bin/chmod", "+a", "everyone deny write", str(self.manifest)], check=True, capture_output=True)
            with self.subTest(operation=operation):
                self.write_while_access_changes(operation, tighten)
                self.assert_unchanged()
                acl = subprocess.run(["/bin/ls", "-le", str(self.manifest)], check=True, capture_output=True, text=True).stdout
                self.assertIn("deny write", acl)

    def test_replaced_target_is_not_overwritten_using_old_inode_permissions(self):
        backup = self.root / "retained-original.yaml"
        replacement = yaml.safe_dump({"status": "initialized", "retained": {"owner": "replacement"}}).encode()
        def replace_target():
            self.manifest.rename(backup)
            self.manifest.write_bytes(replacement)
        self.write_while_access_changes("lock", replace_target)
        self.assertEqual(replacement, self.manifest.read_bytes())
        self.assertEqual(self.original, backup.read_bytes())
        self.assertEqual([self.manifest], list(self.manifest.parent.iterdir()))

    def test_hardlinked_manifest_is_refused_without_changing_either_name(self):
        alias = self.root / "same-inode.yaml"
        os.link(self.manifest, alias)
        for operation in ("lock", "unlock"):
            with self.subTest(operation=operation), self.assertRaises(OSError):
                self.operation(operation)
            self.assert_unchanged()
            self.assertEqual(self.original, alias.read_bytes())
            self.assertEqual(self.manifest.stat().st_ino, alias.stat().st_ino)

    @unittest.skipUnless(hasattr(os, "setxattr"), "requires file extended attributes")
    def test_existing_extended_attributes_survive_lock_and_unlock(self):
        key = "com.entigram.atomic-fixture" if sys.platform == "darwin" else "user.entigram.atomic-fixture"
        os.setxattr(self.manifest, key, b"retained-private-fixture")
        for operation in ("unlock", "lock"):
            self.operation(operation)
            self.assertEqual(b"retained-private-fixture", os.getxattr(self.manifest, key))

    @unittest.skipUnless(sys.platform == "darwin", "requires macOS ACLs")
    def test_native_macos_access_acl_is_retained(self):
        subprocess.run(["/bin/chmod", "+a", "everyone deny execute", str(self.manifest)], check=True, capture_output=True)
        def access_list():
            return subprocess.run(["/bin/ls", "-le", str(self.manifest)], check=True,
                                  capture_output=True, text=True).stdout.splitlines()[1:]
        expected = access_list()
        self.assertTrue(expected)
        for operation in ("unlock", "lock"):
            self.operation(operation)
            self.assertEqual(expected, access_list())

    @unittest.skipUnless(sys.platform == "darwin", "requires macOS ACLs")
    def test_native_macos_acl_write_denial_is_not_bypassed(self):
        subprocess.run(["/bin/chmod", "+a", "everyone deny write", str(self.manifest)], check=True, capture_output=True)
        for operation in ("lock", "unlock"):
            with self.subTest(operation=operation), self.assertRaises(PermissionError):
                self.operation(operation)
            self.assert_unchanged()

    @unittest.skipUnless(sys.platform == "darwin", "requires the macOS extended-attribute utility")
    def test_native_macos_extended_attribute_is_retained(self):
        key = "com.entigram.atomic-fixture"
        subprocess.run(["/usr/bin/xattr", "-w", key, "retained-private-fixture", str(self.manifest)],
                       check=True, capture_output=True)
        for operation in ("unlock", "lock"):
            self.operation(operation)
            result = subprocess.run(["/usr/bin/xattr", "-p", key, str(self.manifest)],
                                    check=True, capture_output=True)
            self.assertEqual(b"retained-private-fixture\n", result.stdout)

    @unittest.skipUnless(hasattr(os, "chflags") and hasattr(stat, "UF_HIDDEN"), "requires file flags")
    def test_unpreservable_file_flags_fail_without_dropping_them(self):
        original_flags = self.manifest.stat().st_flags
        os.chflags(self.manifest, original_flags | stat.UF_HIDDEN)
        self.addCleanup(os.chflags, self.manifest, original_flags)
        for operation in ("lock", "unlock"):
            with self.subTest(operation=operation), self.assertRaises(OSError):
                self.operation(operation)
            self.assert_unchanged()
            self.assertEqual(original_flags | stat.UF_HIDDEN, self.manifest.stat().st_flags)

    @unittest.skipUnless(sys.platform == "darwin", "requires the native macOS metadata copier")
    def test_native_metadata_copy_failure_leaves_original_untouched(self):
        import ctypes
        from types import SimpleNamespace

        native = ctypes.CDLL(None, use_errno=True)
        copier = SimpleNamespace(fgetattrlist=native.fgetattrlist,
                                 fcopyfile=Mock(side_effect=OSError("private metadata failure")))
        for operation in ("lock", "unlock"):
            with self.subTest(operation=operation), patch("ctypes.CDLL", return_value=copier):
                with self.assertRaisesRegex(OSError, "private metadata failure"):
                    self.operation(operation)
                self.assert_unchanged()


if __name__ == "__main__":
    unittest.main()
