# Security Reviewer Checklist

## Scope
All changed files, with extra scrutiny on auth, API, and data-handling code.

## P0 — Critical
- [ ] Hardcoded secrets, API keys, tokens, or credentials (CWE-798)
- [ ] SQL injection (CWE-89) or NoSQL injection
- [ ] OS command injection (CWE-78)
- [ ] Path traversal allowing reads/writes outside intended directories (CWE-22)
- [ ] Authentication bypass or broken access control (CWE-287)
- [ ] Deserialization of untrusted data (CWE-502)

## P1 — High
- [ ] Missing input validation on external inputs (CWE-20)
- [ ] SSRF — server-side request forgery (CWE-918)
- [ ] Sensitive data in logs or error messages (CWE-532)
- [ ] Missing authorization checks on privileged operations
- [ ] Insecure cryptographic defaults (weak hash, no salt)

## P2 — Medium
- [ ] Missing CSRF protection on state-changing endpoints
- [ ] Overly permissive CORS configuration
- [ ] Error messages leaking internal details (stack traces, paths)
- [ ] Missing rate limiting on authentication endpoints
- [ ] Deprecated or insecure dependency versions

## P3 — Low
- [ ] Missing Content-Security-Policy headers
- [ ] Cookie without Secure/HttpOnly/SameSite flags
- [ ] Verbose debug output enabled in non-dev configs
