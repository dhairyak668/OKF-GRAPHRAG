# Auth Service

The Auth Service is the central identity and access management component of the Acme Platform. It handles user authentication, authorization, session management, and token issuance for all downstream microservices.

## Table of Contents

- [Overview](#overview)
- [Architecture](#architecture)
- [Authentication Flows](#authentication-flows)
- [User Management](#user-management)
- [Role-Based Access Control (RBAC)](#role-based-access-control-rbac)
- [Session Handling](#session-handling)
- [Password Policies](#password-policies)
- [Multi-Factor Authentication (MFA)](#multi-factor-authentication-mfa)
- [API Reference](#api-reference)
- [Configuration](#configuration)
- [Deployment](#deployment)

## Overview

The Auth Service implements the OAuth 2.0 and OpenID Connect (OIDC) specifications to provide a standards-compliant identity layer. It is built with Node.js (Express) and backed by PostgreSQL for persistent storage and Redis for session/token caching.

**Current version:** 3.2.1
**Protocol support:** OAuth 2.0 (RFC 6749), OIDC Core 1.0, PKCE (RFC 7636)
**Runtime:** Node.js 20 LTS on Alpine Linux

**Key responsibilities:**

- Issue and validate JWT access tokens and refresh tokens
- Manage user accounts, profiles, and credentials
- Enforce RBAC policies across the platform
- Provide OIDC discovery and JWKS endpoints for service-to-service verification
- Support MFA via TOTP and WebAuthn
- Federated login via external identity providers (Google, GitHub, SAML)

---

## Architecture

```
                         +-------------------+
                         |   API Gateway     |
                         | (rate limiting,   |
                         |  TLS termination) |
                         +---------+---------+
                                   |
                    +--------------+--------------+
                    |                             |
           +--------v--------+          +---------v--------+
           |  Auth Service   |          |  Other Services  |
           |  (this repo)    |          |  (orders, users, |
           |                 |          |   billing, etc.) |
           +--------+--------+          +---------+--------+
                    |                             |
          +---------+---------+                   |
          |         |         |          +--------v--------+
   +------v---+ +---v-----+ +v-------+  | Token Validation|
   | PostgreSQL| |  Redis  | | Kafka  |  | (JWT verify via |
   | (accounts,| |(sessions| |(audit  |  |  JWKS endpoint) |
   |  roles,   | | tokens, | | log)   |  +-----------------+
   |  audit)   | | MFA)    | |        |
   +----------+  +--------+  +-------+
```

### Component Responsibilities

| Component         | Role                                                    |
|-------------------|---------------------------------------------------------|
| Auth Service      | Core logic for login, token issuance, RBAC evaluation   |
| Token Validator   | Sidecar library for fast JWT verification in services   |
| User DB           | Stores user accounts, credentials, roles, permissions   |
| Session Store     | Redis-backed session and refresh token persistence      |
| Audit Log         | Immutable event stream via Kafka for all auth operations|

### Service Dependencies

| Dependency       | Purpose                                | Required |
|------------------|----------------------------------------|----------|
| PostgreSQL 15+   | User accounts, roles, audit trail      | Yes      |
| Redis 7+         | Session store, token blacklist, MFA    | Yes      |
| Event Bus (NATS) | Publish auth events (login, logout)    | No       |
| Notification Svc | Send MFA codes, password reset emails  | No       |
| Kafka            | Audit event streaming                  | No       |

---

## Authentication Flows

### Authorization Code Flow (with PKCE)

This is the recommended flow for web and mobile applications.

```
Client                    Auth Service                  Resource Server
  |                            |                              |
  |-- 1. /authorize ---------->|                              |
  |   (code_challenge,         |                              |
  |    redirect_uri)           |                              |
  |                            |                              |
  |<-- 2. redirect with -------|                              |
  |       authorization_code   |                              |
  |                            |                              |
  |-- 3. /token -------------->|                              |
  |   (code, code_verifier)    |                              |
  |                            |                              |
  |<-- 4. access_token, -------|                              |
  |       refresh_token,       |                              |
  |       id_token             |                              |
  |                            |                              |
  |-- 5. API call with ---------------------------------->    |
  |       Bearer token         |                              |
  |                            |                              |
  |<-- 6. Protected resource ----------------------------|    |
```

### Client Credentials Flow

Used for service-to-service communication where no user context is needed.

```python
import requests

response = requests.post(
    "https://auth.acme-platform.io/auth/token",
    json={
        "grant_type": "client_credentials",
        "client_id": "notification-engine",
        "client_secret": "svc_notif_xxxxxxxxxxxxxxxx",
        "scope": "notifications:send users:read"
    }
)

token_data = response.json()
# {
#   "access_token": "eyJhbGciOiJSUzI1NiIs...",
#   "token_type": "Bearer",
#   "expires_in": 3600,
#   "scope": "notifications:send users:read"
# }
```

### Resource Owner Password Grant (Legacy)

> **Deprecated.** This flow is supported only for backward compatibility with legacy internal tools. New integrations must use the Authorization Code flow with PKCE.

```bash
curl -X POST https://auth.acme-platform.io/auth/token \
  -H "Content-Type: application/json" \
  -d '{
    "grant_type": "password",
    "client_id": "web-dashboard",
    "username": "jdoe@acme.io",
    "password": "s3cureP@ss!",
    "scope": "openid profile email"
  }'
```

**Response:**

```json
{
  "access_token": "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9...",
  "token_type": "Bearer",
  "expires_in": 900,
  "refresh_token": "dGhpcyBpcyBhbiBvcGFxdWUgcmVmcmVzaCB0b2tlbg...",
  "id_token": "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9...",
  "scope": "openid profile email"
}
```

---

## User Management

### Creating a User

```javascript
const response = await fetch("https://auth.acme-platform.io/admin/users", {
  method: "POST",
  headers: {
    "Authorization": "Bearer <admin_token>",
    "Content-Type": "application/json"
  },
  body: JSON.stringify({
    email: "jane.doe@acme.io",
    display_name: "Jane Doe",
    roles: ["platform:viewer"],
    send_invite: true
  })
});

const user = await response.json();
// {
//   "id": "usr_8f3a2b1c-d4e5-6789-abcd-ef0123456789",
//   "email": "jane.doe@acme.io",
//   "display_name": "Jane Doe",
//   "status": "INVITED",
//   "roles": ["platform:viewer"],
//   "created_at": "2026-10-13T14:30:00Z"
// }
```

### User Lifecycle States

```
  INVITED --> ACTIVE --> SUSPENDED --> DELETED
     |                      ^
     |                      |
     +--- (expires) ------->+
```

| State     | Description                                                   |
|-----------|---------------------------------------------------------------|
| INVITED   | Account created, awaiting email confirmation                  |
| ACTIVE    | Fully operational, can authenticate and access resources      |
| SUSPENDED | Temporarily locked out (admin action or policy violation)     |
| DELETED   | Soft-deleted, data retained for 90 days per retention policy  |

### Bulk User Import

For onboarding large teams, the Auth Service supports CSV-based bulk import:

```bash
curl -X POST https://auth.acme-platform.io/admin/users/import \
  -H "Authorization: Bearer <admin_token>" \
  -F "file=@users.csv" \
  -F "default_role=team:member" \
  -F "send_invites=true"
```

The CSV format expects columns: `email`, `display_name`, `team_id` (optional).

---

## Role-Based Access Control (RBAC)

The Auth Service implements a hierarchical RBAC model with three layers: **organizations**, **teams**, and **resources**.

### Role Hierarchy

```
super_admin > org_admin > project_admin > member > viewer
```

### Permission Structure

```
organization:acme-corp
  |
  +-- team:engineering
  |     +-- role:admin    -> [*]
  |     +-- role:member   -> [read, write, deploy]
  |     +-- role:viewer   -> [read]
  |
  +-- team:billing
        +-- role:admin    -> [*]
        +-- role:auditor  -> [read, export]
```

### Built-in Roles

| Role              | Scope        | Permissions                              |
|-------------------|--------------|------------------------------------------|
| `super_admin`     | Platform     | Full access to all organizations         |
| `org_admin`       | Organization | Manage users, teams, billing, settings   |
| `project_admin`   | Team         | Manage team members and team resources   |
| `member`          | Team         | Read/write access to team resources      |
| `viewer`          | Team         | Read-only access to team resources       |

### Fine-Grained Permission Sets

Each role maps to a set of permissions using the format `resource:action`:

```json
{
  "role": "org_admin",
  "permissions": [
    "users:read", "users:write", "users:delete",
    "teams:read", "teams:write", "teams:delete",
    "projects:read", "projects:write", "projects:manage",
    "billing:read", "billing:write",
    "audit:read", "audit:export"
  ]
}
```

### Checking Permissions (Middleware Example)

```javascript
// Express middleware for permission checks
const requirePermission = (resource, action) => {
  return async (req, res, next) => {
    const allowed = await authClient.checkPermission({
      subject: req.user.id,
      resource: resource,
      action: action,
      context: { team_id: req.params.teamId }
    });

    if (!allowed) {
      return res.status(403).json({
        error: "forbidden",
        message: `Missing permission: ${action} on ${resource}`
      });
    }
    next();
  };
};

app.delete("/projects/:id", requirePermission("projects", "delete"), handler);
```

### Scope-Based Authorization for OAuth Clients

OAuth clients request scopes during authorization. Scopes are mapped to permission sets and intersected with the user's roles to determine the effective permissions embedded in the access token.

```
Requested scopes: ["orders:read", "orders:write", "billing:read"]
User permissions:  ["orders:read", "orders:write", "users:read"]
Effective scopes:  ["orders:read", "orders:write"]  (intersection)
```

---

## Session Handling

Sessions are stored in Redis with a configurable TTL. Each session record contains the user ID, device fingerprint, IP address, and the set of active refresh tokens bound to it.

### Session Configuration

| Parameter                  | Default      | Description                          |
|----------------------------|--------------|--------------------------------------|
| `AUTH_SESSION_MAX`         | 5            | Max concurrent sessions per user     |
| `SESSION_IDLE_TIMEOUT`     | 24h          | Idle session timeout                 |
| `SESSION_ABSOLUTE_TIMEOUT` | 7d           | Maximum session lifetime             |
| `SESSION_BIND_IP`          | false        | Bind session to originating IP       |
| `SESSION_COOKIE_NAME`      | `__acme_sid` | Name of the session cookie           |
| `SESSION_COOKIE_SECURE`    | true         | Require HTTPS for cookie transport   |
| `SESSION_COOKIE_SAMESITE`  | Strict       | SameSite attribute for the cookie    |

When `AUTH_SESSION_MAX` is exceeded, the oldest session is revoked automatically.

### Device Fingerprinting

Each session captures a device fingerprint hash derived from:
- User-Agent string
- Accept-Language header
- Screen resolution (if reported by client SDK)
- Timezone offset

Anomaly detection flags sessions where the fingerprint changes significantly mid-session, which may indicate token theft.

### Forced Session Termination

Administrators can terminate any user's session:

```bash
curl -X DELETE https://auth.acme-platform.io/admin/sessions/sess_xyz789 \
  -H "Authorization: Bearer <admin_token>"
```

---

## Password Policies

Password requirements are configurable per organization. The defaults follow NIST SP 800-63B guidelines.

| Policy                  | Default             | Description                                |
|-------------------------|---------------------|--------------------------------------------|
| Minimum length          | 12 characters       | Enforced at registration and password change |
| Maximum length          | 128 characters      | Prevents denial-of-service via bcrypt       |
| Complexity rules        | None (NIST-aligned) | No forced uppercase/symbol requirements     |
| Breached password check | Enabled             | Checked against HaveIBeenPwned k-anonymity API |
| Password history        | Last 5              | Prevents reuse of recent passwords          |
| Lockout threshold       | 10 failed attempts  | Account locked for 15 minutes               |
| Bcrypt cost factor      | 12                  | Tuned for ~250ms hash time on target hardware |

### Password Reset Flow

1. User requests reset via `POST /auth/password/reset`
2. Auth Service generates a one-time token (SHA-256, 32 bytes) stored in Redis with a 1-hour TTL
3. Notification Service sends the reset link to the user's verified email
4. User submits new password with the reset token via `POST /auth/password/reset/confirm`
5. All existing sessions and refresh tokens for the user are revoked
6. An `auth.password.changed` event is published to the audit log

### Rate Limiting on Login

Login endpoints are rate-limited to prevent brute force attacks:

| Threshold                  | Action                                      |
|----------------------------|---------------------------------------------|
| 5 failures in 1 minute     | Progressive delay (exponential backoff)     |
| 10 failures in 5 minutes   | Account temporarily locked for 15 minutes   |
| 50 failures in 1 hour      | Source IP blocked for 1 hour                |

---

## Multi-Factor Authentication (MFA)

The Auth Service supports three MFA methods:

### TOTP (Time-Based One-Time Password)

Compatible with any TOTP authenticator app (Google Authenticator, Authy, 1Password, etc.).

```bash
# Enable TOTP for a user
curl -X POST https://auth.acme-platform.io/auth/mfa/setup \
  -H "Authorization: Bearer <user_token>" \
  -H "Content-Type: application/json" \
  -d '{"method": "totp"}'

# Response:
# {
#   "secret": "JBSWY3DPEHPK3PXP",
#   "qr_code_uri": "otpauth://totp/AcmePlatform:jane@acme.io?secret=JBSWY3DPEHPK3PXP&issuer=AcmePlatform",
#   "backup_codes": ["a1b2c3d4", "e5f6g7h8", "i9j0k1l2", "m3n4o5p6", "q7r8s9t0"]
# }
```

### SMS One-Time Codes

Delivered via the Notification Engine. A 6-digit code is sent to the user's verified phone number with a 5-minute expiration window.

### WebAuthn / FIDO2

Hardware security keys and platform authenticators (Touch ID, Windows Hello).

```javascript
// Registration (client-side)
const credential = await navigator.credentials.create({
  publicKey: {
    challenge: base64ToBuffer(serverChallenge),
    rp: { name: "Acme Platform", id: "acme-platform.io" },
    user: {
      id: base64ToBuffer(userId),
      name: userEmail,
      displayName: userDisplayName
    },
    pubKeyCredParams: [
      { type: "public-key", alg: -7 },   // ES256
      { type: "public-key", alg: -257 }  // RS256
    ],
    authenticatorSelection: {
      authenticatorAttachment: "cross-platform",
      userVerification: "preferred"
    }
  }
});

// Send attestation to server
await fetch("/auth/mfa/webauthn/register", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({
    attestation: bufferToBase64(credential.response.attestationObject),
    client_data: bufferToBase64(credential.response.clientDataJSON)
  })
});
```

### MFA Enforcement Policies

| Policy               | Description                                       |
|----------------------|---------------------------------------------------|
| `mfa_optional`       | Users may enable MFA voluntarily                  |
| `mfa_encouraged`     | Prompt shown at login if MFA is not configured    |
| `mfa_required`       | Login blocked until MFA is configured             |
| `mfa_required_admin` | MFA mandatory for admin roles, optional otherwise |

---

## API Reference

### Authentication Endpoints

| Method | Path                          | Description                          |
|--------|-------------------------------|--------------------------------------|
| POST   | `/auth/token`                 | Issue access + refresh tokens        |
| POST   | `/auth/refresh`               | Refresh an expired access token      |
| POST   | `/auth/revoke`                | Revoke a refresh token               |
| GET    | `/auth/userinfo`              | Get authenticated user profile       |
| POST   | `/auth/logout`                | Terminate session and revoke tokens  |
| POST   | `/auth/mfa/setup`             | Initialize MFA enrollment            |
| POST   | `/auth/mfa/verify`            | Verify an MFA challenge              |
| POST   | `/auth/password/reset`        | Request password reset               |
| POST   | `/auth/password/reset/confirm`| Confirm password reset               |

### Administration Endpoints

| Method | Path                          | Description                        |
|--------|-------------------------------|------------------------------------|
| GET    | `/admin/users`                | List users (paginated)             |
| POST   | `/admin/users`                | Create a new user                  |
| GET    | `/admin/users/{id}`           | Get user details                   |
| PATCH  | `/admin/users/{id}`           | Update user profile                |
| PUT    | `/admin/users/{id}/roles`     | Update user roles                  |
| DELETE | `/admin/sessions/{id}`        | Force-terminate a session          |
| GET    | `/admin/audit`                | Query audit log                    |
| POST   | `/admin/users/import`         | Bulk import users via CSV          |

### Service / Discovery Endpoints

| Method | Path                                  | Description                  |
|--------|---------------------------------------|------------------------------|
| GET    | `/health`                             | Liveness probe               |
| GET    | `/ready`                              | Readiness probe              |
| GET    | `/.well-known/openid-configuration`   | OIDC discovery document      |
| GET    | `/.well-known/jwks.json`              | JSON Web Key Set             |

---

## Configuration

Configuration is loaded from environment variables, with fallback to `config/default.yaml`.

### Environment Variables

| Variable                    | Default                          | Description                              |
|-----------------------------|----------------------------------|------------------------------------------|
| `AUTH_PORT`                 | `4000`                           | HTTP listen port                         |
| `AUTH_DB_URL`               | (required)                       | PostgreSQL connection string             |
| `AUTH_REDIS_URL`            | (required)                       | Redis connection string                  |
| `AUTH_JWT_ISSUER`           | `https://auth.acme-platform.io`  | JWT `iss` claim value                    |
| `AUTH_JWT_ACCESS_TTL`       | `900`                            | Access token lifetime in seconds         |
| `AUTH_JWT_REFRESH_TTL`      | `604800`                         | Refresh token lifetime in seconds        |
| `AUTH_JWT_SIGNING_KEY_PATH` | `/secrets/rsa.pem`               | Path to RSA private key for signing      |
| `AUTH_JWT_ALGORITHM`        | `RS256`                          | Signing algorithm (RS256 or ES256)       |
| `AUTH_MFA_ISSUER_NAME`      | `AcmePlatform`                   | Name shown in authenticator apps         |
| `AUTH_SESSION_MAX`          | `5`                              | Max concurrent sessions per user         |
| `AUTH_AUDIT_KAFKA_BROKERS`  | `kafka:9092`                     | Kafka broker list for audit events       |
| `AUTH_RATE_LIMIT_LOGIN`     | `10`                             | Max login attempts per minute per IP     |
| `AUTH_CORS_ORIGINS`         | `*`                              | Allowed CORS origins (comma-separated)   |
| `AUTH_LOG_LEVEL`            | `info`                           | Log level: debug, info, warn, error      |
| `AUTH_JWKS_ROTATION_DAYS`   | `90`                             | Days between automatic key rotations     |
| `AUTH_KEY_STORE`            | `k8s-secret`                     | Key storage backend (vault, k8s-secret, file) |

---

## Getting Started

### Prerequisites

- Docker 24+ and Docker Compose v2
- PostgreSQL 15+ (or use the bundled Docker image)
- Redis 7+ (or use the bundled Docker image)
- Access to the Acme Platform container registry

### Quick Start with Docker Compose

```bash
# Clone the repository
git clone https://github.com/acme-platform/auth-service.git
cd auth-service

# Copy environment template
cp .env.example .env

# Start all dependencies
docker compose up -d

# Run database migrations
docker compose exec auth-service npm run migrate

# Verify the service is healthy
curl http://localhost:4000/health
```

### Local Development

```bash
npm install
npm run dev    # starts with hot-reload on port 4000
npm run test   # runs the full test suite
```

---

## Deployment

The Auth Service is deployed as a container on Kubernetes. See the `deploy-tools/` directory for Helm charts and CI/CD pipeline configuration.

```yaml
# Minimum resource requirements
resources:
  requests:
    cpu: 250m
    memory: 256Mi
  limits:
    cpu: 1000m
    memory: 512Mi

# Health checks
livenessProbe:
  httpGet:
    path: /health
    port: 4000
  initialDelaySeconds: 10
  periodSeconds: 15

readinessProbe:
  httpGet:
    path: /ready
    port: 4000
  initialDelaySeconds: 5
  periodSeconds: 10
```

### Scaling Considerations

- The Auth Service is stateless (sessions in Redis) and scales horizontally
- Recommended: 3+ replicas behind the API Gateway for high availability
- Database connections are pooled; adjust pool size relative to replica count
- Token signing uses RSA keys loaded at startup; all replicas must share the same key material via a Kubernetes Secret or Vault
- Redis should be deployed as a cluster or sentinel setup for high availability

---

## Security Considerations

1. **Key rotation:** RSA signing keys should be rotated every 90 days. The JWKS endpoint serves both current and previous keys during the overlap window so existing tokens remain valid.

2. **Rate limiting:** Login endpoints are rate-limited to 10 attempts per minute per source IP. After 5 consecutive failures, a progressive delay (exponential backoff) is applied to the account.

3. **Token storage:** Access tokens must only be stored in memory on the client side. Refresh tokens should be stored in `HttpOnly`, `Secure`, `SameSite=Strict` cookies when used from browsers.

4. **Audit trail:** Every authentication event (login, logout, token refresh, MFA challenge, role change) is published to a Kafka topic and retained for 365 days for compliance purposes.

5. **Secrets management:** All secrets (database credentials, signing keys, API keys) are injected at runtime via HashiCorp Vault. Never commit secrets to source control.

6. **HTTPS enforcement:** The Auth Service must only be exposed over TLS in production. The health and readiness endpoints are the only exception, as they are accessed via the internal cluster network.

---

## Related Documentation

- [Token Management](tokens.md) -- JWT structure, lifecycle, and validation
- [API Gateway](/api-gateway/README.md) -- upstream routing and rate limiting
- [Notification Engine](/notification-engine/README.md) -- MFA SMS/email delivery
- [Event Bus](/event-bus/README.md) -- auth event publishing
