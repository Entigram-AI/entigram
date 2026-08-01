# SBOM Builder Reference Checklist

## Scope
Inspects all software components, package manifests, lockfiles, system bindings, and library dependencies:
`pyproject.toml`, `requirements.txt`, `package.json`, `package-lock.json`, `go.mod`, `go.sum`, `Cargo.toml`, `Cargo.lock`, `Pipfile.lock`, `poetry.lock`, system library imports.

## Standard SBOM Fields (CycloneDX / SPDX Standard)
- **Component Name:** Package / Module / Library identifier
- **Version:** Exact declared version or resolved lockfile version
- **Package URL (purl):** Standard package locator (e.g. `pkg:pypi/requests@2.31.0`, `pkg:npm/express@4.18.2`)
- **License:** SPDX license expression (e.g. `MIT`, `Apache-2.0`)
- **Supplier / Origin:** Package index or repository origin (PyPI, npm registry, Crates.io, GitHub)
- **Dependency Depth:** Direct vs Transitive level
- **Integrity Hash:** Cryptographic hash / SHA-256 (where available in lockfiles)

---

## Severity Scale

### 🔴 P0 — Critical (SBOM Inventory Blocker)
- [ ] **Unresolved Package Identity:** Dependency declared in build manifest with untraceable source, unknown supplier, and missing version.
- [ ] **Corrupted Manifest preventing Inventory Build:** Dependency lockfile unparseable or corrupted, preventing full component graph enumeration.
- [ ] **Untracked Binary Executable / Blob:** Pre-compiled binary executable included in workspace without source package metadata or provenance.

### 🟠 P1 — High (Action Required)
- [ ] **Missing Version Pinning in Manifest:** Package manifest includes wildcard or open version specification (`>= 1.0.0`) without lockfile pinning.
- [ ] **Unregistered Private Dependency Supplier:** Dependency originating from non-standard or unauthenticated internal package index.
- [ ] **Missing Transitive Graph Resolution:** Transitive dependency tree depth truncated due to missing lockfile depth resolution.

### 🟡 P2 — Medium (Warning / Track)
- [ ] **Missing Integrity Checksum:** Dependency lockfile entry missing SHA-256 / integrity signature.
- [ ] **Multi-Source Component Discrepancy:** Component resolved from multiple conflicting package registries.
- [ ] **Incomplete Component Supplier Metadata:** Package missing author / supplier URI attribute in manifest.

### 🟢 P3 — Low (Advisory)
- [ ] **Missing Optional Description Field:** Component entry missing summary description in SBOM inventory.
- [ ] **Minor Package URL Format Advisory:** Purl expression formatting advisory (e.g. casing standardization).
