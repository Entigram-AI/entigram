import os
import re
import shutil
import subprocess
import tarfile
import hashlib
import yaml
from pathlib import Path
from typing import List, Optional

from .package_signing import MANIFEST_NAME, SIGNATURE_NAME, verify_package


_PACKAGE_NAME_RE = re.compile(r"^(?:@[A-Za-z0-9_.-]+/)?[A-Za-z0-9_.-]+$")


def _safe_extract(tar: tarfile.TarFile, extract_dir: Path) -> None:
    """Extract ``tar`` into ``extract_dir`` while blocking path traversal.

    The Cloud API registry is trusted to serve well-formed package tarballs,
    but this guard prevents a compromised or malformed archive from writing
    outside the cache (e.g. ``../../etc/...`` or absolute entries). Symlinks
    and hardlinks are rejected outright since package payloads are plain
    schema/mapping files.
    """
    extract_base = extract_dir.resolve()
    for member in tar.getmembers():
        member_path = (extract_base / member.name).resolve()
        if member_path != extract_base and extract_base not in member_path.parents:
            raise RuntimeError(
                f"Refusing to extract tar entry outside target directory: {member.name}"
            )
        if member.issym() or member.islnk():
            raise RuntimeError(
                f"Refusing to extract link from package tarball: {member.name}"
            )
    try:
        # Prefer the stdlib ``data`` filter (Python 3.12+, backported to
        # 3.11.4+/3.10.13+/3.9.18+); it also strips unsafe metadata.
        tar.extractall(path=extract_dir, filter="data")
    except TypeError:
        # Older runtimes lack the ``filter`` argument; the member validation
        # above is the guard on those versions.
        tar.extractall(path=extract_dir)


