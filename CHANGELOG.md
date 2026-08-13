# Changelog

## Unreleased

### Features

* add out-of-the-box expectation guard for pre-handoff agent verification
* add `etg serve` MCP server with schema-gated alignment and conflict tools
* publish agent discoverability through `ai-catalog.json`
* add privacy-preserving `etg usage` estimates for static context and observed CLI/MCP traffic
* add reversible workspace `pause`, `resume`, and archive-first `eject` controls

### Bug Fixes

* harden SQLite ledger concurrency with WAL mode and busy timeouts
* close warning-producing registry, broker, router, and hydration resources

## [1.24.1](https://github.com/Entigram-AI/entigram/compare/v1.24.0...v1.24.1) (2026-08-13)


### Bug Fixes

* name initialized schema for Entigram ([#40](https://github.com/Entigram-AI/entigram/issues/40)) ([4ae679d](https://github.com/Entigram-AI/entigram/commit/4ae679d97c60fcee1f2a432d21f904ff7de1152d))

## [1.24.0](https://github.com/Entigram-AI/entigram/compare/v1.23.0...v1.24.0) (2026-08-13)


### Features

* enforce adapters for workspace agents ([#37](https://github.com/Entigram-AI/entigram/issues/37)) ([9d1302b](https://github.com/Entigram-AI/entigram/commit/9d1302be631ee8f648d774496c76be2831f3daf7))

## [1.23.0](https://github.com/Entigram-AI/entigram/compare/v1.22.0...v1.23.0) (2026-08-13)


### Features

* enforce agent lifecycle check-ins ([#35](https://github.com/Entigram-AI/entigram/issues/35)) ([8320e21](https://github.com/Entigram-AI/entigram/commit/8320e2154dc1c8cf868e4c3182e405e7595332d3))

## [1.22.0](https://github.com/Entigram-AI/entigram/compare/v1.21.1...v1.22.0) (2026-08-03)


### Features

* improve agent discoverability ([#34](https://github.com/Entigram-AI/entigram/issues/34)) ([ce270d6](https://github.com/Entigram-AI/entigram/commit/ce270d6bb2813e5bd59343904f2e069f9e544e2c))


### Documentation

* clarify user and agent workflows ([#32](https://github.com/Entigram-AI/entigram/issues/32)) ([3cdd3ae](https://github.com/Entigram-AI/entigram/commit/3cdd3ae02a2e9d7f7b42b6b0d458b07f6503a9fd))

## [1.21.1](https://github.com/Entigram-AI/entigram/compare/v1.21.0...v1.21.1) (2026-08-01)


### Bug Fixes

* make community package installs self-guiding ([#30](https://github.com/Entigram-AI/entigram/issues/30)) ([058b202](https://github.com/Entigram-AI/entigram/commit/058b20255b2b51e1de11d16c91fdb00cc01194c8))

## [1.21.0](https://github.com/Entigram-AI/entigram/compare/v1.20.0...v1.21.0) (2026-08-01)


### Features

* publish community packages through standard registry ([#27](https://github.com/Entigram-AI/entigram/issues/27)) ([ac1a6d2](https://github.com/Entigram-AI/entigram/commit/ac1a6d26d0a68fdc97b6da3c9a41a8f41e074833))

## [1.20.0](https://github.com/Entigram-AI/entigram/compare/v1.19.0...v1.20.0) (2026-08-01)


### Features

* GitHub issue links for package requests + demand tracking ([95b4605](https://github.com/Entigram-AI/entigram/commit/95b4605ad2834d0c866581b5da25c1f1279d9f42))
* security assessments, multi-agent skills, and package request flow ([caf389c](https://github.com/Entigram-AI/entigram/commit/caf389c3e6c8573aae693c5f25c8a98b55e9eddb))


### Bug Fixes

* address P1/P2 findings from multi-agent review ([b4b124d](https://github.com/Entigram-AI/entigram/commit/b4b124d23eb7f6b36d324de014ababae2bd470e5))

## [1.19.0](https://github.com/Entigram-AI/entigram/compare/v1.18.0...v1.19.0) (2026-08-01)


### Features

* add assessment package catalog and request-access monetization hook ([243fcc5](https://github.com/Entigram-AI/entigram/commit/243fcc570486771c2221bf1e16fe3f7d304dff27))
* governed security assessments, tech-aware advisories, and UI removal ([79a161b](https://github.com/Entigram-AI/entigram/commit/79a161b6334595b86ae6949eb19d00c39cf09de7))
* install multi-agent PR review skill (moltenbits/claude-review pattern) ([b1eb279](https://github.com/Entigram-AI/entigram/commit/b1eb279d2cc6dddd60f5600aedf626c0f90d8e81))
* package 6 multi-agent skills with entigram ([a53739b](https://github.com/Entigram-AI/entigram/commit/a53739b1be833b69f14aff3c1455cd414b74d141))


### Bug Fixes

* add debug logging to suppression loader instead of silent swallow ([abe4742](https://github.com/Entigram-AI/entigram/commit/abe4742a3f5b2ed1144461dd50b1f4f3bd25f382))
* address P0-P2 findings from multi-agent review + rewrite skill ([c205276](https://github.com/Entigram-AI/entigram/commit/c2052762cacb818d6b9ccc0592dd9d2283b7301a))
* always raise flags — elevate advisories to warning, add suppression with rationale ([c5b9b81](https://github.com/Entigram-AI/entigram/commit/c5b9b8102d5d09e4d2aac67bc238a84d5f436a7f))
* direct access requests to developer@entigram.com ([094f1f8](https://github.com/Entigram-AI/entigram/commit/094f1f86459b3f4ddbfed09088871fd09cd6cb98))
* emit proactive security advisories for detected workspace technologies ([2c80f5a](https://github.com/Entigram-AI/entigram/commit/2c80f5a2b903b4fc0851a893b8ad055006e4b50e))
* integrate assess into hydration, expand frameworks beyond OWASP, add package recs ([ee92c7e](https://github.com/Entigram-AI/entigram/commit/ee92c7ea0a13783776fedb3a955d3615cedca80b))
* remove stale entigram.ui package refs breaking CI build ([27a77ac](https://github.com/Entigram-AI/entigram/commit/27a77acaed9f25dcce97d790ff8b156658ffbab6))

## [1.18.0](https://github.com/Entigram-AI/entigram/compare/v1.17.1...v1.18.0) (2026-07-31)


### Features

* harden governed security assessment boundaries ([6d1fdc8](https://github.com/Entigram-AI/entigram/commit/6d1fdc8e79146ef4e16c9b4fb24e8496b729d926))

## [1.17.1](https://github.com/Entigram-AI/entigram/compare/v1.17.0...v1.17.1) (2026-07-31)


### Bug Fixes

* generate brew-style-compliant formulae ([393a54f](https://github.com/Entigram-AI/entigram/commit/393a54f4d31e8ad6424c3579df19d340ee4c911d))
* generate brew-style-compliant formulae ([38790b6](https://github.com/Entigram-AI/entigram/commit/38790b6b669bc8e7cd409a76208cbb5b1684bfdc))

## [1.17.0](https://github.com/Entigram-AI/entigram/compare/v1.16.2...v1.17.0) (2026-07-30)


### Features

* add workspace usage and lifecycle controls ([2a5ece4](https://github.com/Entigram-AI/entigram/commit/2a5ece44dd0d39534183413b6bd7a79315c9cee2))
* add workspace usage and lifecycle controls ([7619ee9](https://github.com/Entigram-AI/entigram/commit/7619ee9315c77fbcf0b26bf194c1dad801832e32))

## [1.16.2](https://github.com/nyabutid/entigram/compare/v1.16.1...v1.16.2) (2026-07-28)


### Bug Fixes

* close governed delivery review gaps ([23bc947](https://github.com/nyabutid/entigram/commit/23bc9476672a98c6c476cebb43feee8196483561))
* harden governed delivery paths ([8924bab](https://github.com/nyabutid/entigram/commit/8924bab73868ead0292349617fe110972a1708d5))
* harden governed delivery paths ([5eeea8f](https://github.com/nyabutid/entigram/commit/5eeea8f1b8bd780ec1ac883c8edf8dae4811d46b))


### Documentation

* add opencode integration guidance ([2ccedcd](https://github.com/nyabutid/entigram/commit/2ccedcdabae57fbe115671363aab3fafc8af1789))
* add OpenCode integration guidance ([b09706c](https://github.com/nyabutid/entigram/commit/b09706c273aa569c13d5ea6ac6be17b2f906907b))

## [1.16.1](https://github.com/nyabutid/entigram/compare/v1.16.0...v1.16.1) (2026-07-27)


### Documentation

* publish Entigram workspace standard ([428c55b](https://github.com/nyabutid/entigram/commit/428c55bfe432a9ff55a6223811f942b0700b24b0))
* publish workspace standard ([6ed435d](https://github.com/nyabutid/entigram/commit/6ed435d4645bf7527ed9889d7c2180c928cbb138))

## [1.16.0](https://github.com/nyabutid/entigram/compare/v1.15.0...v1.16.0) (2026-07-18)


### Features

* make hydrate concise by default ([2b6fe0c](https://github.com/nyabutid/entigram/commit/2b6fe0cfda454a12b5c0100a70bf0f1d578e1a88))
* make hydrate concise by default ([29c0ea2](https://github.com/nyabutid/entigram/commit/29c0ea2c0157cdc4353015138cec40f7b1b80578))

## [1.15.0](https://github.com/nyabutid/entigram/compare/v1.14.0...v1.15.0) (2026-07-18)


### Features

* auto-select Cloudflare coding models ([5ff4552](https://github.com/nyabutid/entigram/commit/5ff4552dcf0ada1906e1f2d443c44dd08d01a936))
* harden Cloudflare backend and agent governance ([9a68c83](https://github.com/nyabutid/entigram/commit/9a68c8320c925b8b9159f2a3a477e41950bed563))
* make agent governance portable ([b152b20](https://github.com/nyabutid/entigram/commit/b152b209b089b95e347b7bc75d25e1966db93a2a))


### Bug Fixes

* avoid Claude slash model interception ([1885cff](https://github.com/nyabutid/entigram/commit/1885cffb5754bdb1afd0093e3e2c0398acc3631e))
* normalize Cloudflare proxy message content ([d1914b0](https://github.com/nyabutid/entigram/commit/d1914b08f823e8c222c96d4eb4fc9686a8e3e37e))

## [1.14.0](https://github.com/nyabutid/entigram/compare/v1.13.0...v1.14.0) (2026-07-09)


### Features

* add WebSocket panel bridge for Agent-Hosted Panels ([07b85b3](https://github.com/nyabutid/entigram/commit/07b85b341bd3e5a8210e63321bb42294f8682306))
* add WebSocket panel bridge for Agent-Hosted Panels ([8527ee7](https://github.com/nyabutid/entigram/commit/8527ee73a48f6736ea0bad1344e91f7d3a4e7c65))

## [1.13.0](https://github.com/nyabutid/entigram/compare/v1.12.0...v1.13.0) (2026-07-05)


### Features

* add agent orchestration hibernation ([cc58dc4](https://github.com/nyabutid/entigram/commit/cc58dc44694bf7c872a557fe43f28cd2743b776a))
* add agent orchestration hibernation ([5e5b353](https://github.com/nyabutid/entigram/commit/5e5b353b3dead4ae622949493a9e70df822e92e5))

## [1.12.0](https://github.com/nyabutid/entigram/compare/v1.11.0...v1.12.0) (2026-07-05)


### Features

* add model gate feedback retries ([c56d659](https://github.com/nyabutid/entigram/commit/c56d6595abb2bccbc25bc489661072ff8d6ebfad))
* add model gate feedback retries ([8c9a3e4](https://github.com/nyabutid/entigram/commit/8c9a3e460f45b697a815507a1a46339390bde99e))
* add structured halt event json ([bbf8bef](https://github.com/nyabutid/entigram/commit/bbf8bef47bb104d3ba8bd6c1d970d59af6385b4f))
* add structured halt event json ([5ffcc17](https://github.com/nyabutid/entigram/commit/5ffcc173e6e582b50029a25958ae1390f29bf6bc))

## [1.11.0](https://github.com/nyabutid/entigram/compare/v1.10.1...v1.11.0) (2026-07-05)


### Features

* add etg merge ([30a31dd](https://github.com/nyabutid/entigram/commit/30a31dd14e88076bf3017a052518ccf26215357a))

## [1.10.1](https://github.com/nyabutid/entigram/compare/v1.10.0...v1.10.1) (2026-07-03)


### Bug Fixes

* make adapter fields optional in catalog audit for domain-only packages ([7b31865](https://github.com/nyabutid/entigram/commit/7b318657f434fa847c2220c64489af1afb9e35d2))
* make adapter_module, source_kinds, adapters optional in catalog audit ([f011e8f](https://github.com/nyabutid/entigram/commit/f011e8fdb909a79f164b49f287b71d0763b75385))

## [1.10.0](https://github.com/nyabutid/entigram/compare/v1.9.0...v1.10.0) (2026-07-03)


### Features

* add discovery review and signed package catalog ([6abed8f](https://github.com/nyabutid/entigram/commit/6abed8f237029a4d7558022f18e3f89cab51ff8d))
* add discovery review and signed package catalog ([a55c781](https://github.com/nyabutid/entigram/commit/a55c78172b3c5a55577d429916b08afcd27b4990))

## [1.9.0](https://github.com/nyabutid/entigram/compare/v1.8.0...v1.9.0) (2026-06-30)


### Features

* add Cloudflare Claude proxy launcher ([8076678](https://github.com/nyabutid/entigram/commit/8076678dae3a1732584ae23b8e394733658562b8))
* add Cloudflare Claude proxy launcher ([6d2aedb](https://github.com/nyabutid/entigram/commit/6d2aedb6e6e50a3d30e1596d943c7ae411fe9e4d))

## [1.8.0](https://github.com/nyabutid/entigram/compare/v1.7.8...v1.8.0) (2026-06-21)


### Features

* implement Cloud API package fetching using ENTIGRAM_TOKEN ([17f7ecf](https://github.com/nyabutid/entigram/commit/17f7ecf0fbb30af484e21a57746e24d8c9e679a7))
* implement Cloud API package fetching using ENTIGRAM_TOKEN ([83e6f84](https://github.com/nyabutid/entigram/commit/83e6f840b26629190856c16e415e2db7f3226fe1))

## [1.7.8](https://github.com/nyabutid/entigram/compare/v1.7.7...v1.7.8) (2026-06-21)


### Bug Fixes

* align Homebrew formula Python runtime ([fd10640](https://github.com/nyabutid/entigram/commit/fd1064089fb4d6deed718eaad47e767710365689))
* align Homebrew formula Python runtime ([7da8056](https://github.com/nyabutid/entigram/commit/7da80568c31f52d66853199e1b845b4d027a762a))

## [1.7.7](https://github.com/nyabutid/entigram/compare/v1.7.6...v1.7.7) (2026-06-21)


### Bug Fixes

* include setuptools in Homebrew resources ([2e364e4](https://github.com/nyabutid/entigram/commit/2e364e463e8d9e07b86f1a75f2b20f38a22aff3c))
* remove vulnerable ecdsa dependency ([11e5fa7](https://github.com/nyabutid/entigram/commit/11e5fa7cf002c7767e902d19806429623ed376cc))
* remove vulnerable ecdsa dependency ([8e5df53](https://github.com/nyabutid/entigram/commit/8e5df53b5e6621be5bc15f97dc466d034a88a14f))

## [1.7.6](https://github.com/nyabutid/entigram/compare/v1.7.5...v1.7.6) (2026-06-21)


### Bug Fixes

* wait for PyPI sdist metadata ([cead15e](https://github.com/nyabutid/entigram/commit/cead15e25ffb4ef12973175c63b391081ffd0176))
* wait for PyPI sdist metadata ([c835b42](https://github.com/nyabutid/entigram/commit/c835b42df0083316970b8011dc1a61314b526e85))

## [1.7.5](https://github.com/nyabutid/entigram/compare/v1.7.4...v1.7.5) (2026-06-21)


### Bug Fixes

* filter pydantic and rpds-py rust dependencies ([a608216](https://github.com/nyabutid/entigram/commit/a6082166576994fa1efbe80750c9cc4a7d429678))
* filter pydantic and rpds-py rust dependencies ([68478a7](https://github.com/nyabutid/entigram/commit/68478a7c3d79f9c8c118e97222613d6e96e76238))
* harden Homebrew resource filtering ([4c20ebd](https://github.com/nyabutid/entigram/commit/4c20ebdefd496ca8849930a4c9896296ba5759d0))

## [1.7.4](https://github.com/nyabutid/entigram/compare/v1.7.3...v1.7.4) (2026-06-21)


### Bug Fixes

* filter cryptography from poet resources and inject Homebrew dependency ([24b3d65](https://github.com/nyabutid/entigram/commit/24b3d65ddbffa076af7fda741f888b83553d62a1))
* filter cryptography from poet resources and inject Homebrew dependency ([6c5a8df](https://github.com/nyabutid/entigram/commit/6c5a8df2669364d4608fa4b2ccf451fef6d04749))

## [1.7.3](https://github.com/nyabutid/entigram/compare/v1.7.2...v1.7.3) (2026-06-21)


### Bug Fixes

* eliminate cryptography dependency for Homebrew builds ([572e8ad](https://github.com/nyabutid/entigram/commit/572e8ad3571384b4ae8723e5a16ac42df9a5b693))
* replace cryptography with pure python ecdsa to resolve Homebrew sandbox build failures ([2dee694](https://github.com/nyabutid/entigram/commit/2dee69408d490f9050ea1a359287048c5ca26ad5))

## [1.7.2](https://github.com/nyabutid/entigram/compare/v1.7.1...v1.7.2) (2026-06-21)


### Bug Fixes

* eliminate PyPI index race condition in homebrew release script ([a211b37](https://github.com/nyabutid/entigram/commit/a211b379683b6adf852100d1d4435a5f80394ca3))
* use local repo root to avoid PyPI index race condition ([0e95d74](https://github.com/nyabutid/entigram/commit/0e95d7420f6684b5afc4ed5c3ceb02d4cdbf44b4))

## [1.7.1](https://github.com/nyabutid/entigram/compare/v1.7.0...v1.7.1) (2026-06-21)


### Bug Fixes

* generate homebrew formula resources using poet ([01ecd5e](https://github.com/nyabutid/entigram/commit/01ecd5e1cb54547fc8ea75643c20595f0cf99819))
* generate homebrew formula resources using poet ([1173fa7](https://github.com/nyabutid/entigram/commit/1173fa77e0e3b7fe926e4a1273aeebcb9981ebad))

## [1.7.0](https://github.com/nyabutid/entigram/compare/v1.6.0...v1.7.0) (2026-06-21)


### Features

* sign audit bundles and clarify versions ([be12d03](https://github.com/nyabutid/entigram/commit/be12d0357b1098abe7426b534bbc10a9c3c208e3))


### Bug Fixes

* dynamically read version from pyproject.toml in tests ([309c43d](https://github.com/nyabutid/entigram/commit/309c43d89e0314e6a08d1eb519678ece52bf681d))
* sync requirements.txt for CI build ([cf01b76](https://github.com/nyabutid/entigram/commit/cf01b760ff4e1a0a2e60f06d49ced56e97e2906d))
* update hardcoded version 1.6.0 to 1.7.0 in tests ([4c5c7d9](https://github.com/nyabutid/entigram/commit/4c5c7d9be24a5c05e72844c43b68ca0b5c6b8c0b))

## [1.6.0](https://github.com/nyabutid/entigram/compare/v1.5.0...v1.6.0) (2026-06-21)

### Features

* add audit bundle and maintainer docs ([9c4840e](https://github.com/nyabutid/entigram/commit/9c4840ef4d037c6fcfead86101505283c0f88842))
* Entigram 1.6 introduces signed audit bundles for portable governance evidence.
* Document the MCP gate contract for `etg_get_schemas`, `etg_propose_alignment`, and `etg_log_conflict`.
* Add a deterministic Immutable Gate smoke test for schema discovery, hallucination rejection, ledger writes, delivery anchoring, and audit export.
* Keep the CLI/MCP runtime headless by default while publishing an optional Streamlit UI extra.

## [1.5.0](https://github.com/nyabutid/entigram/compare/v1.4.1...v1.5.0) (2026-06-20)


### Features

* deterministic ontology generation and change impact analysis ([dad7c8d](https://github.com/nyabutid/entigram/commit/dad7c8d9bf0f55813f7f58488361ee5580cfe85a))
* deterministic ontology generation and change impact analysis ([38ae72f](https://github.com/nyabutid/entigram/commit/38ae72f1936a2bc586df37907d311f844e465a6a))
* harden MCP gate contract ([4c52320](https://github.com/nyabutid/entigram/commit/4c5232011b9c0735310a544c67a2495d36476690))
* inject RelationalAlgebraGuard into etg_propose_alignment MCP handler ([af00ada](https://github.com/nyabutid/entigram/commit/af00ada789e1e3eaa9adbbb65228959dd11df0fb))
* model canonical cross-agent governance policy ([b4b480e](https://github.com/nyabutid/entigram/commit/b4b480ed85e6d8c95d0f9c90f3fc8ad343d75ab3))


### Bug Fixes

* correct pre-handoff governance order ([cb0aa04](https://github.com/nyabutid/entigram/commit/cb0aa04764294b33526e862ea8c269163c03a596))
* harden impact analysis and alignment precedence ([98880ca](https://github.com/nyabutid/entigram/commit/98880ca7d62d98425380bd65cb90d8c1dc8387b7))

## [1.4.1](https://github.com/nyabutid/entigram/compare/v1.4.0...v1.4.1) (2026-06-20)


### Bug Fixes

* support pyproject-only release versioning ([2ecc1cf](https://github.com/nyabutid/entigram/commit/2ecc1cfd7f304a3d8aeed8021e4321acaf4c3195))
* support pyproject-only release versioning ([d667780](https://github.com/nyabutid/entigram/commit/d667780ed2d94c1e051acf1693a4dd0e0a1e5432))

## [1.4.0](https://github.com/nyabutid/entigram/compare/v1.3.3...v1.4.0) (2026-06-20)


### Features

* add zero-trust MCP server ([0c5e3a3](https://github.com/nyabutid/entigram/commit/0c5e3a358757239dacea081936a2abae62b1379f))
* add zero-trust MCP server ([ee6f09c](https://github.com/nyabutid/entigram/commit/ee6f09c3fb4c89da41e3eecc8ca7cf952c31f98d))

## [1.3.3](https://github.com/nyabutid/entigram/compare/v1.3.2...v1.3.3) (2026-06-18)


### Bug Fixes

* update homebrew tap from pypi metadata ([2851af3](https://github.com/nyabutid/entigram/commit/2851af39c860a83748b0c96f173297f32dc938a1))
* update homebrew tap from pypi metadata ([adc332f](https://github.com/nyabutid/entigram/commit/adc332f832879ce5aa0e9d8727ecfd342f4790f3))

## [1.3.2](https://github.com/nyabutid/entigram/compare/v1.3.1...v1.3.2) (2026-06-18)


### Bug Fixes

* model release PR governance rules ([4360c02](https://github.com/nyabutid/entigram/commit/4360c02ef375c9b49186bbd9aa574ce4f47626e7))
* model release PR governance rules ([98bbbdd](https://github.com/nyabutid/entigram/commit/98bbbdd73a99ebb625592ebe64fd3ee4539dc1cb))

## [0.3.0](https://github.com/nyabutid/entigram/compare/v0.2.2...v0.3.0) (2026-06-04)


### Features

* add expectation guard for pre-handoff agent verification ([562dd72](https://github.com/nyabutid/entigram/commit/562dd72107d49b13f51d951cbaa75615222e2ded))
* out-of-the-box expectation guard for pre-handoff agent verification ([10d2471](https://github.com/nyabutid/entigram/commit/10d24712d5b79734a0f9205ea8fd6d9b30bde108))

## [0.2.2](https://github.com/nyabutid/entigram/compare/v0.2.1...v0.2.2) (2026-06-04)


### Bug Fixes

* broaden homebrew formula detection ([171671e](https://github.com/nyabutid/entigram/commit/171671e5e031d419b7c393209092edf4d8f59761))
* broaden homebrew formula detection ([f4fede4](https://github.com/nyabutid/entigram/commit/f4fede43d4a8ca9de94ca659e67fff20cd399b41))

## [0.2.1](https://github.com/nyabutid/entigram/compare/v0.2.0...v0.2.1) (2026-06-04)


### Bug Fixes

* resolve homebrew formula path dynamically ([c88adbd](https://github.com/nyabutid/entigram/commit/c88adbd3eaaf8423f28d4e63bc5fc489dd0abb87))
* resolve homebrew formula path dynamically ([5e3709c](https://github.com/nyabutid/entigram/commit/5e3709c9b6b35443f63c47fc00a055419fd41e21))

## [0.2.0](https://github.com/nyabutid/entigram/compare/v0.1.0...v0.2.0) (2026-06-04)


### Features

* add modern Entigram logo assets ([dab8f99](https://github.com/nyabutid/entigram/commit/dab8f9925a69418c849d7235aaf249e301cc6897))
* add modern Entigram logo assets ([43daa34](https://github.com/nyabutid/entigram/commit/43daa349b56fc7452aebe8311643ce987280dd13))
* add Ollama launch option selection ([3853b95](https://github.com/nyabutid/entigram/commit/3853b95cef8ecf0d2c1a0b23d21318b9eb7c7c83))
* close indispensability gap — learnings, trust score, E2T mapping, session proposals ([3203725](https://github.com/nyabutid/entigram/commit/3203725d6f4d797fa490122c0c945e74c7c04df3))
* commissioner companion features — evidence ledger, delivery snapshots, improvement proposals, Blocked state, CLI deliver/resolve/improve ([5eb7447](https://github.com/nyabutid/entigram/commit/5eb7447800e52ad944608c96b9db5cb777cb4a6a))


### Documentation

* add workspace alignment check to agent initialization step ([3e6204f](https://github.com/nyabutid/entigram/commit/3e6204f6f0783cf145482dad107b10edfe4a627d))

## 0.1.0 (2026-06-03)


### Bug Fixes

* align tests with federated routing guards ([2c8ed5e](https://github.com/nyabutid/entigram/commit/2c8ed5e6dd4ad246253535378cb8312256aefc6b))
* align tests with federated routing guards ([d48c969](https://github.com/nyabutid/entigram/commit/d48c969b887fcd55df9a76aa43a1bcaf69cfa393))

## [0.0.1](https://github.com/nyabutid/entigram/releases/tag/v0.0.1) (2026-06-02)

### Chore
* Initialize Entigram public baseline.
