# PII Detector Reference Checklist

## Scope
Scans all data models, schema definitions, and test fixtures:
- **Logical Schemas:** `*.lds`
- **Ontologies:** `*.ttl`
- **Data Models:** `*.py`, `*.ts`, `*.go`, `*.sql`, `*.proto`
- **Test Fixtures & Seed Data:** `tests/fixtures/*`, `testdata/*`, `*.json`, `*.csv`

## High-Risk PII Categories
- **Direct Identifiers:** Full Name, Social Security Number (SSN), Tax ID, National Identity Number, Passport Number, Driver's License Number, Credit/Debit Card Number, Financial Account Number, Passwords, Biometric Data.
- **Indirect / Sensitive PII:** Email Address, Phone Number, Physical Address, Date of Birth (DOB), IP Address, MAC Address, Geographic Location, Health Data, Gender, Race/Ethnicity.

---

## Severity Scale

### 🔴 P0 — Critical (Data Protection / Compliance Blocker)
- [ ] **Plaintext Unencrypted High-Risk PII in Schema/Database Model:** High-risk PII field (SSN, credit card, unhashed password, national ID) defined without encryption-at-rest metadata or vault mapping.
- [ ] **Real Live PII in Committed Test Fixtures:** Real human personal data (real names, real personal emails, real credit cards) committed in test fixtures or seed datasets.
- [ ] **Unprotected Biometric or Financial Identifiers:** Direct financial or biometric fields stored without tokenization or strict access controls.

### 🟠 P1 — High (Action Required)
- [ ] **Unclassified Sensitive PII:** Schema attribute representing sensitive PII (email, phone, home address, IP address) missing `@pii` or privacy classification tags.
- [ ] **Plaintext PII in Application Logs or Exception Handlers:** Code paths serializing full user profiles or PII objects into diagnostic logs.
- [ ] **Missing Field-Level Masking / Anonymization Strategy:** API response model returning unmasked PII (e.g. full email or full phone number) to unauthenticated or general client scope.

### 🟡 P2 — Medium (Warning / Track)
- [ ] **Quasi-Identifiers Without Anonymization:** Fields such as Zip Code + DOB + Gender combined in a single model without k-anonymity safeguards.
- [ ] **Hardcoded Test PII Lacking Mock Prefix:** Realistic test dataset containing sample names/emails without clear `mock_` or `example.com` domain indicators.
- [ ] **Undocumented PII Field Retention:** PII field stored without retention policy or TTL metadata tag in LDS/TTL schema.

### 🟢 P3 — Low (Advisory)
- [ ] **Advisory PII Naming Style:** PII attribute naming ambiguous (e.g. `user_info` vs explicit `email_address`).
- [ ] **Minor Test Data Hygiene:** Sample data using outdated test email formats (`user@test` instead of `user@example.org`).
