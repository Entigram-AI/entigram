import json
import shutil
import tempfile
import unittest
from pathlib import Path

from entigram.package_signing import (
    MANIFEST_NAME,
    SIGNATURE_NAME,
    create_package_manifest,
    sign_catalog,
    sign_package_manifest,
    verify_catalog,
    verify_package,
    write_package_manifest,
)


class TestPackageSigning(unittest.TestCase):
    def setUp(self):
        self.test_dir = Path(tempfile.mkdtemp())
        self.package_dir = self.test_dir / "@entigram" / "demo"
        self.package_dir.mkdir(parents=True)
        (self.package_dir / "schema.lds").write_text("ENTITY: Demo\nATTRIBUTES:\n  - id (String, PK)\n")
        (self.package_dir / "source_adapter.py").write_text("ADAPTER_NAME = 'demo'\n")
        (self.package_dir / "__pycache__").mkdir()
        (self.package_dir / "__pycache__" / "source_adapter.cpython-312.pyc").write_bytes(b"ignored")
        (self.package_dir / ".etg").mkdir()
        (self.package_dir / ".etg" / "package_signing_ed25519_private.pem").write_text("ignored")
        (self.package_dir / ".etg" / "entigram.yaml").write_text("local workspace state")
        self.key_path = self.test_dir / "keys" / "package.pem"

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    def test_manifest_is_deterministic_and_covers_package_descriptor(self):
        metadata = {"name": "@entigram/demo", "title": "Demo"}
        manifest = create_package_manifest(str(self.package_dir), metadata)
        write_package_manifest(str(self.package_dir), manifest)
        (self.package_dir / SIGNATURE_NAME).write_text("{}\n")

        regenerated = create_package_manifest(str(self.package_dir), metadata)

        self.assertEqual(manifest, regenerated)
        paths = {item["path"] for item in manifest["files"]}
        self.assertEqual(paths, {".etg/entigram.yaml", "schema.lds", "source_adapter.py"})
        self.assertEqual(manifest["package"], "@entigram/demo")

    def test_manifest_ignores_generated_package_archive(self):
        (self.package_dir / "package.tar.gz").write_bytes(b"generated archive")
        manifest = create_package_manifest(str(self.package_dir), {"name": "@entigram/demo"})
        self.assertNotIn("package.tar.gz", {item["path"] for item in manifest["files"]})

    def test_v1_manifest_retains_archive_compatibility(self):
        (self.package_dir / "package.tar.gz").write_bytes(b"legacy archive")
        manifest = create_package_manifest(
            str(self.package_dir),
            {"name": "@entigram/demo"},
            manifest_version=1,
        )
        self.assertIn("package.tar.gz", {item["path"] for item in manifest["files"]})

    def test_manifest_ignores_macos_resource_forks(self):
        (self.package_dir / "._schema.lds").write_bytes(b"resource fork")
        (self.package_dir / "__MACOSX").mkdir()
        (self.package_dir / "__MACOSX" / "._schema.lds").write_bytes(b"resource fork")
        manifest = create_package_manifest(str(self.package_dir), {"name": "@entigram/demo"})
        paths = {item["path"] for item in manifest["files"]}
        self.assertNotIn("._schema.lds", paths)
        self.assertNotIn("__MACOSX/._schema.lds", paths)

    def test_package_signature_verifies_and_detects_tampering(self):
        manifest = create_package_manifest(str(self.package_dir), {"name": "@entigram/demo"})
        write_package_manifest(str(self.package_dir), manifest)
        signature = sign_package_manifest(str(self.package_dir), key_path=str(self.key_path))

        verification = verify_package(str(self.package_dir))
        self.assertTrue(verification.ok)
        self.assertEqual(verification.key_id, signature["key_id"])

        (self.package_dir / "schema.lds").write_text("ENTITY: Demo\nATTRIBUTES:\n  - id (String, PK)\n  - name (String)\n")
        verification = verify_package(str(self.package_dir))

        self.assertFalse(verification.ok)
        self.assertIn("manifest sha256 mismatch", verification.errors)
        self.assertTrue(any(error.startswith("sha256 mismatch: schema.lds") for error in verification.errors))

    def test_unsigned_package_can_warn_instead_of_failing(self):
        manifest = create_package_manifest(str(self.package_dir), {"name": "@entigram/demo"})
        write_package_manifest(str(self.package_dir), manifest)

        verification = verify_package(str(self.package_dir), require_signature=False)

        self.assertTrue(verification.ok)
        self.assertIn(f"missing {SIGNATURE_NAME}", verification.warnings)

    def test_v1_package_signature_remains_verifiable(self):
        manifest = create_package_manifest(
            str(self.package_dir),
            {"name": "@entigram/demo"},
            manifest_version=1,
        )
        write_package_manifest(str(self.package_dir), manifest)
        sign_package_manifest(str(self.package_dir), key_path=str(self.key_path))

        verification = verify_package(str(self.package_dir))

        self.assertTrue(verification.ok)

    def test_package_manifest_rejects_unsupported_version(self):
        with self.assertRaisesRegex(ValueError, "unsupported package manifest version"):
            create_package_manifest(
                str(self.package_dir),
                {"name": "@entigram/demo"},
                manifest_version=3,
            )

    def test_catalog_signature_verifies_and_detects_tampering(self):
        catalog_path = self.test_dir / "standard_package_catalog.json"
        catalog_path.write_text(json.dumps({"packages": [{"name": "@entigram/demo"}]}, indent=2))
        signature = sign_catalog(str(catalog_path), key_path=str(self.key_path))

        verification = verify_catalog(str(catalog_path))
        self.assertTrue(verification["ok"])
        self.assertEqual(verification["key_id"], signature["key_id"])

        catalog_path.write_text(json.dumps({"packages": [{"name": "@entigram/changed"}]}, indent=2))
        verification = verify_catalog(str(catalog_path))

        self.assertFalse(verification["ok"])
        self.assertIn("signed artifact sha256 mismatch", verification["errors"])

    def test_package_signature_rejects_mismatched_key_id(self):
        manifest = create_package_manifest(str(self.package_dir), {"name": "@entigram/demo"})
        write_package_manifest(str(self.package_dir), manifest)
        sign_package_manifest(str(self.package_dir), key_path=str(self.key_path))
        signature_path = self.package_dir / SIGNATURE_NAME
        signature = json.loads(signature_path.read_text())
        signature["key_id"] = "not-the-public-key-id"
        signature_path.write_text(json.dumps(signature))

        verification = verify_package(str(self.package_dir))

        self.assertFalse(verification.ok)
        self.assertIn("signature key id mismatch", verification.errors)

    def test_package_signature_rejects_wrong_artifact_label(self):
        manifest = create_package_manifest(str(self.package_dir), {"name": "@entigram/demo"})
        write_package_manifest(str(self.package_dir), manifest)
        sign_package_manifest(str(self.package_dir), key_path=str(self.key_path))
        signature_path = self.package_dir / SIGNATURE_NAME
        signature = json.loads(signature_path.read_text())
        signature["signed_artifact"] = "standard_package_catalog.json"
        signature_path.write_text(json.dumps(signature))

        verification = verify_package(str(self.package_dir))

        self.assertFalse(verification.ok)
        self.assertIn("unexpected signed artifact", verification.errors)


if __name__ == "__main__":
    unittest.main()
