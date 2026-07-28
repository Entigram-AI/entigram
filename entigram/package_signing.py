import base64
import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey


MANIFEST_NAME = "package.manifest.json"
SIGNATURE_NAME = "package.manifest.sig"
CATALOG_SIGNATURE_SUFFIX = ".sig"


@dataclass
class PackageVerification:
    ok: bool
    package: str
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    manifest_sha256: Optional[str] = None
    key_id: Optional[str] = None


def canonical_json_bytes(payload: Dict[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def create_package_manifest(
    package_dir: str,
    package_metadata: Optional[Dict[str, Any]] = None,
    *,
    manifest_version: int = 2,
) -> Dict[str, Any]:
    if manifest_version not in {1, 2}:
        raise ValueError(f"unsupported package manifest version: {manifest_version}")
    root = Path(package_dir).expanduser().resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"package directory not found: {package_dir}")
    files = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or _should_skip(path, root, manifest_version):
            continue
        rel = path.relative_to(root).as_posix()
        data = path.read_bytes()
        files.append({
            "path": rel,
            "sha256": hashlib.sha256(data).hexdigest(),
            "size_bytes": len(data),
        })
    manifest = {
        "manifest_version": manifest_version,
        "package": package_metadata.get("name") if package_metadata else root.name,
        "metadata": package_metadata or {},
        "files": files,
    }
    manifest["sha256"] = hashlib.sha256(canonical_json_bytes({
        "manifest_version": manifest["manifest_version"],
        "package": manifest["package"],
        "metadata": manifest["metadata"],
        "files": manifest["files"],
    })).hexdigest()
    return manifest


def write_package_manifest(package_dir: str, manifest: Dict[str, Any], out: Optional[str] = None) -> Path:
    root = Path(package_dir).expanduser().resolve()
    manifest_path = Path(out).expanduser().resolve() if out else root / MANIFEST_NAME
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return manifest_path


def sign_package_manifest(package_dir: str, key_path: Optional[str] = None) -> Dict[str, Any]:
    root = Path(package_dir).expanduser().resolve()
    manifest_path = root / MANIFEST_NAME
    if not manifest_path.is_file():
        raise FileNotFoundError(f"package manifest not found: {manifest_path}")
    manifest = json.loads(manifest_path.read_text())
    canonical = canonical_json_bytes(manifest)
    private_key, resolved_key_path = load_or_create_private_key(key_path)
    public_key = private_key.public_key()
    public_key_bytes = public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    signature = private_key.sign(canonical)
    payload = {
        "signature_version": 1,
        "signature_type": "ed25519",
        "signed_artifact": MANIFEST_NAME,
        "manifest_sha256": hashlib.sha256(canonical).hexdigest(),
        "public_key": base64.b64encode(public_key_bytes).decode("ascii"),
        "key_id": hashlib.sha256(public_key_bytes).hexdigest(),
        "signature": base64.b64encode(signature).decode("ascii"),
    }
    (root / SIGNATURE_NAME).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    payload["signing_key_path"] = str(resolved_key_path)
    return payload


def verify_package(
    package_dir: str,
    require_signature: bool = True,
    trusted_key_ids: Optional[Iterable[str]] = None,
) -> PackageVerification:
    root = Path(package_dir).expanduser().resolve()
    manifest_path = root / MANIFEST_NAME
    signature_path = root / SIGNATURE_NAME
    result = PackageVerification(ok=True, package=root.name)
    if not manifest_path.is_file():
        result.ok = False
        result.errors.append(f"missing {MANIFEST_NAME}")
        return result
    try:
        manifest = json.loads(manifest_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        result.ok = False
        result.errors.append(f"invalid {MANIFEST_NAME}: {exc}")
        return result
    shape_errors = _validate_manifest_shape(manifest)
    if shape_errors:
        result.ok = False
        result.errors.extend(shape_errors)
        return result
    result.package = manifest.get("package", root.name)
    try:
        expected = create_package_manifest(
            str(root),
            manifest.get("metadata") or {},
            manifest_version=manifest.get("manifest_version", 1),
        )
    except Exception as exc:
        result.ok = False
        result.errors.append(str(exc))
        return result

    if manifest.get("sha256") != expected.get("sha256"):
        result.errors.append("manifest sha256 mismatch")
    file_errors = _compare_manifest_files(manifest, expected)
    result.errors.extend(file_errors)
    if result.errors:
        result.ok = False

    canonical = canonical_json_bytes(manifest)
    result.manifest_sha256 = hashlib.sha256(canonical).hexdigest()
    if not signature_path.is_file():
        if require_signature:
            result.ok = False
            result.errors.append(f"missing {SIGNATURE_NAME}")
        else:
            result.warnings.append(f"missing {SIGNATURE_NAME}")
        return result

    try:
        signature = json.loads(signature_path.read_text())
        _verify_signature_payload(
            signature,
            canonical,
            expected_artifact=MANIFEST_NAME,
        )
        result.key_id = signature.get("key_id")
        if trusted_key_ids is not None and result.key_id not in set(trusted_key_ids):
            raise ValueError(f"untrusted package signing key: {result.key_id}")
    except Exception as exc:
        result.ok = False
        result.errors.append(str(exc))
    return result


def sign_catalog(catalog_path: str, key_path: Optional[str] = None, out: Optional[str] = None) -> Dict[str, Any]:
    path = Path(catalog_path).expanduser().resolve()
    catalog = json.loads(path.read_text())
    canonical = canonical_json_bytes(catalog)
    private_key, resolved_key_path = load_or_create_private_key(key_path)
    public_key = private_key.public_key()
    public_key_bytes = public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    payload = {
        "signature_version": 1,
        "signature_type": "ed25519",
        "signed_artifact": path.name,
        "catalog_sha256": hashlib.sha256(canonical).hexdigest(),
        "public_key": base64.b64encode(public_key_bytes).decode("ascii"),
        "key_id": hashlib.sha256(public_key_bytes).hexdigest(),
        "signature": base64.b64encode(private_key.sign(canonical)).decode("ascii"),
    }
    sig_path = Path(out).expanduser().resolve() if out else path.with_suffix(path.suffix + CATALOG_SIGNATURE_SUFFIX)
    sig_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    payload["signature_path"] = str(sig_path)
    payload["signing_key_path"] = str(resolved_key_path)
    return payload


def verify_catalog(catalog_path: str, signature_path: Optional[str] = None) -> Dict[str, Any]:
    path = Path(catalog_path).expanduser().resolve()
    sig_path = Path(signature_path).expanduser().resolve() if signature_path else path.with_suffix(path.suffix + CATALOG_SIGNATURE_SUFFIX)
    if not sig_path.is_file():
        return {"ok": False, "errors": [f"missing catalog signature: {sig_path}"], "key_id": None}
    catalog = json.loads(path.read_text())
    canonical = canonical_json_bytes(catalog)
    signature = json.loads(sig_path.read_text())
    errors = []
    try:
        _verify_signature_payload(
            signature,
            canonical,
            expected_artifact=path.name,
        )
    except Exception as exc:
        errors.append(str(exc))
    return {"ok": not errors, "errors": errors, "key_id": signature.get("key_id")}


def load_or_create_private_key(key_path: Optional[str] = None) -> tuple[Ed25519PrivateKey, Path]:
    path = Path(key_path).expanduser() if key_path else Path(".etg/package_signing_ed25519_private.pem")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        key = serialization.load_pem_private_key(path.read_bytes(), password=None)
        if not isinstance(key, Ed25519PrivateKey):
            raise ValueError(f"package signing key is not an Ed25519 private key: {path}")
        return key, path

    private_key = Ed25519PrivateKey.generate()
    data = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    path.write_bytes(data)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return private_key, path


def _should_skip(path: Path, root: Path, manifest_version: int) -> bool:
    rel_parts = path.relative_to(root).parts
    rel = path.relative_to(root).as_posix()
    if any(part in {".git", "__pycache__"} for part in rel_parts):
        return True
    if rel in {MANIFEST_NAME, SIGNATURE_NAME}:
        return True
    if rel.endswith(".pyc") or rel.endswith(".pyo") or rel.endswith(".DS_Store"):
        return True
    if ".etg" in rel_parts and not (
        manifest_version >= 2 and rel == ".etg/entigram.yaml"
    ):
        return True
    return False


def _compare_manifest_files(manifest: Dict[str, Any], expected: Dict[str, Any]) -> List[str]:
    errors = []
    actual_files = {item.get("path"): item for item in manifest.get("files", [])}
    expected_files = {item.get("path"): item for item in expected.get("files", [])}
    for path in sorted(set(expected_files) - set(actual_files)):
        errors.append(f"manifest missing file: {path}")
    for path in sorted(set(actual_files) - set(expected_files)):
        errors.append(f"manifest contains file not present in package: {path}")
    for path in sorted(set(actual_files) & set(expected_files)):
        actual = actual_files[path]
        expected_item = expected_files[path]
        if actual.get("sha256") != expected_item.get("sha256"):
            errors.append(f"sha256 mismatch: {path}")
        if actual.get("size_bytes") != expected_item.get("size_bytes"):
            errors.append(f"size mismatch: {path}")
    return errors


def _validate_manifest_shape(manifest: Any) -> List[str]:
    if not isinstance(manifest, dict):
        return ["package manifest must be a JSON object"]
    errors = []
    if manifest.get("manifest_version") not in {1, 2}:
        errors.append("unsupported package manifest version")
    if not isinstance(manifest.get("package"), str) or not manifest.get("package"):
        errors.append("package must be a non-empty string")
    if not isinstance(manifest.get("metadata"), dict):
        errors.append("metadata must be an object")
    files = manifest.get("files")
    if not isinstance(files, list):
        errors.append("files must be a list")
        return errors
    paths = []
    for item in files:
        if not isinstance(item, dict):
            errors.append("files entries must be objects")
            continue
        path = item.get("path")
        if not isinstance(path, str) or not path:
            errors.append("files entries must contain a non-empty path")
        else:
            paths.append(path)
        if not isinstance(item.get("sha256"), str) or not item.get("sha256"):
            errors.append(f"files entry has invalid sha256: {path}")
        if not isinstance(item.get("size_bytes"), int) or item.get("size_bytes") < 0:
            errors.append(f"files entry has invalid size_bytes: {path}")
    if len(paths) != len(set(paths)):
        errors.append("files entries must have unique paths")
    return errors


def _verify_signature_payload(
    signature: Dict[str, Any],
    canonical: bytes,
    *,
    expected_artifact: str,
) -> None:
    if signature.get("signature_version") != 1:
        raise ValueError("unsupported signature version")
    if signature.get("signature_type") != "ed25519":
        raise ValueError("unsupported signature type")
    if signature.get("signed_artifact") != expected_artifact:
        raise ValueError("unexpected signed artifact")
    expected_digest = signature.get("manifest_sha256") or signature.get("catalog_sha256")
    actual_digest = hashlib.sha256(canonical).hexdigest()
    if expected_digest != actual_digest:
        raise ValueError("signed artifact sha256 mismatch")
    public_key_bytes = base64.b64decode(signature.get("public_key", ""))
    if signature.get("key_id") != hashlib.sha256(public_key_bytes).hexdigest():
        raise ValueError("signature key id mismatch")
    signature_bytes = base64.b64decode(signature.get("signature", ""))
    public_key = Ed25519PublicKey.from_public_bytes(public_key_bytes)
    try:
        public_key.verify(signature_bytes, canonical)
    except InvalidSignature as exc:
        raise ValueError("invalid signature") from exc
