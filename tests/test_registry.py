import io
import os
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from entigram.injector import inject_entigram_manifest
from entigram.package_signing import (
    create_package_manifest,
    sign_package_manifest,
    write_package_manifest,
)
from entigram.registry import (
    OFFICIAL_PACKAGE_KEY_IDS,
    EntigramRegistry,
    _safe_extract,
)


class TestSafeTarExtract(unittest.TestCase):
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

    def test_remote_registry_requires_a_trusted_package_key(self):
        with tempfile.TemporaryDirectory() as workspace, tempfile.TemporaryDirectory() as remote:
            inject_entigram_manifest(workspace, ["Entigram Schemas"], "Codex")
            package = Path(remote, "@publisher", "demo")
            package.mkdir(parents=True)
            (package / "schema.lds").write_text("ENTITY Demo { id UUID PK }")
            manifest = create_package_manifest(
                str(package),
                {"name": "@publisher/demo"},
            )
            write_package_manifest(str(package), manifest)
            signature = sign_package_manifest(
                str(package),
                key_path=str(Path(remote, "publisher.pem")),
            )

            registry_url = "https://example.com/packages.git"
            registry = EntigramRegistry(workspace)
            registry.get_registries = lambda: [registry_url]
            registry._fetch_registry = lambda _url: Path(remote)

            with patch.dict(os.environ, {"ENTIGRAM_REGISTRY_OFFLINE": "0"}):
                self.assertFalse(registry.install_package("@publisher/demo"))

            manifest_path = Path(workspace, ".etg", "entigram.yaml")
            workspace_manifest = yaml.safe_load(manifest_path.read_text())
            workspace_manifest["registry_trust"] = {
                registry_url: {
                    "require_signature": True,
                    "trusted_key_ids": [signature["key_id"]],
                }
            }
            manifest_path.write_text(yaml.dump(workspace_manifest, default_flow_style=False))

            with patch.dict(os.environ, {"ENTIGRAM_REGISTRY_OFFLINE": "0"}):
                self.assertTrue(registry.install_package("@publisher/demo"))

    def test_official_registry_uses_pinned_key_without_hostname_spoofing(self):
        with tempfile.TemporaryDirectory() as workspace:
            inject_entigram_manifest(workspace, ["Entigram Schemas"], "Codex")
            registry = EntigramRegistry(workspace)

            policy = registry._registry_trust_policy(
                "https://api.entigram.ai/v1/registry"
            )

            self.assertTrue(policy["require_signature"])
            self.assertEqual(policy["trusted_key_ids"], set(OFFICIAL_PACKAGE_KEY_IDS))
            self.assertFalse(
                registry._is_official_registry(
                    "https://example.com/api.entigram.ai/v1/registry"
                )
            )

    def test_install_rejects_package_downgrade(self):
        with tempfile.TemporaryDirectory() as workspace, tempfile.TemporaryDirectory() as remote:
            inject_entigram_manifest(workspace, ["Entigram Schemas"], "Codex")
            package = Path(remote, "demo")
            package_manifest = package / ".etg" / "entigram.yaml"
            package_manifest.parent.mkdir(parents=True)
            package_manifest.write_text("version: 2.0.0\n")
            (package / "schema.lds").write_text("ENTITY Demo { id UUID PK }\n")

            registry = EntigramRegistry(workspace)
            registry.get_registries = lambda: [remote]
            registry._fetch_registry = lambda _url: Path(remote)

            with patch.dict(os.environ, {"ENTIGRAM_REGISTRY_OFFLINE": "0"}):
                self.assertTrue(registry.install_package("demo"))

            installed_schema = Path(
                workspace,
                ".etg",
                "packages",
                "demo",
                "schema.lds",
            )
            package_manifest.write_text("version: 1.9.0\n")
            (package / "schema.lds").write_text("ENTITY Downgraded { id UUID PK }\n")

            with patch.dict(os.environ, {"ENTIGRAM_REGISTRY_OFFLINE": "0"}):
                self.assertFalse(registry.install_package("demo"))

            self.assertIn("ENTITY Demo", installed_schema.read_text())
            workspace_manifest = yaml.safe_load(
                Path(workspace, ".etg", "entigram.yaml").read_text()
            )
            self.assertEqual(workspace_manifest["packages"]["demo"], "2.0.0")


if __name__ == "__main__":
    unittest.main()
