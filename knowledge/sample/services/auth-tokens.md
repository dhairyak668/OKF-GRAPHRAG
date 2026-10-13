---
id: auth-tokens
title: Token Management
type: System
status: stable
tags: [auth, tokens, jwt, security]
links:
  - target: auth-service
    relation: part-of
    label: Auth subsystem
  - target: api-gateway
    relation: validated-by
    label: Token validation at gateway
sources:
  - id: auth-tokens-doc
    resource: /source_docs/auth-service/tokens.md
    title: Token Management Guide
generated:
  by: "human:author"
  at: "2026-10-01T00:00:00Z"
---

# Token Management

Token Management is a subsystem of the [Auth Service](../services/auth-service.md) responsible for the full lifecycle of authentication tokens within the Acme Platform. It handles token issuance, validation, rotation, and revocation for both user-facing and service-to-service authentication flows.

## Token Types

The platform uses three token types, each with a distinct purpose and lifetime:

| Token Type | Format | Lifetime | Storage |
|---|---|---|---|
| Access Token | Signed JWT (RS256) | 1 hour | Client-side only |
| Refresh Token | Opaque (UUID v4) | 30 days | Server-side (Redis + PostgreSQL) |
| ID Token | Signed JWT (RS256) | 1 hour | Client-side only |

Access tokens are self-contained JWTs validated at the [API Gateway](../services/api-gateway.md) without a round-trip to the Auth Service. Refresh tokens are opaque identifiers stored server-side, preventing token theft from being exploitable without server cooperation.

## JWT Structure

Every access token contains the following standard and custom claims:

```json
{
  "header": {
    "alg": "RS256",
    "typ": "JWT",
    "kid": "key-2026-09"
  },
  "payload": {
    "iss": "https://auth.acme-platform.io",
    "sub": "user:u-abc123",
    "aud": ["https://api.acme-platform.io"],
    "exp": 1727884800,
    "iat": 1727881200,
    "nbf": 1727881200,
    "jti": "tok-unique-id",
    "scope": "openid profile email",
    "roles": ["platform:editor"],
    "org_id": "org-xyz789",
    "client_id": "acme-web-app"
  }
}
```

The `kid` (Key ID) claim in the header identifies which signing key was used. The gateway fetches the corresponding public key from the JWKS endpoint to validate the signature.

## Token Issuance

Tokens are issued through the `/oauth/token` endpoint after successful authentication. The issuance flow:

1. Validate the grant (authorization code, client credentials, or refresh token).
2. Resolve the subject's roles and permissions from the identity store.
3. Build the JWT payload with the appropriate claims.
4. Sign the token using the current active signing key.
5. Store the refresh token (if applicable) in Redis with a PostgreSQL backup.
6. Return the token response.

```bash
# Token issuance response
POST /oauth/token

{
  "access_token": "eyJhbGciOiJSUzI1NiIs...",
  "refresh_token": "rt-550e8400-e29b-41d4-a716-446655440000",
  "token_type": "Bearer",
  "expires_in": 3600,
  "id_token": "eyJhbGciOiJSUzI1NiIs...",
  "scope": "openid profile email"
}
```

## Token Validation

The [API Gateway](../services/api-gateway.md) validates access tokens on every request using the following checks:

1. **Signature verification** -- Validate the JWT signature against the JWKS public key matching the `kid`.
2. **Expiration check** -- Reject tokens where `exp` is in the past (with a 30-second clock skew tolerance).
3. **Audience check** -- Confirm the `aud` claim includes the API's identifier.
4. **Issuer check** -- Confirm the `iss` claim matches the expected Auth Service URL.
5. **Revocation check** -- Query the token revocation cache (Redis bloom filter) for the `jti`.

Validation is performed entirely at the gateway for access tokens, requiring no call to the Auth Service. This keeps per-request latency under 1ms for the auth check.

## Token Rotation

### Signing Key Rotation

Signing keys are rotated on a monthly schedule. The rotation process ensures zero-downtime:

1. A new RSA key pair is generated and published to the JWKS endpoint alongside the current key.
2. New tokens are signed with the new key; the old key remains in JWKS for validation.
3. After 48 hours (longer than the maximum access token lifetime), the old key is removed from JWKS.

```bash
# JWKS endpoint returns current and previous keys
GET /.well-known/jwks.json

{
  "keys": [
    { "kid": "key-2026-10", "kty": "RSA", "use": "sig", "n": "...", "e": "AQAB" },
    { "kid": "key-2026-09", "kty": "RSA", "use": "sig", "n": "...", "e": "AQAB" }
  ]
}
```

### Refresh Token Rotation

Refresh tokens are rotated on every use. When a client exchanges a refresh token for a new access token:

1. The old refresh token is invalidated immediately.
2. A new refresh token is issued alongside the new access token.
3. If the old refresh token is used again (replay detection), all tokens in the family are revoked.

This provides protection against refresh token theft: if an attacker steals and uses a refresh token, the legitimate client's next refresh attempt will trigger family-wide revocation.

## Token Revocation

Tokens can be revoked through two mechanisms:

### Explicit Revocation

Clients or administrators can revoke tokens via the revocation endpoint:

```bash
POST /oauth/revoke
Content-Type: application/x-www-form-urlencoded
Authorization: Basic base64(client_id:client_secret)

token=eyJhbGciOiJSUzI1NiIs...&
token_type_hint=access_token
```

### Implicit Revocation

Tokens are implicitly revoked when:

- A user changes their password.
- An administrator disables a user account.
- A service client's credentials are rotated.
- A security incident triggers a global key rotation.

Revoked token JTIs are stored in a Redis bloom filter for fast lookup at the gateway. The bloom filter is replicated to all gateway instances with sub-second propagation delay.

## Monitoring

Key metrics for token management:

- `auth_tokens_issued_total{type, grant_type}` -- Token issuance rate.
- `auth_tokens_revoked_total{reason}` -- Revocation rate by reason.
- `auth_token_validation_duration_seconds` -- Validation latency at the gateway.
- `auth_refresh_replay_detected_total` -- Refresh token replay attempts (security signal).