class EntigramRegistry:
    """
    Manages package registries, allowing Entigram to pull standard or custom 
    packages from remote Git repositories.
    """
    def __init__(self, target_dir: str):
        self.target_dir = Path(target_dir).expanduser().resolve()
        self.etg_dir = self.target_dir / ".etg"
        self.manifest_path = self.etg_dir / "entigram.yaml"
        cache_root = os.environ.get("ENTIGRAM_REGISTRY_CACHE_DIR")
        self.global_cache_dir = (
            Path(cache_root).expanduser()
            if cache_root
            else Path.home() / ".etg" / "registry_cache"
        )
        self.global_cache_dir.mkdir(parents=True, exist_ok=True)
        self.default_registry = (
            "git@github.com:Entigram-AI/entigram-standard-packages.git"
        )

    def get_registries(self) -> List[str]:
        """Returns a list of all configured registries, including the default Cloud API."""
        # Primary: managed Entigram registry.
        regs = ["https://api.entigram.ai/v1/registry"]
        
        # Secondary: Community Git Registry
        if self.default_registry not in regs:
            regs.append(self.default_registry)
            
        if self.manifest_path.exists():
            try:
                with open(self.manifest_path, 'r') as f:
                    manifest = yaml.safe_load(f) or {}
                for r in manifest.get('registries', []):
                    if r not in regs:
                        regs.append(r)
            except Exception as e:
                print(f"Warning: Could not read registries from manifest: {e}")
        return regs

    def add_registry(self, url: str) -> bool:
        """Adds a new remote Git URL to the project manifest."""
        if not self.manifest_path.exists():
            print("❌ Entigram workspace not initialized. Run 'etg init' first.")
            return False
        try:
            with open(self.manifest_path, 'r') as f:
                manifest = yaml.safe_load(f) or {}
            regs = manifest.get('registries', [])
            if url not in regs:
                regs.append(url)
                manifest['registries'] = regs
                with open(self.manifest_path, 'w') as f:
                    yaml.dump(manifest, f, default_flow_style=False)
            return True
        except Exception as e:
            print(f"Error adding registry: {e}")
            return False

    def _get_auth_url(self, url: str) -> str:
        """Injects ENTIGRAM_TOKEN into HTTPS URLs if present."""
        token = os.environ.get("ENTIGRAM_TOKEN")
        if token and url.startswith("https://"):
            # Ensure we don't double-inject if it already has credentials
            if "@" not in url.split("://")[1]:
                return url.replace("https://", f"https://{token}@")
        return url

    def _fetch_registry(self, url: str) -> Optional[Path]:
        """Clones or pulls the latest version of the registry into the global cache."""
        if os.environ.get("ENTIGRAM_REGISTRY_OFFLINE") == "1":
            return None

        url_hash = hashlib.md5(url.encode()).hexdigest()
        cache_path = self.global_cache_dir / url_hash
        
        auth_url = self._get_auth_url(url)
        
        # Mask the URL for logging if it contains a token
        log_url = url
        if "@" in log_url and log_url.startswith("https://"):
             log_url = "https://***@" + log_url.split("@", 1)[1]
        elif "@" in auth_url and auth_url.startswith("https://"):
             log_url = "https://***@" + auth_url.split("@", 1)[1]
             
        try:
            if cache_path.exists():
                print(f"🔄 Updating registry cache: {log_url}")
                # We use --quiet to keep the CLI output clean
                # Set origin URL to auth URL before pulling, then revert
                subprocess.run(["git", "-C", str(cache_path), "remote", "set-url", "origin", auth_url], check=True)
                subprocess.run(["git", "-C", str(cache_path), "pull", "--quiet"], check=True)
                # Remove token from local git config
                subprocess.run(["git", "-C", str(cache_path), "remote", "set-url", "origin", url], check=True)
            else:
                print(f"📥 Cloning registry: {log_url}")
                subprocess.run(["git", "clone", "--quiet", auth_url, str(cache_path)], check=True)
                # Remove token from local git config
                subprocess.run(["git", "-C", str(cache_path), "remote", "set-url", "origin", url], check=True)
            return cache_path
        except subprocess.CalledProcessError as e:
            # Mask error message just in case the URL is included in the exception
            err_msg = str(e).replace(auth_url, log_url)
            print(f"❌ Failed to fetch registry {log_url}: {err_msg}")
            return None

    def _is_api_registry(self, url: str) -> bool:
        """Heuristic to check if the registry is a Entigram API endpoint vs a Git repo."""
        return url.startswith("http") and not url.endswith(".git") and "api.entigram.ai" in url

    def _fetch_api_package(self, api_url: str, package_name: str) -> Optional[Path]:
        """
        Fetches a package tarball from the Entigram Cloud API registry and
        extracts it into the cache. The registry is trusted to serve
        well-formed tarballs; _safe_extract blocks path traversal as a
        defense-in-depth guard.
        """
        if os.environ.get("ENTIGRAM_REGISTRY_OFFLINE") == "1":
            return None

        url_hash = hashlib.md5(f"{api_url}/{package_name}".encode()).hexdigest()
        cache_path = self.global_cache_dir / "api_cache" / url_hash
        
        # Managed delivery path for signed semantic mapping packages.
        print(f"🌐 [ENTIGRAM CLOUD] Resolving '{package_name}' via API: {api_url}")
        
        token = os.environ.get("ENTIGRAM_TOKEN")
        if not token:
            print("❌ ENTIGRAM_TOKEN is not set for Cloud API. Set it to download premium packages.")
            return None

        package_url = f"{api_url}/packages/{package_name}"
        
        try:
            import urllib.request
            
            req = urllib.request.Request(
                package_url, 
                headers={
                    "Authorization": token,
                    "User-Agent": "Entigram-CLI/1.0"
                }
            )
            with urllib.request.urlopen(req) as response:
                if response.status == 200:
                    cache_path.mkdir(parents=True, exist_ok=True)
                    tar_path = cache_path / "package.tar.gz"
                    with open(tar_path, "wb") as f:
                        f.write(response.read())
                    
                    extract_dir = cache_path / package_name
                    if extract_dir.exists():
                        shutil.rmtree(extract_dir)
                    extract_dir.mkdir(parents=True, exist_ok=True)
                    
                    with tarfile.open(tar_path, "r:gz") as tar:
                        _safe_extract(tar, extract_dir)
                    
                    return cache_path
                else:
                    print(f"❌ Failed to fetch from API: HTTP {response.status}")
                    return None
        except Exception as e:
            print(f"❌ API Fetch Error: {e}")
            return None

    def install_package(self, package_name: str) -> bool:
        """
        Searches all configured registries for a package and installs it 
        into the local .etg/packages directory. Locks the version.
        Supports namespaces e.g., @entigram/SupplyChain.
        """
        if os.environ.get("ENTIGRAM_REGISTRY_OFFLINE") == "1":
            return False
        if not _PACKAGE_NAME_RE.fullmatch(package_name):
            print(f"❌ Invalid package name: {package_name}")
            return False

        registries = self.get_registries()
        local_packages_dir = self.etg_dir / "packages"
        local_packages_dir.mkdir(parents=True, exist_ok=True)
        
        target_pkg_path = local_packages_dir / package_name
            
        for reg_url in registries:
            if self._is_api_registry(reg_url):
                cache_path = self._fetch_api_package(reg_url, package_name)
            else:
                cache_path = self._fetch_registry(reg_url)
                
            if not cache_path:
                continue
                
            source_pkg_path = cache_path / package_name
            
            # Legacy fallback: if package_name doesn't have a namespace, check @entigram/
            if not source_pkg_path.exists() and "/" not in package_name:
                source_pkg_path = cache_path / "@entigram" / package_name

            if source_pkg_path.exists() and source_pkg_path.is_dir():
                # Legacy packages without provenance remain installable. Once a
                # package advertises a manifest or signature, fail closed on any
                # invalid or incomplete provenance instead of copying it.
                has_provenance = (
                    (source_pkg_path / MANIFEST_NAME).exists()
                    or (source_pkg_path / SIGNATURE_NAME).exists()
                )
                if has_provenance:
                    verification = verify_package(str(source_pkg_path))
                    if not verification.ok:
                        print(
                            f"❌ Refusing unverified package '{package_name}': "
                            + "; ".join(verification.errors)
                        )
                        continue
                    accepted_names = {package_name}
                    if "/" not in package_name:
                        accepted_names.add(f"@entigram/{package_name}")
                    if verification.package not in accepted_names:
                        print(
                            f"❌ Refusing package identity mismatch: requested "
                            f"'{package_name}', signed manifest declares "
                            f"'{verification.package}'."
                        )
                        continue

                # Extract version from package's own manifest
                pkg_version = "latest"
                pkg_manifest = source_pkg_path / ".etg" / "entigram.yaml"
                if pkg_manifest.exists():
                    try:
                        with open(pkg_manifest, 'r') as f:
                            pm = yaml.safe_load(f) or {}
                            pkg_version = pm.get('version', '0.0.1')
                    except Exception as e:
                        print(f"Warning: Could not read package manifest for '{package_name}': {e}")

                if target_pkg_path.exists():
                    # If it exists, overwrite it (upgrade/reinstall)
                    shutil.rmtree(target_pkg_path)

                print(f"📦 Installing '{package_name}' (v{pkg_version}) from registry...")
                shutil.copytree(source_pkg_path, target_pkg_path)

                if not self._update_manifest(package_name, pkg_version):
                    print(f"❌ Package files copied, but the workspace manifest could not be updated.")
                    return False
                print(f"✅ Package '{package_name}' successfully locked to v{pkg_version}.")
                return True
        
        print(f"❌ Package '{package_name}' not found in any registered repositories.")
        return False
        
    def check_for_updates(self) -> dict:
        """
        Fetches the latest registries and compares remote package versions
        against the locked versions in the local manifest.
        Returns a dict of {package_name: {"current": v1, "latest": v2}}.
        """
        if not self.manifest_path.exists(): return {}
        
        try:
            with open(self.manifest_path, 'r') as f:
                manifest = yaml.safe_load(f) or {}
            
            local_pkgs = manifest.get('packages', {})
            if isinstance(local_pkgs, list):
                local_pkgs = {p: "latest" for p in local_pkgs}
        except Exception as e:
            print(f"Warning: Could not read local manifest for update check: {e}")
            return {}

        updates_available = {}
        registries = self.get_registries()
        
        for reg_url in registries:
            cache_path = self._fetch_registry(reg_url)
            if not cache_path: continue
            
            for pkg_name, current_version in local_pkgs.items():
                if pkg_name in updates_available: continue # already found an update
                
                source_pkg_path = cache_path / pkg_name
                # Legacy fallback check
                if not source_pkg_path.exists() and "/" not in pkg_name:
                    source_pkg_path = cache_path / "@entigram" / pkg_name

                if source_pkg_path.exists() and source_pkg_path.is_dir():
                    pkg_manifest = source_pkg_path / ".etg" / "entigram.yaml"
                    if pkg_manifest.exists():
                        try:
                            with open(pkg_manifest, 'r') as f:
                                pm = yaml.safe_load(f) or {}
                                remote_version = pm.get('version', '0.0.1')
                                
                                if remote_version != current_version and remote_version != "latest":
                                    updates_available[pkg_name] = {
                                        "current": current_version,
                                        "latest": remote_version
                                    }
                        except Exception as e:
                            print(f"Warning: Could not read remote manifest for '{pkg_name}': {e}")

        return updates_available

    def _update_manifest(self, package_name: str, version: str) -> bool:
        """Ensures the package is listed in the entigram.yaml packages dict."""
        if not self.manifest_path.exists():
            return False
        try:
            with open(self.manifest_path, 'r') as f:
                manifest = yaml.safe_load(f) or {}
            
            pkgs = manifest.get('packages', {})
            # Backwards compatibility: convert list to dict
            if isinstance(pkgs, list):
                pkgs = {p: "latest" for p in pkgs}
            if not isinstance(pkgs, dict):
                raise ValueError("packages must be a list or object")
                
            pkgs[package_name] = version
            manifest['packages'] = pkgs

            package_schema = self.etg_dir / "packages" / package_name / "schema.lds"
            if package_schema.exists():
                schema_path = package_schema.relative_to(self.target_dir).as_posix()
                schema_paths = manifest.setdefault("schema_paths", ["schema.lds"])
                if not isinstance(schema_paths, list):
                    raise ValueError("schema_paths must be a list")
                if schema_path not in schema_paths:
                    schema_paths.append(schema_path)
            
            with open(self.manifest_path, 'w') as f:
                yaml.dump(manifest, f, default_flow_style=False)
            return True
        except Exception as e:
            print(f"❌ Error updating manifest lockfile: {e}")
            return False
