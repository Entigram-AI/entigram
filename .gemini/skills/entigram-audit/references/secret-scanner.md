# Secret Scanner Reference Checklist

## Scope
Scans all workspace files across source code, configurations, test suites, and environment manifests:
`*.py`, `*.ts`, `*.js`, `*.go`, `*.json`, `*.yaml`, `*.yml`, `*.toml`, `.env*`, `Dockerfile`, `Makefile`, CI/CD pipelines, documentation.

## Secret Patterns & Signatures
- **Cloud Provider Keys:** AWS Access Key ID (`AKIA...`), AWS Secret Access Key, GCP Service Account Private Key, Azure Client Secret.
- **AI / LLM API Keys:** OpenAI (`sk-...`, `sk-proj-...`), Anthropic (`sk-ant-...`), Cohere, HuggingFace tokens.
- **Payment & Platform Keys:** Stripe Secret Key (`sk_live_...`), PayPal credentials, Twilio tokens, SendGrid keys.
- **Git & CI Tokens:** GitHub Personal Access Token (`ghp_...`), GitLab Private Token, npm tokens.
- **Cryptographic Keys:** RSA / PEM / EC Private Keys (`-----BEGIN PRIVATE KEY-----`), SSH Private Keys, PGP Keys.
- **Database & Auth:** Hardcoded DB Connection Strings (`postgres://user:password@host`), OAuth Secrets, JWT Signing Secrets.

---

## Severity Scale

### 🔴 P0 — Critical (Security Blocker)
- [ ] **Active Production Credentials Hardcoded:** Valid Cloud, Database, AI Provider, Payment, or VCS API keys/tokens hardcoded in tracked workspace files.
- [ ] **Unencrypted Private Keys Committed:** RSA, SSH, EC, or PGP private keys stored in non-ignored workspace paths.
- [ ] **Hardcoded JWT / Encryption Master Keys:** Application master encryption key or JWT signing secret hardcoded in source code or defaults.

### 🟠 P1 — High (Action Required)
- [ ] **High-Entropy String Matching Credential Pattern:** Strings matching secret format (e.g. 32+ char high-entropy base64/hex tokens) assigned to sensitive variable names (`API_SECRET`, `AUTH_TOKEN`).
- [ ] **Unprotected `.env` File Tracked:** Real `.env` file containing sensitive environment variables committed to workspace tracking.
- [ ] **Hardcoded Password in Service Connection String:** Database or Redis connection string with plaintext password embedded in code or configuration default.

### 🟡 P2 — Medium (Warning / Track)
- [ ] **Hardcoded Development Credential in Test Code:** Test fixture containing hardcoded test passwords or mock secret strings without env var override capability.
- [ ] **Exposed Internal Service Secret:** Service-to-service internal authorization token hardcoded in dev configuration file.
- [ ] **Secret Pattern in Commented Code:** Legacy secret or token left in commented-out code blocks.

### 🟢 P3 — Low (Advisory)
- [ ] **Placeholder Credential String:** Clear mock secret string (e.g. `sk-live-0000000000000000`) that triggers high-entropy heuristics.
- [ ] **Insecure Default Config Example:** `.env.example` file containing realistic-looking password instead of `<YOUR_PASSWORD_HERE>`.
