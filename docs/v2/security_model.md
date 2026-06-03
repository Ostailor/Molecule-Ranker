# Security Model

V2.0 security is based on enterprise identity, RBAC, scoped service accounts,
tenant/project isolation, audit logging, secret redaction, secure deployment
defaults, and release-gated validation.

## Identity

- Local auth remains available for development and internal standalone use.
- OIDC is the production identity path when configured.
- OIDC discovery, ID token validation, JWKS rotation, allowed email domains,
  group-to-role mapping, session expiration, and logout/revocation support are
  part of the production hardening surface.
- SAML and SCIM interfaces may exist as placeholders only where full support is
  not implemented.

## Access Control

- Admin actions require admin permission.
- Projects require explicit project permission.
- Service accounts use scoped tokens and should receive least privilege.
- Service tokens are shown once at creation and are never stored or displayed in
  plaintext.
- Every admin action is audited.

## Secret Handling

Do not put tokens, passwords, OIDC client secrets, integration credentials, or
private keys into Codex prompts, support bundles, audit metadata, artifacts, or
deployment examples. Metrics, logs, support bundles, validation packages, and
exports must redact secrets.

## Production Transport

HTTPS is required in production unless explicitly overridden for local
development. Use secret managers or orchestrator secrets for configuration.
Never bake secrets into images.

## Security Process

Run security audit, red-team tests, isolation audit, support bundle redaction
checks, and release gate before V2.0 promotion. Treat findings as release
blockers until triaged and remediated or explicitly accepted.
