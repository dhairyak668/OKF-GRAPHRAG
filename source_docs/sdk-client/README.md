# Acme Platform SDK Client

## Overview

The **Acme Platform SDK** is the official Python client library for interacting with the Acme Platform suite of microservices. It provides a unified, Pythonic interface to the platform's APIs including authentication, event publishing, notifications, HTTP communication, and service discovery.

The SDK handles cross-cutting concerns such as token management, retry logic, circuit breaking, structured logging, and request correlation so that service developers can focus on business logic rather than infrastructure plumbing.

## Installation

Install the SDK from PyPI:

```bash
pip install acme-platform-sdk
```

For async support, install with the optional `async` extra:

```bash
pip install acme-platform-sdk[async]
```

To include development and testing utilities:

```bash
pip install acme-platform-sdk[dev]
```

## Quick Start

```python
from acme_platform import AcmePlatformClient

# Initialize with an API key
client = AcmePlatformClient(
    api_key="your-api-key",
    environment="production",
    region="us-east-1"
)

# Publish an event
client.events.publish(
    topic="order.created",
    payload={"order_id": "ORD-12345", "amount": 99.95},
    schema_version="1.2.0"
)

# Send a notification
client.notifications.send(
    channel="email",
    recipient="user@example.com",
    template="order_confirmation",
    context={"order_id": "ORD-12345"}
)

# Query a downstream service
response = client.http.get("/api/v2/orders/ORD-12345")
print(response.json())
```

## Client Initialization

### API Key Authentication

```python
client = AcmePlatformClient(api_key="your-api-key")
```

### OAuth Client Credentials

```python
client = AcmePlatformClient(
    client_id="your-client-id",
    client_secret="your-client-secret",
    token_url="https://auth.acmeplatform.io/oauth/token"
)
```

### User Token (Bearer)

```python
client = AcmePlatformClient(bearer_token="eyJhbGciOi...")
```

See [authentication.md](authentication.md) for full details on token lifecycle management, refresh behavior, and multi-tenant patterns.

## Core Modules

| Module | Description |
|---|---|
| `client.auth` | Token acquisition, refresh, caching, and service account management |
| `client.events` | Event publishing, schema validation, batch operations |
| `client.notifications` | Multi-channel notification delivery (email, SMS, push, webhook) |
| `client.http` | Low-level HTTP client with connection pooling, retries, and circuit breaking |
| `client.logging` | Structured logging with correlation ID propagation |
| `client.discovery` | Service registry lookup and health checking |

## Error Handling

All SDK errors inherit from `AcmePlatformError`:

```python
from acme_platform.exceptions import (
    AcmePlatformError,
    AuthenticationError,
    RateLimitError,
    ServiceUnavailableError,
    ValidationError,
    CircuitOpenError
)

try:
    client.events.publish(topic="order.created", payload=data)
except RateLimitError as e:
    print(f"Rate limited. Retry after {e.retry_after} seconds.")
except CircuitOpenError as e:
    print(f"Circuit open for {e.service}. Fallback in effect.")
except AuthenticationError:
    print("Token expired or invalid. Re-authenticate.")
except AcmePlatformError as e:
    print(f"Platform error: {e.status_code} - {e.message}")
```

## Pagination Helpers

List endpoints return paginated results. The SDK provides automatic pagination:

```python
# Iterate through all pages transparently
for order in client.http.paginate("/api/v2/orders", params={"status": "active"}):
    print(order["id"])

# Or fetch a single page
page = client.http.get("/api/v2/orders", params={"page": 1, "page_size": 50})
print(page.json()["data"])
print(page.json()["pagination"]["total_pages"])
```

## Async Support

Every synchronous method has an async counterpart accessible through `AcmeAsyncPlatformClient`:

```python
import asyncio
from acme_platform import AcmeAsyncPlatformClient

async def main():
    client = AcmeAsyncPlatformClient(api_key="your-api-key")
    
    # Async event publishing
    await client.events.publish(topic="order.shipped", payload={"order_id": "ORD-12345"})
    
    # Concurrent requests
    tasks = [
        client.http.get(f"/api/v2/orders/{oid}")
        for oid in ["ORD-001", "ORD-002", "ORD-003"]
    ]
    responses = await asyncio.gather(*tasks)
    
    await client.close()

asyncio.run(main())
```

## Configuration Options

The `AcmePlatformClient` accepts the following configuration:

| Parameter | Type | Default | Description |
|---|---|---|---|
| `api_key` | `str` | `None` | API key for authentication |
| `client_id` | `str` | `None` | OAuth client ID |
| `client_secret` | `str` | `None` | OAuth client secret |
| `environment` | `str` | `"production"` | Target environment (`production`, `staging`, `development`) |
| `region` | `str` | `"us-east-1"` | Deployment region |
| `base_url` | `str` | Auto-resolved | Override the base URL for all requests |
| `timeout` | `float` | `30.0` | Default request timeout in seconds |
| `max_retries` | `int` | `3` | Maximum retry attempts for transient failures |
| `enable_circuit_breaker` | `bool` | `True` | Enable circuit breaker for downstream calls |
| `log_level` | `str` | `"INFO"` | SDK log level |
| `correlation_id` | `str` | Auto-generated | Override the correlation ID for request tracing |

Configuration can also be loaded from environment variables prefixed with `ACME_`:

```bash
export ACME_API_KEY="your-api-key"
export ACME_ENVIRONMENT="staging"
export ACME_REGION="eu-west-1"
export ACME_TIMEOUT="60"
```

## Version Compatibility

| SDK Version | Python | Platform API | Status |
|---|---|---|---|
| 3.x | 3.10+ | v2, v3 | Current, actively maintained |
| 2.x | 3.8+ | v1, v2 | Maintenance only, security patches |
| 1.x | 3.6+ | v1 | End of life, no longer supported |

## Further Reading

- [Authentication Guide](authentication.md)
- [Events Guide](events.md)
- [HTTP Client Guide](http-client.md)
- [Structured Logging Guide](logging.md)
- [Circuit Breaker Guide](circuit-breaker.md)
- [Acme Platform API Reference](https://docs.acmeplatform.io/api)
- [Changelog](https://github.com/acme-platform/sdk-python/blob/main/CHANGELOG.md)
