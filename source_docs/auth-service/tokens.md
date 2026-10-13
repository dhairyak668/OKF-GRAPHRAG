# Token Management

## Overview

The Auth Service issues JSON Web Tokens (JWTs) as access tokens and opaque strings as refresh tokens. This document covers the token structure, signing keys, rotation procedures, the refresh flow, revocation mechanisms, and the JWKS endpoint used by all services on the Acme Platform.

---

## Token Types

The Auth Service issues three types of tokens:

| Token          | Format | Lifetime    | Storage              | Purpose                          |
|----------------|--------|-------------|----------------------|----------------------------------|
| Access Token   | JWT    | 15 minutes  | Client memory only   | Authorize API requests           |
| Refresh Token  | Opaque | 7 days      | Redis + PostgreSQL   | Obtain new access tokens         |
| ID Token       | JWT    | 15 minutes  | Client memory only   | Convey user identity (OIDC)      |

---

## JWT Structure

Every access token is a signed JWT consisting of three Base64URL-encoded parts:

```
<header>.<payload>.<signature>
```

### Header

```json
{
  "alg": "RS256",
  "typ": "JWT",
  "kid": "acme-key-2026-Q3"
}
```

The `kid` (key ID) field identifies which RSA public key from the JWKS endpoint should be used for verification. This supports seamless key rotation.

### Payload (Claims)

```json
{
  "iss": "https://auth.acme-platform.io",
  "sub": "usr_8f3a2c91e4b7",
  "aud": "acme-platform",
  "exp": 1697145600,
  "iat": 1697144700,
  "nbf": 1697144700,
  "jti": "tok_a1b2c3d4e5f6",
  "scope": "openid profile email",
  "roles": ["org_admin"],
  "org_id": "org_xk9m2n",
  "permissions": ["users:read", "users:write", "projects:manage"],
  "client_id": "web-dashboard",
  "amr": ["pwd", "totp"],
  "session_id": "sess_xyz789"
}
```

### Claims Reference

| Claim         | Type     | Description                                         |
|---------------|----------|-----------------------------------------------------|
| `iss`         | string   | Token issuer URL                                    |
| `sub`         | string   | Unique user or service identifier                   |
| `aud`         | string   | Intended audience (platform identifier)             |
| `exp`         | integer  | Expiration time (Unix epoch seconds)                |
| `iat`         | integer  | Issued-at time                                      |
| `nbf`         | integer  | Not-valid-before time                               |
| `jti`         | string   | Unique token identifier for revocation tracking     |
| `scope`       | string   | Space-delimited OAuth scopes                        |
| `roles`       | string[] | User roles at time of issuance                      |
| `org_id`      | string   | Organization the user belongs to                    |
| `permissions` | string[] | Resolved permission set derived from roles          |
| `client_id`   | string   | OAuth client that requested the token               |
| `amr`         | string[] | Authentication methods used (e.g., `["pwd","mfa"]`) |
| `session_id`  | string   | Session this token is bound to                      |

### ID Token Claims

The ID Token follows the OIDC Core specification and includes identity claims:

```json
{
  "iss": "https://auth.acme-platform.io",
  "sub": "usr_8f3a2c91e4b7",
  "aud": "web-dashboard",
  "exp": 1697145600,
  "iat": 1697144700,
  "auth_time": 1697144680,
  "nonce": "n-0S6_WzA2Mj",
  "amr": ["pwd", "totp"],
  "name": "Jane Doe",
  "email": "jane.doe@acme.io",
  "email_verified": true,
  "picture": "https://cdn.acme-platform.io/avatars/usr_8f3a2c91e4b7.jpg"
}
```

### Refresh Token Format

Refresh tokens are **opaque** (not JWTs). They are random 256-bit values, base64url-encoded, and stored server-side in both Redis (for fast lookup) and PostgreSQL (for audit and revocation).

```
Format:  rt_<base64url(32 random bytes)>
Example: rt_dGhpcyBpcyBhIHNhbXBsZSByZWZyZXNoIHRva2Vu...
```

---

## Signing Keys

### Algorithm

The Auth Service uses **RS256** (RSA with SHA-256) by default. ES256 (ECDSA with P-256) is supported as an alternative and can be enabled via `AUTH_JWT_ALGORITHM=ES256`.

| Algorithm | Key Size   | Signature Size | Verify Speed | Use Case                       |
|-----------|------------|----------------|--------------|--------------------------------|
| RS256     | 2048-bit   | 256 bytes      | Fast         | Default, broad compatibility   |
| ES256     | P-256      | 64 bytes       | Very fast    | Smaller tokens, modern clients |

### Key Storage

Signing keys are stored in one of the following backends, configured via `AUTH_KEY_STORE`:

- **`vault`** (recommended for production): HashiCorp Vault Transit engine. Keys never leave Vault; signing is performed via the Vault API.
- **`k8s-secret`**: Kubernetes Secret mounted as a volume. Suitable for staging and smaller deployments.
- **`file`**: PEM files on the local filesystem. For local development only.

