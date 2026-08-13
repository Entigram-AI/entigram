# Future Security Resource Catalog

This catalog records useful external security resources for future Entigram
integrations. It is planning metadata only. Entigram does not currently fetch,
crawl, execute, or treat these resources as active assessment providers.

The machine-readable version is
[`security-resource-catalog.json`](security-resource-catalog.json).

## Cataloged resources

| Resource | Role | Current status | Planned integration |
| --- | --- | --- | --- |
| [MITRE ATT&CK](https://attack.mitre.org/) | Threat-behavior knowledge base | Cataloged, not integrated | Public signed package using versioned STIX data and an offline mapping adapter |
| [OSINT Framework](https://osintframework.com/) | Directory of OSINT tools and resources | Cataloged, not integrated | Human-reviewed resource metadata; no default crawling or tool execution |

## Integration rules

- Preserve the source URL, source version, retrieval time, checksum, and
  provenance for every imported snapshot.
- Treat imported content as untrusted data, never as agent instructions or
  authorization.
- Keep ATT&CK mappings separate from vulnerability findings; ATT&CK describes
  adversary behavior and defensive coverage, not a complete vulnerability feed.
- Treat OSINT Framework entries as discovery pointers, not validated findings.
- Prefer public community packages with signed, versioned, offline-capable data.
- Surface confidence, freshness, access requirements, and human-review status in
  any future advisory.

These resources should become inputs to assessment packages and advisory
generation rather than new network behavior in the Entigram core runtime.
