# Dependency Auditor Checklist

## Scope
Files matching: `requirements.txt`, `package-lock.json`, `go.sum`, `Cargo.lock`, `pyproject.toml`, `Pipfile.lock`, `poetry.lock`

## P0 — Critical (Must Fix / Hydration Blocker)
- [ ] Critical security vulnerability (CVSS 9.0+ / Critical CVE) in direct dependency with active exploit
- [ ] Known malicious or hijacked package dependency present in lockfile
- [ ] Corrupted, unparseable, or truncated lockfile preventing workspace build/hydration

## P1 — High (Should Fix)
- [ ] High severity vulnerability (CVSS 7.0–8.9) in direct or transitive dependency
- [ ] Dependency version pinned to an End-Of-Life (EOL) or deprecated runtime library
- [ ] Unlocked or wildcard dependency version specifications allowing non-deterministic builds
- [ ] Missing lockfile when package manifest (`pyproject.toml`, `package.json`, `Cargo.toml`) is present

## P2 — Medium (Fix or Track)
- [ ] Moderate severity vulnerability (CVSS 4.0–6.9) with available upgrade path
- [ ] Mismatch between dependency manifest (`pyproject.toml`) and lockfile (`poetry.lock`)
- [ ] Orphaned or unused dependency declared in lockfile
- [ ] Outdated major version of core framework dependency

## P3 — Low (Optional)
- [ ] Minor or patch version update available for secure dependency
- [ ] Formatting or sorting inconsistencies in requirements/lockfile
- [ ] Missing license declaration in package specification
