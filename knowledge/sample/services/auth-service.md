---
id: auth-service
title: Authentication Service
type: System
status: stable
tags: [auth, security, oauth, jwt]
links:
  - target: api-gateway
    relation: authenticates-for
    label: Request authentication
  - target: auth-tokens
    relation: manages
    label: Token lifecycle
  - target: sdk-client
    relation: used-by
    label: SDK authentication
sources:
  - id: auth-readme
    resource: /source_docs/auth-service/README.md
    title: Auth Service README
generated:
  by: "human:author"
  at: "2026-10-01T00:00:00Z"
---

# Authentication Service

The Authentication Service is the central identity and access management system for the Acme Platform. It handles user authentication, service-to-service authorization, and token lifecycle management. The service implements OAuth 2.0 and OpenID Connect (OIDC) protocols and serves as the external authorization provider for the [API Gateway](../services/api-gateway.md).

## Overview

The Auth Service provides the following capabilities:

- **User Authentication** via OAuth 2.0 authorization code flow with PKCE for web and mobile clients.
- **Service Authentication** via OAuth 2.0 client credentials flow for machine-to-machine communication.
- **Token Management** through the [Token Management](../services/auth-tokens.md) subsystem, including issuance, validation, rotation, and revocation of JWT access and refresh tokens.
- **Role-Based Access Control (RBAC)** with hierarchical roles and fine-grained permissions.
- **Multi-Factor Authentication (MFA)** supporting TOTP, WebAuthn/FIDO2, and SMS backup codes.
- **Identity Federation** with external identity providers (Okta, Azure AD, Google Workspace) via SAML 2.0 and OIDC.

The service is consumed directly by the [SDK Client](../libraries/sdk-client.md) for client-side authentication flows and by the API Gateway for request-level authorization decisions.

## Architecture

The Auth Service is deployed as a stateless Go service backed by PostgreSQL for persistent identity data and Redis for session state and token caching.

```
Client -> API Gateway -> Auth Service -> PostgreSQL (identity store)
                                      -> Redis (sessions, token cache)
                                      -> External IdP (federation)
```

The service exposes two interfaces:

1. **HTTP/REST** -- Public-facing endpoints for OAuth flows, user management, and MFA enrollment.
2. **gRPC** -- Internal interface consumed by the API Gateway's ext_authz filter for per-request authorization.

## OAuth 2.0 Flows

### Authorization Code Flow (PKCE)

Used by web and mobile clients via the [SDK Client](../libraries/sdk-client.md):

```bash
# Step 1: Redirect user to authorization endpoint
GET /oauth/authorize?
  response_type=code&
  client_id=acme-web-app&
  redirect_uri=https://app.acme-platform.io/callback&
  scope=openid profile email&
  code_challenge=E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM&
  code_challenge_method=S256&
  state=xyz123

# Step 2: Exchange authorization code for tokens
POST /oauth/token
Content-Type: application/x-www-form-urlencoded

grant_type=authorization_code&
code=AUTH_CODE_HERE&
redirect_uri=https://app.acme-platform.io/callback&
client_id=acme-web-app&
code_verifier=dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk
```

### Client Credentials Flow

Used for service-to-service authentication:

```bash
POST /oauth/token
Content-Type: application/x-www-form-urlencoded
Authorization: Basic base64(client_id:client_secret)

grant_type=client_credentials&
scope=notifications:send events:publish
```

Response:

```json
{
  "access_token": "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9...",
  "token_type": "Bearer",
  "expires_in": 3600,
  "scope": "notifications:send events:publish"
}
```

## JWT Token Structure

Access tokens are signed JWTs with the following claims structure:

```json
{
  "iss": "https://auth.acme-platform.io",
  "sub": "user:u-abc123",
  "aud": ["https://api.acme-platform.io"],
  "exp": 1727884800,
  "iat": 1727881200,
  "jti": "tok-def456",
  "scope": "openid profile email",
  "roles": ["platform:editor", "team:acme-core:admin"],
  "org_id": "org-xyz789"
}
```

Tokens are signed using RS256 with rotating RSA key pairs. The JWKS endpoint at `/.well-known/jwks.json` publishes the current and previous public keys.

For detailed token lifecycle management, see [Token Management](../services/auth-tokens.md).

## Role-Based Access Control

RBAC is implemented with a three-level hierarchy:

1. **Platform Roles** -- Global roles like `platform:admin`, `platform:editor`, `platform:viewer`.
2. **Team Roles** -- Scoped to a team, e.g., `team:acme-core:admin`.
3. **Resource Roles** -- Scoped to a specific resource, e.g., `project:proj-123:contributor`.

Permissions are resolved by traversing the hierarchy from most specific to least specific. A deny at any level overrides an allow at a broader level.

```yaml
roles:
  - name: platform:editor
    permissions:
      - "services:read"
      - "services:write"
      - "events:read"
      - "notifications:read"
      - "notifications:write"

  - name: platform:viewer
    permissions:
      - "services:read"
      - "events:read"
      - "notifications:read"
```

## Service-to-Service Authentication

Internal services authenticate using mTLS certificates issued by the platform CA and short-lived JWT tokens obtained via the client credentials flow. The [API Gateway](../services/api-gateway.md) validates both the mTLS certificate and the JWT on every request.

## Health and Metrics

The Auth Service exposes `/healthz` (liveness), `/readyz` (readiness), and `/metrics` (Prometheus) endpoints. Key metrics include:

- `auth_requests_total` -- Total authentication requests by type and result.
- `auth_token_issued_total` -- Tokens issued by grant type.
- `auth_latency_seconds` -- Authentication latency histogram.
- `auth_mfa_challenges_total` -- MFA challenges issued and completed.