```bash
# Generate an RSA key pair for local development
openssl genrsa -out private.pem 2048
openssl rsa -in private.pem -pubout -out public.pem
```

### Signature Verification

The signature is computed using RS256 (or ES256) over the encoded header and payload. Only the Auth Service holds the private key; all other services verify using the public key fetched from the JWKS endpoint.

---

## Key Rotation

Key rotation is critical for limiting the blast radius of a compromised signing key. The Auth Service supports both automatic and manual rotation.

### Rotation Schedule

| Parameter                  | Default | Description                                    |
|----------------------------|---------|------------------------------------------------|
| `AUTH_JWKS_ROTATION_DAYS`  | 90      | Days between automatic key rotations           |
| `AUTH_JWKS_OVERLAP_DAYS`   | 14      | Days the old key remains in JWKS after rotation |

### Rotation Process

```
Day 0:   Key A is the active signing key.
         JWKS contains: [Key A]

Day 90:  Key B is generated and becomes the active signing key.
         JWKS contains: [Key B, Key A]
         (Key A still validates existing tokens)

Day 104: Key A is removed from JWKS.
         JWKS contains: [Key B]
         (All tokens signed by Key A have expired by now)
```

### Manual Rotation

To trigger an immediate key rotation (e.g., after a suspected compromise):

```bash
# Via the admin API
curl -X POST https://auth.acme-platform.io/admin/keys/rotate \
  -H "Authorization: Bearer <platform_owner_token>"

# Via the CLI tool
acme-auth keys rotate --reason "scheduled quarterly rotation"
```

After manual rotation, all active access tokens signed with the old key will continue to validate until they expire (max 15 minutes). If immediate invalidation is required, use the emergency revocation procedure described in the Revocation section.

---

## Refresh Flow

When an access token expires, the client requests a new one using the refresh token:

```
Client                           Auth Service
  |                                   |
  |-- POST /auth/refresh ------------>|
  |   grant_type=refresh_token        |
  |   refresh_token=rt_abc123...      |
  |   client_id=web-dashboard         |
  |                                   |
  |   [Auth Service validates:]       |
  |   1. Token exists in Redis        |
  |   2. Token not revoked            |
  |   3. Token not expired            |
  |   4. Client ID matches            |
  |   5. Session still active         |
  |                                   |
  |<-- 200 OK ------------------------|
  |   access_token (new JWT)          |
  |   refresh_token (new, rotated)    |
  |   expires_in: 900                 |
```

### Refresh Token Rotation

The Auth Service implements **rotating refresh tokens** as recommended by OAuth 2.0 Security Best Current Practice (RFC 6819):

1. Each refresh token is single-use. After a successful refresh, the old token is invalidated and a new one is issued.
2. If a previously used (invalidated) refresh token is presented, the entire token family is revoked. This detects stolen tokens that were replayed.
3. A grace period of 30 seconds allows for network retries without triggering the replay detection.

### Example: Handling Token Refresh in Python

```python
import requests

def refresh_access_token(refresh_token: str) -> dict:
    response = requests.post(
        "https://auth.acme-platform.io/auth/refresh",
        json={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": "my-service"
        }
    )

    if response.status_code == 401:
        # Refresh token expired or revoked; re-authenticate
        raise AuthenticationRequired("Session expired")

    data = response.json()
    # IMPORTANT: store the NEW refresh token for next use
    store_refresh_token(data["refresh_token"])
    return data
```

---

## Token Revocation

### Single Token Revocation

Refresh tokens can be explicitly revoked via the `/auth/revoke` endpoint:

```bash
curl -X POST https://auth.acme-platform.io/auth/revoke \
  -H "Authorization: Bearer <access_token>" \
  -H "Content-Type: application/json" \
  -d '{
    "token": "rt_7g8h9i0j1k2l3m4n"
  }'
```

This immediately deletes the refresh token from Redis. The associated access token will continue to work until its natural expiration (max 15 minutes), unless the consuming service checks the `jti` against the revocation list.

### Session-Based Revocation

Revoking a session invalidates all refresh tokens bound to that session:

```bash
curl -X DELETE https://auth.acme-platform.io/admin/sessions/sess_xyz789 \
  -H "Authorization: Bearer <admin_token>"
```

### Emergency: Global Token Invalidation

In the event of a key compromise, platform owners can force-expire all tokens globally:

```bash
acme-auth emergency-revoke --scope=global --reason="key compromise"
```

This performs two actions:
1. Rotates all signing keys immediately
2. Purges all refresh tokens from Redis and marks them revoked in PostgreSQL

All users will be required to re-authenticate after a global revocation.

### Access Token Revocation via JTI

For high-security flows, consuming services can optionally check the `jti` claim against a revocation list maintained in Redis. This enables near-real-time access token invalidation at the cost of an additional Redis lookup per request.

