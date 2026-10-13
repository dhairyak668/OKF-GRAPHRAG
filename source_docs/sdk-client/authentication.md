# SDK Authentication Guide

## Overview

The Acme Platform SDK supports multiple authentication methods depending on your use case. All authentication is managed transparently by the SDK once configured -- tokens are cached, refreshed automatically, and injected into every outgoing request.

## Authentication Methods

### API Key Authentication

The simplest method, suitable for server-side services and scripts. API keys are long-lived credentials associated with a service account.

```python
from acme_platform import AcmePlatformClient

client = AcmePlatformClient(api_key="acme_sk_live_4f8a2b...")
```

API keys are passed in the `X-Acme-Api-Key` header on every request. They do not expire but can be rotated through the Acme Platform console.

### OAuth Client Credentials

For service-to-service communication in production environments, OAuth client credentials flow is recommended. The SDK handles token acquisition and refresh automatically.

```python
client = AcmePlatformClient(
    client_id="svc-order-processor",
    client_secret="your-client-secret",
    token_url="https://auth.acmeplatform.io/oauth/token",
    scopes=["events:publish", "notifications:send", "orders:read"]
)
```

The SDK requests an access token on first use and caches it in memory. When the token nears expiration (within 60 seconds of the `expires_in` value), the SDK proactively refreshes it before the next request.

### User Bearer Tokens

When acting on behalf of a user (for example, in a BFF or gateway service), pass a pre-obtained bearer token:

```python
client = AcmePlatformClient(bearer_token="eyJhbGciOiJSUzI1NiIs...")
```

The SDK does not manage the lifecycle of user tokens. Your application is responsible for obtaining and refreshing them via the Acme Identity Provider.

## Token Caching and Refresh

The SDK uses an in-memory token cache by default. For distributed deployments where multiple instances share credentials, you can provide a custom cache backend:

```python
from acme_platform.auth import RedisTokenCache

cache = RedisTokenCache(
    redis_url="redis://cache.internal:6379/0",
    key_prefix="acme:tokens:"
)

client = AcmePlatformClient(
    client_id="svc-order-processor",
    client_secret="your-client-secret",
    token_cache=cache
)
```

### Refresh Behavior

The token refresh strategy follows these rules:

1. **Proactive refresh**: If the cached token will expire within `refresh_buffer` seconds (default 60), a background refresh is triggered before the next request.
2. **On-demand refresh**: If a request receives a `401 Unauthorized`, the SDK invalidates the cached token, acquires a new one, and retries the request once.
3. **Retry limit**: If the retry also fails with `401`, an `AuthenticationError` is raised to the caller.

```python
client = AcmePlatformClient(
    client_id="svc-order-processor",
    client_secret="your-client-secret",
    refresh_buffer=120  # Refresh 2 minutes before expiry
)
```

## Service Account Setup

Service accounts are created through the Acme Platform console or the Admin API:

```python
from acme_platform.admin import AdminClient

admin = AdminClient(api_key="acme_admin_key")

service_account = admin.service_accounts.create(
    name="order-processor-prod",
    description="Handles order processing in production",
    scopes=["events:publish", "orders:read", "orders:write"],
    environment="production"
)

print(f"Client ID: {service_account.client_id}")
print(f"Client Secret: {service_account.client_secret}")  # Shown once
```

Service accounts have the following properties:
- Bound to a single environment (production, staging, development)
- Scoped to specific permissions
- Support credential rotation without downtime (dual-active secrets)
- Audit-logged for all token acquisitions

## Multi-Tenant Authentication

For platforms that serve multiple tenants, the SDK supports tenant-scoped authentication:

```python
client = AcmePlatformClient(
    client_id="svc-multitenant-gateway",
    client_secret="your-client-secret"
)

# Make a request on behalf of a specific tenant
response = client.http.get(
    "/api/v2/orders",
    tenant_id="tenant-acme-corp"
)

# Or set the tenant context for all subsequent requests
client.auth.set_tenant("tenant-acme-corp")
```

When a `tenant_id` is specified, the SDK includes the `X-Acme-Tenant-Id` header and requests a tenant-scoped token if using OAuth. The token cache keys are partitioned by tenant to prevent cross-tenant token reuse.

## Token Lifecycle Management

### Inspecting Token State

```python
token_info = client.auth.token_info()
print(f"Token type: {token_info.type}")        # "oauth" | "api_key" | "bearer"
print(f"Expires at: {token_info.expires_at}")   # datetime or None for API keys
print(f"Scopes: {token_info.scopes}")           # List of granted scopes
print(f"Tenant: {token_info.tenant_id}")        # Current tenant or None
```

### Manual Token Refresh

```python
# Force a token refresh
client.auth.refresh()

# Revoke the current token (OAuth only)
client.auth.revoke()
```

### Token Event Hooks

You can register callbacks for token lifecycle events:

```python
def on_token_refreshed(old_token, new_token):
    print(f"Token refreshed. New expiry: {new_token.expires_at}")

def on_auth_failure(error):
    alert_ops_team(f"Authentication failure: {error}")

client.auth.on("token_refreshed", on_token_refreshed)
client.auth.on("auth_failure", on_auth_failure)
```

## Error Handling for Auth Failures

```python
from acme_platform.exceptions import (
    AuthenticationError,
    TokenExpiredError,
    InsufficientScopesError
)

try:
    client.events.publish(topic="order.created", payload=data)
except TokenExpiredError:
    # Token could not be refreshed automatically
    print("Token expired and could not be refreshed. Check credentials.")
except InsufficientScopesError as e:
    print(f"Missing required scope: {e.required_scope}")
    print(f"Current scopes: {e.granted_scopes}")
except AuthenticationError as e:
    print(f"Auth error {e.status_code}: {e.message}")
```

## Security Best Practices

- Store client secrets in a secrets manager (AWS Secrets Manager, HashiCorp Vault), never in source code or environment variables in plaintext.
- Use the narrowest scopes possible for each service account.
- Rotate credentials on a regular schedule using dual-active secrets.
- Monitor `auth_failure` events to detect compromised credentials.
- In multi-tenant systems, always validate the tenant context matches the authenticated principal.
