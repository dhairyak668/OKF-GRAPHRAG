---
id: sdk-auth
title: SDK Authentication Module
type: Tool
status: stable
tags: [sdk, auth, tokens]
links:
  - target: sdk-client
    relation: part-of
    label: SDK module
  - target: auth-service
    relation: integrates-with
    label: Auth integration
  - target: auth-tokens
    relation: manages
    label: Token handling
sources:
  - id: sdk-auth-doc
    resource: /source_docs/sdk-client/authentication.md
    title: SDK Authentication Guide
generated:
  by: "human:author"
  at: "2026-10-01T00:00:00Z"
---

# SDK Authentication Module

The authentication module manages credential lifecycle within the Acme Platform SDK. It handles token acquisition, automatic refresh, secure caching, and service account authentication flows.

## Token Acquisition

When the [SDK Client](../libraries/sdk-client.md) is initialized with credentials, the auth module negotiates an access token from the [Auth Service](../services/auth-service.md) using the OAuth2 client credentials grant:

```python
from acme_sdk import AcmeClient

client = AcmeClient(
    base_url="https://api.acmeplatform.io",
    client_id="svc-pipeline-runner",
    client_secret="secret-value",
)

# Token is acquired automatically on first API call
# You can also force acquisition explicitly:
token = client.auth.acquire_token()
print(f"Token expires at: {token.expires_at}")
```

## Automatic Token Refresh

The module monitors token expiry and refreshes transparently before requests fail. A token is refreshed when less than 20% of its lifetime remains:

```python
# Refresh behavior is automatic — no code changes needed.
# To customize the refresh threshold:
client.auth.configure(refresh_threshold=0.3)  # refresh at 30% remaining
```

## Token Caching

Tokens are cached in memory by default. For long-running services or multi-process deployments, use the persistent cache backed by an encrypted file store:

```python
from acme_sdk.auth import FileTokenCache

client = AcmeClient(
    base_url="https://api.acmeplatform.io",
    client_id="svc-pipeline-runner",
    client_secret="secret-value",
    token_cache=FileTokenCache(path="/var/run/acme/token-cache.enc"),
)
```

The `FileTokenCache` encrypts tokens at rest using AES-256-GCM with a key derived from the client secret.

## Service Account Authentication

For automated workloads such as CI/CD pipelines and batch jobs, use service account credentials:

```python
from acme_sdk.auth import ServiceAccountCredentials

creds = ServiceAccountCredentials.from_file(
    "/etc/acme/service-account.json"
)

client = AcmeClient(
    base_url="https://api.acmeplatform.io",
    credentials=creds,
)
```

Service accounts are managed through the [Auth Service](../services/auth-service.md) admin console and support scoped permissions tied to specific platform resources.

## Token Management

The module works closely with the platform's [Token Management](../services/auth-tokens.md) subsystem for token introspection and revocation:

```python
# Inspect the current token
info = client.auth.introspect()
print(f"Scopes: {info.scopes}")
print(f"Issued at: {info.issued_at}")

# Revoke all tokens for this client
client.auth.revoke_all()
```

## Related Documentation

- [SDK Client](../libraries/sdk-client.md) — parent SDK package
- [Auth Service](../services/auth-service.md) — identity provider and token issuer
- [Token Management](../services/auth-tokens.md) — platform token lifecycle
