# License Scanner Reference Checklist

## Scope
Scans all workspace dependency manifests and lockfiles:
`pyproject.toml`, `requirements.txt`, `package.json`, `package-lock.json`, `go.mod`, `go.sum`, `Cargo.toml`, `Cargo.lock`, `Pipfile.lock`, `poetry.lock`, `LICENSE`, `THIRD_PARTY_LICENSES`.

## License Classifications & Policy
- **Permissive:** MIT, Apache-2.0, BSD-2-Clause, BSD-3-Clause, ISC, Unlicense, CC0-1.0
- **Weak Copyleft:** LGPL-2.1, LGPL-3.0, MPL-2.0, EPL-2.0, CDDL-1.0
- **Strong Copyleft / Restricted:** GPL-2.0, GPL-3.0, AGPL-3.0, SSPL-1.0, EUPL-1.2, CC-BY-NC-4.0, Reciprocal Public License

---

## Severity Scale

### 🔴 P0 — Critical (Governance / Compliance Blocker)
- [ ] **Strong Copyleft / Restricted License in Commercial Boundary:** Direct or transitive dependency under AGPL-3.0, GPL-2.0/3.0, SSPL-1.0, or CC-BY-NC without explicit open-source isolation strategy.
- [ ] **Missing License File in Distributed Package:** Binary/library distribution artifact missing mandatory license text required by included dependencies.
- [ ] **Unlicensed / Restrictive Proprietary Dependency:** Third-party dependency explicitly stating "All Rights Reserved" or forbidding commercial use.

### 🟠 P1 — High (Action Required)
- [ ] **Weak Copyleft Statically Linked:** Dependency under LGPL or MPL statically linked into core proprietary runtime without dynamic linking boundary.
- [ ] **Dual-License Ambiguity:** Dependency with ambiguous "GPL or commercial" license declaration where license selection is unverified.
- [ ] **License Version Mismatch:** Included license text conflicts with package registry metadata (e.g. package manifest claims MIT, but source headers state GPL).

### 🟡 P2 — Medium (Warning / Track)
- [ ] **Unknown / Unclassified License:** Dependency license expression cannot be mapped to standard SPDX identifiers.
- [ ] **Custom Non-Standard License:** Package uses custom license terms requiring legal team review.
- [ ] **Deprecated License Identifier:** Lockfile references deprecated SPDX license expression (e.g., `GPL-2.0` vs `GPL-2.0-only`).

### 🟢 P3 — Low (Advisory)
- [ ] **Missing Copyright Notice:** Permissive license dependency (MIT/BSD) missing explicit copyright attribution in compliance inventory.
- [ ] **Inconsistent License Formatting:** License header formatting inconsistent across local package manifests.