```python
def is_token_revoked(jti: str) -> bool:
    return redis_client.exists(f"revoked:jti:{jti}")
```

---

## JWKS Endpoint

The Auth Service exposes a JSON Web Key Set at `/.well-known/jwks.json`. All resource servers use this endpoint to fetch the public keys needed to verify JWT signatures.

### Endpoint Details

| Property      | Value                                                  |
|---------------|--------------------------------------------------------|
| URL           | `https://auth.acme-platform.io/.well-known/jwks.json` |
| Method        | GET                                                    |
| Auth required | No (public endpoint)                                   |
| Cache-Control | `public, max-age=3600`                                 |
| Content-Type  | `application/json`                                     |

### Example Response

```json
{
  "keys": [
    {
      "kty": "RSA",
      "kid": "acme-key-2026-Q4",
      "use": "sig",
      "alg": "RS256",
      "n": "0vx7agoebGcQSuuPiLJXZptN9nndrQmbXEps2aiAFbWhM78LhWx4cbbfAAtVT86zwu1RK7aPFFxuhDR1L6tSoc_BJECPebWKRXjBZCiFV4n3oknjhMstn64tZ_2W-5JsGY4Hc5n9yBXArwl93lqt7_RN5w6Cf0h4QyQ5v-65YGjQR0_FDW2QvzqY368QQMicAtaSqzs8KJZgnYb9c7d0zgdAZHzu6qMQvRL5hajrn1n91CbOpbISD08qNLyrdkt-bFTWhAI4vMQFh6WeZu0fM4lFd2NcRwr3XPksINHaQ-G_xBniIqbw0Ls1jF44-csFCur-kEgU8awapJzKnqDKgw",
      "e": "AQAB"
    },
    {
      "kty": "RSA",
      "kid": "acme-key-2026-Q3",
      "use": "sig",
      "alg": "RS256",
      "n": "sXchDaQebHnPiGvhGPEUBrUlgP4B_krVqaQZKzGpLe5Q...",
      "e": "AQAB"
    }
  ]
}
```

During key rotation, the JWKS contains both the new and old keys (as shown above with Q4 and Q3 keys).

### Caching Recommendations for Resource Servers

Resource servers should cache the JWKS response and refresh it:

- On a regular interval (recommended: every 5 minutes)
- Immediately when encountering an unknown `kid` in a JWT header (to handle key rotation gracefully)
- Never more frequently than once per minute (to avoid overwhelming the Auth Service)

```javascript
// Example: Node.js JWKS client with caching
const jwksClient = require("jwks-rsa");

const client = jwksClient({
  jwksUri: "https://auth.acme-platform.io/.well-known/jwks.json",
  cache: true,
  cacheMaxEntries: 5,
  cacheMaxAge: 300000,  // 5 minutes
  rateLimit: true,
  jwksRequestsPerMinute: 10
});

async function getSigningKey(kid) {
  const key = await client.getSigningKey(kid);
  return key.getPublicKey();
}
```

### Python Validation Example

```python
import jwt
import requests

# Fetch JWKS (cache this -- do not fetch on every request)
jwks = requests.get(
    "https://auth.acme-platform.io/.well-known/jwks.json"
).json()

# Decode and validate the access token
payload = jwt.decode(
    token,
    key=jwks,               # library matches kid automatically
    algorithms=["RS256"],
    audience="acme-platform",
    issuer="https://auth.acme-platform.io"
)

# Check permissions
if "users:read" not in payload.get("permissions", []):
    raise PermissionError("Insufficient permissions")
```

**Validation checklist:**
- Verify the signature against the JWKS public key matching the `kid`
- Confirm `exp` has not passed (token not expired)
- Confirm `nbf` has passed (token is active)
- Confirm `iss` matches the expected issuer
- Confirm `aud` includes the validating service
- Check `jti` against the revocation list (optional, for high-security flows)

---

## Token Storage Best Practices

### Browser Applications
- Store access tokens in a JavaScript closure or in-memory variable. Never use `localStorage`.
- Store refresh tokens in `HttpOnly`, `Secure`, `SameSite=Strict` cookies.
- Use the Backend-for-Frontend (BFF) pattern to keep tokens out of the browser entirely when possible.

### Mobile Applications
- Use the platform keychain (iOS Keychain, Android Keystore).
- Clear tokens on app uninstall or user logout.

### Backend Services
- Use the client credentials grant; do not store user tokens.
- Cache access tokens in memory until 60 seconds before expiration.
- Retrieve client secrets from Vault at startup, never from environment variables in production.

---

## Related Documentation

- [Auth Service README](README.md) -- full service overview, RBAC, MFA, and API reference
- [API Gateway](/api-gateway/README.md) -- upstream routing and rate limiting
- [Notification Engine](/notification-engine/README.md) -- MFA code delivery
