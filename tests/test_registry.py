import io
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from entigram.registry import EntigramRegistry, _safe_extract


class TestSafeTarExtract(unittest.TestCase):
    def test_standard_worker_registry_is_available_without_cloud_token(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict("os.environ", {}, clear=True):
                registry = EntigramRegistry(tmp)
                self.assertEqual(
                    registry.get_registries(),
                    ["https://api.entigram.ai/v1/registry"],
                )

    def test_standard_worker_registry_is_used_with_cloud_token(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict("os.environ", {"ENTIGRAM_TOKEN": "test-token"}, clear=True):
                registry = EntigramRegistry(tmp)
                self.assertEqual(
                    registry.get_registries(),
                    ["https://api.entigram.ai/v1/registry"],
                )

    def test_configured_standard_registry_is_not_duplicated(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            etg_dir = target / ".etg"
            etg_dir.mkdir()
            (etg_dir / "entigram.yaml").write_text(
                "registries:\n  - https://api.entigram.ai/v1/registry\n"
            )
            with patch.dict("os.environ", {}, clear=True):
                registry = EntigramRegistry(tmp)
                self.assertEqual(registry.get_registries(), ["https://api.entigram.ai/v1/registry"])

    def test_install_without_token_uses_public_worker_registry(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict("os.environ", {}, clear=True):
                registry = EntigramRegistry(tmp)
                with (
                    patch.object(registry, "_fetch_api_package", return_value=None) as fetch_api,
                ):
                    self.assertFalse(registry.install_package("@entigram/data-privacy"))
                fetch_api.assert_called_once_with(
                    "https://api.entigram.ai/v1/registry", "@entigram/data-privacy"
                )

    def test_custom_git_registry_can_store_packages_under_community_packages(self):
        with tempfile.TemporaryDirectory() as tmp:
            registry = EntigramRegistry(tmp)
            package = Path(tmp) / "registry" / "community-packages" / "@example" / "demo"
            package.mkdir(parents=True)
            self.assertEqual(
                registry._find_package_path(Path(tmp) / "registry", "@example/demo"),
                package,
            )

    def _make_tar(self, members):
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            for name, data in members:
                info = tarfile.TarInfo(name=name)
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
        buf.seek(0)
        return tarfile.open(fileobj=buf, mode="r:gz")

    def test_safe_extract_rejects_parent_traversal(self):
        tar = self._make_tar([("../evil.txt", b"pwned")])
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(RuntimeError):
                _safe_extract(tar, Path(tmp))

    def test_safe_extract_rejects_absolute_path(self):
        tar = self._make_tar([("/etc/evil.txt", b"pwned")])
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(RuntimeError):
                _safe_extract(tar, Path(tmp))

    def test_safe_extract_rejects_symlink(self):
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            info = tarfile.TarInfo(name="link.txt")
            info.type = tarfile.SYMTYPE
            info.linkname = "../evil.txt"
            tar.addfile(info)
        buf.seek(0)
        tar = tarfile.open(fileobj=buf, mode="r:gz")
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(RuntimeError):
                _safe_extract(tar, Path(tmp))

    def test_safe_extract_allows_legitimate_files(self):
        tar = self._make_tar([
            ("pkg/file.txt", b"hello"),
            ("pkg/.etg/entigram.yaml", b"version: 1.0"),
        ])
        with tempfile.TemporaryDirectory() as tmp:
            _safe_extract(tar, Path(tmp))
            self.assertTrue((Path(tmp) / "pkg" / "file.txt").exists())
            self.assertTrue((Path(tmp) / "pkg" / ".etg" / "entigram.yaml").exists())

    def test_package_manifest_registers_installed_schema(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            etg_dir = target / ".etg"
            etg_dir.mkdir()
            (etg_dir / "entigram.yaml").write_text("packages: {}\nschema_paths:\n  - schema.lds\n")
            package_schema = etg_dir / "packages" / "@entigram" / "demo" / "schema.lds"
            package_schema.parent.mkdir(parents=True)
            package_schema.write_text("ENTITY: Demo\n")

            registry = EntigramRegistry(str(target))
            self.assertTrue(registry._update_manifest("@entigram/demo", "1.0.0"))

            manifest = yaml.safe_load((etg_dir / "entigram.yaml").read_text())
            self.assertIn(".etg/packages/@entigram/demo/schema.lds", manifest["schema_paths"])


if __name__ == "__main__":
    unittest.main()
