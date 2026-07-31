import hashlib
import json
import os
import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import List, Dict, Any, Optional
from ..schema_compiler.parser import SchemaParser

class SentinelScanner:
    """
    Sentinel package/schema policy linter.

    Sentinel checks LDS structure and configured heuristics. It is not a
    malware scanner, dependency audit, or general-purpose repository SAST.
    """
    def __init__(self, target_dir: str = "."):
        self.target_dir = Path(target_dir).expanduser().resolve()
        
        # Look in local project packages and the registry cache
        self.local_packages_dir = self.target_dir / ".etg" / "packages"
        self.global_registry_cache = Path.home() / ".etg" / "registry_cache"
        
        # Reserved advisory entries for standard packages. This local mapping is
        # intentionally not represented as a comprehensive vulnerability feed.
        self.vulnerability_db = {
            "ContentPublishing": [],
            "AWS": []
        }
        
        # Heuristics for custom package vulnerabilities (Regex-based fallback)
        self.custom_heuristics = [
            {"id": "SNTNL-CUST-001", "severity": "MEDIUM", "trigger": "password", "description": "Plaintext password attribute detected in Schema"},
            {"id": "SNTNL-CUST-002", "severity": "HIGH", "trigger": "ssn", "description": "Social Security Number attribute without explicit encryption annotation"}
        ]

    def _is_standard_package(self, package_name: str) -> bool:
        """Determines if a package is standard by checking if it belongs to the @entigram namespace or standard list."""
        if package_name.startswith("@entigram/"):
            return True
            
        # Legacy fallback for projects initialized before namespaces
        standard_packages = [
            "Entigram Schemas",
            "AWS", "Azure", "GCP", "Banking", "BusinessStrategy", 
            "ClinicalValidation", "CompetitiveIntelligence", "ContentPublishing", 
            "EHRExtraction", "GoogleWorkspace", "HIPAACompliance", 
            "MarketingWebsite", "PartnerManagement", "PersonalFinance", 
            "Salesforce", "SpringBoot", "StartupFounder", "SupplyChain", 
            "TechnicalDueDiligence", "Terraform", "XWiki"
        ]
        return package_name in standard_packages

    def scan_all(self) -> Dict[str, Any]:
        """Scans all packages active in the current project's workspace."""
        manifest_path = self.target_dir / ".etg" / "entigram.yaml"
        schema_path = self.target_dir / "schema.lds"
        
        active_pkgs = set()

        # 1. Load packages from manifest
        if manifest_path.exists():
            import yaml
            try:
                with open(manifest_path, 'r') as f:
                    manifest = yaml.safe_load(f) or {}
                
                raw_pkgs = manifest.get('packages', {})
                if isinstance(raw_pkgs, list):
                    for p in raw_pkgs: active_pkgs.add(p)
                else:
                    for p in raw_pkgs.keys(): active_pkgs.add(p)
            except Exception as e:
                print(f"Warning: Could not parse manifest for scan: {e}")

        # 2. Dynamically discover dependencies from Schema
        if schema_path.exists():
            try:
                parser = SchemaParser(schema_path.read_text())
                entities, _ = parser.parse()
                for ent in entities.values():
                    if ent.external_ref:
                        # Extract package name: @entigram/Salesforce::Account -> @entigram/Salesforce
                        pkg_name = ent.external_ref.split("::")[0]
                        active_pkgs.add(pkg_name)
            except Exception as e:
                print(f"Warning: Could not parse schema.lds for dependency discovery: {e}")

        if not active_pkgs:
            return {"status": "no_packages_found", "results": {}}

        results = {}
        for pkg in sorted(list(active_pkgs)):
            results[pkg] = self.scan_package(pkg)
            
        return results

    def _resolve_pkg_path(self, package_name: str) -> Optional[Path]:
        # 1. Foundation Package
        if package_name == "Entigram Schemas":
             return self.target_dir
             
        # 2. Standard Global Cache
        if self._is_standard_package(package_name) and self.global_registry_cache.exists():
             for repo in self.global_registry_cache.iterdir():
                 if repo.is_dir():
                     parts = package_name.split("/")
                     if len(parts) == 2:
                         # Handle explicit namespaces (e.g., @entigram/MonteCarlo)
                         potential_path = repo / parts[0] / parts[1]
                         if potential_path.exists(): return potential_path
                         # Legacy check
                         potential_path = repo / package_name
                         if potential_path.exists(): return potential_path
                     else:
                         # Legacy standard package fallback
                         potential_path = repo / "@entigram" / package_name
                         if potential_path.exists(): return potential_path

        # 3. Workspace 'packages/' folder (Legacy/Local/Test)
        root_pkg_path = self.target_dir / "packages" / package_name
        if root_pkg_path.exists():
            return root_pkg_path

        # 4. Local '.etg/packages/' folder (Locked/Custom)
        local_pkg_path = self.local_packages_dir / package_name
        if local_pkg_path.exists():
            return local_pkg_path

        return None

    def scan_package(self, package_name: str) -> Dict[str, Any]:
        """Performs static analysis on a specific package."""
        is_standard = self._is_standard_package(package_name)
        vulnerabilities = []

        # 1. Resolve Package Path
        pkg_path = self._resolve_pkg_path(package_name)
                 
        if not pkg_path:
             return {
                 "package": package_name,
                 "is_standard": is_standard,
                 "vulnerabilities": [{"id": "SNTNL-SYS-001", "severity": "HIGH", "description": f"Could not resolve package path for analysis."}],
                 "bypassed": []
             }

        # 2. Check Standard Vulnerability DB
        clean_name = package_name.split("/")[-1]
        if is_standard and clean_name in self.vulnerability_db:
             vulnerabilities.extend(self.vulnerability_db[clean_name])

        schema_path = pkg_path / "schema.lds"
        if schema_path.exists():
            schema_content = schema_path.read_text()
            
            # 3. AST-Based Static Analysis (Structural Integrity)
            try:
                parser = SchemaParser(schema_content)
                entities, relationships = parser.parse()
                
                for ent_name, entity in entities.items():
                    # Rule AST-001: Missing Primary Key
                    has_pk = any(attr.get('pk', False) for attr in entity.attributes)
                    if not has_pk and not entity.external_ref:
                        vulnerabilities.append({
                            "id": "SNTNL-AST-001",
                            "severity": "HIGH",
                            "description": f"Missing Primary Key: Entity '{ent_name}' lacks a PK attribute. This breaks federated GraphQL-LD routing."
                        })
                    
                    # Rule AST-002: Orphaned Entity (No relationships)
                    if not entity.external_ref:
                        is_related = False
                        for rel in relationships:
                            if rel.entity_a.lower() == ent_name.lower() or rel.entity_b.lower() == ent_name.lower():
                                is_related = True
                                break
                        if not is_related and len(entities) > 1:
                            vulnerabilities.append({
                                "id": "SNTNL-AST-002",
                                "severity": "MEDIUM",
                                "description": f"Orphaned Entity: '{ent_name}' has no relationships to other entities in the model."
                            })

            except Exception as e:
                vulnerabilities.append({
                    "id": "SNTNL-AST-000",
                    "severity": "CRITICAL",
                    "description": f"AST Parse Failure: The schema.lds is malformed and cannot be statically analyzed. Error: {str(e)}"
                })

            # 4. Regex-Based Heuristic Scan (PII / Pollution)
            schema_content_lower = schema_content.lower()
            
            if is_standard:
                pollution_pattern = re.compile(r'\b(acme|demo|my_?custom|test_?company|customer_?[a-z])\b', re.IGNORECASE)
                schema_lines = schema_content.splitlines()
                for match in pollution_pattern.finditer(schema_content):
                     line_number = schema_content.count("\n", 0, match.start()) + 1
                     line_start = schema_content.rfind("\n", 0, match.start()) + 1
                     vulnerabilities.append({
                         "id": "SNTNL-RULE-005",
                         "severity": "CRITICAL",
                         "description": f"Standard Package Pollution: Customer-specific term '{match.group(1)}' found. Specific implementations must be isolated.",
                         "artifact": str(schema_path.relative_to(pkg_path)),
                         "line": line_number,
                         "column": match.start() - line_start + 1,
                         "match": match.group(1),
                         "evidence": schema_lines[line_number - 1].strip(),
                     })

            for heuristic in self.custom_heuristics:
                if heuristic["trigger"] in schema_content_lower:
                    vulnerabilities.append({
                        "id": heuristic["id"],
                        "severity": heuristic["severity"],
                        "description": heuristic["description"]
                    })

        vulnerabilities = [
            self._with_fingerprint(package_name, vulnerability)
            for vulnerability in vulnerabilities
        ]

        # 5. Check for legacy ID-wide bypasses and occurrence-specific suppressions
        bypassed = self._get_bypassed_vulnerabilities(pkg_path)
        suppressions = self._get_suppressions(pkg_path)
        
        active_vulns = []
        suppressed_vulns = []
        for v in vulnerabilities:
             if v["id"] in bypassed:
                  continue

             suppression = self._matching_suppression(v, suppressions)
             if suppression:
                  suppressed_vulns.append({**v, "suppression": suppression})
             else:
                  active_vulns.append(v)

        return {
            "package": package_name,
            "is_standard": is_standard,
            "vulnerabilities": active_vulns,
            "suppressed": suppressed_vulns,
            "bypassed": bypassed,
        }

    @staticmethod
    def _with_fingerprint(package_name: str, vulnerability: Dict[str, Any]) -> Dict[str, Any]:
        """Attach a stable identity to one exact finding occurrence."""
        finding = dict(vulnerability)
        fingerprint_payload = {
            "artifact": finding.get("artifact"),
            "column": finding.get("column"),
            "description": finding.get("description"),
            "evidence": finding.get("evidence"),
            "finding_id": finding.get("id"),
            "line": finding.get("line"),
            "match": finding.get("match"),
            "package": package_name,
        }
        canonical = json.dumps(
            fingerprint_payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
        finding["fingerprint"] = f"sha256:{hashlib.sha256(canonical).hexdigest()}"
        return finding

    def _get_suppression_file_path(self, pkg_path: Path) -> Path:
        return pkg_path / ".sentinel-suppressions.yaml"

    def _get_suppressions(self, pkg_path: Path) -> List[Dict[str, Any]]:
        """Load reviewed, occurrence-specific suppressions; invalid records fail closed."""
        suppression_file = self._get_suppression_file_path(pkg_path)
        if not suppression_file.exists():
            return []

        import yaml

        try:
            payload = yaml.safe_load(suppression_file.read_text()) or {}
        except (OSError, yaml.YAMLError):
            return []

        if not isinstance(payload, dict) or payload.get("version") != 1:
            return []

        records = payload.get("suppressions", [])
        if not isinstance(records, list):
            return []

        required_fields = {
            "finding_id",
            "fingerprint",
            "rationale",
            "authorized_by",
            "authorized_at",
        }
        return [
            record
            for record in records
            if isinstance(record, dict)
            and required_fields.issubset(record)
            and all(str(record[field]).strip() for field in required_fields)
        ]

    @staticmethod
    def _suppression_is_current(suppression: Dict[str, Any]) -> bool:
        expires_at = suppression.get("expires_at")
        if not expires_at:
            return True
        try:
            return date.fromisoformat(str(expires_at)[:10]) >= date.today()
        except ValueError:
            return False

    def _matching_suppression(
        self,
        vulnerability: Dict[str, Any],
        suppressions: List[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        for suppression in suppressions:
            if (
                suppression["finding_id"] == vulnerability["id"]
                and suppression["fingerprint"] == vulnerability["fingerprint"]
                and self._suppression_is_current(suppression)
            ):
                return suppression
        return None

    def _get_bypass_file_path(self, pkg_path: Path) -> Path:
        return pkg_path / ".sentinel-ignore"

    def _get_bypassed_vulnerabilities(self, pkg_path: Path) -> List[str]:
        """Reads the .sentinel-ignore file."""
        bypass_file = self._get_bypass_file_path(pkg_path)
        if bypass_file.exists():
            return [line.strip() for line in bypass_file.read_text().splitlines() if line.strip() and not line.startswith("#")]
        return []

    def authorize_bypass(self, package_name: str, vulnerability_id: str, rationale: str) -> bool:
        """
        Authorizes a bypass for a specific vulnerability.
        """
        if self._is_standard_package(package_name):
             print(f"❌ Sentinel Error: Cannot bypass vulnerabilities in standard package '{package_name}'. Please update the package.")
             return False

        pkg_path = self._resolve_pkg_path(package_name)
        if not pkg_path:
            print(f"❌ Sentinel Error: Package path not found.")
            return False

        bypass_file = self._get_bypass_file_path(pkg_path)
        current_bypasses = self._get_bypassed_vulnerabilities(pkg_path)
        
        if vulnerability_id in current_bypasses:
             print(f"ℹ️ Bypass already exists for {vulnerability_id} in {package_name}.")
             return True

        with open(bypass_file, "a") as f:
             f.write(f"\n# Bypass authorized: {rationale}\n")
             f.write(f"{vulnerability_id}\n")
        
        print(f"✅ Sentinel: Bypass authorized for {vulnerability_id} in custom package '{package_name}'.")
        return True

    def authorize_suppression(
        self,
        package_name: str,
        vulnerability_id: str,
        fingerprint: str,
        rationale: str,
        authorized_by: str,
        expires_at: Optional[str] = None,
    ) -> bool:
        """Record a reviewed suppression for one current finding occurrence."""
        if not rationale.strip() or not authorized_by.strip():
            print("❌ Sentinel Error: Suppressions require a rationale and authorizer.")
            return False

        if expires_at:
            try:
                date.fromisoformat(expires_at)
            except ValueError:
                print("❌ Sentinel Error: --expires must use YYYY-MM-DD format.")
                return False

        pkg_path = self._resolve_pkg_path(package_name)
        if not pkg_path:
            print("❌ Sentinel Error: Package path not found.")
            return False

        scan_result = self.scan_package(package_name)
        current_findings = scan_result.get("vulnerabilities", []) + scan_result.get("suppressed", [])
        matching_finding = next(
            (
                finding
                for finding in current_findings
                if finding["id"] == vulnerability_id
                and finding.get("fingerprint") == fingerprint
            ),
            None,
        )
        if not matching_finding:
            print("❌ Sentinel Error: Fingerprint does not identify a current package finding.")
            return False

        existing = self._get_suppressions(pkg_path)
        if self._matching_suppression(matching_finding, existing):
            print(f"ℹ️ Suppression already exists for {fingerprint} in {package_name}.")
            return True

        suppression = {
            "finding_id": vulnerability_id,
            "fingerprint": fingerprint,
            "artifact": matching_finding.get("artifact"),
            "line": matching_finding.get("line"),
            "rationale": rationale.strip(),
            "authorized_by": authorized_by.strip(),
            "authorized_at": datetime.now(timezone.utc).isoformat(),
        }
        if expires_at:
            suppression["expires_at"] = expires_at

        import yaml

        suppression_file = self._get_suppression_file_path(pkg_path)
        payload = {"version": 1, "suppressions": existing + [suppression]}
        suppression_file.write_text(yaml.safe_dump(payload, sort_keys=False))
        print(
            f"✅ Sentinel: Suppressed {vulnerability_id} occurrence {fingerprint} "
            f"in '{package_name}'."
        )
        return True
