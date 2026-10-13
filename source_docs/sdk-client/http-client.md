# HTTP Client Guide

## Overview

The Acme Platform SDK includes a production-grade HTTP client built on top of `httpx`. It provides connection pooling, automatic retries with exponential backoff, circuit breaker integration, timeout management, and request/response interceptors. The HTTP client is used internally by all SDK modules and is also available directly for calling downstream services.

## HTTP Client Configuration

### Basic Usage

```python
from acme_platform import AcmePlatformClient

client = AcmePlatformClient(api_key="your-api-key")

# GET request
response = client.http.get("/api/v2/orders/ORD-12345")
order = response.json()

# POST request
response = client.http.post(
    "/api/v2/orders",
    json={"customer_id": "CUST-6789", "items": [{"sku": "WIDGET-A", "quantity": 2}]}
)

# PUT request
response = client.http.put(
    "/api/v2/orders/ORD-12345",
    json={"status": "confirmed"}
)

# DELETE request
response = client.http.delete("/api/v2/orders/ORD-12345")
```

### Targeting a Specific Service

When multiple services are registered in the platform, specify the target service:

```python
response = client.http.get(
    "/api/v2/inventory",
    service="inventory-service"
)
```

The SDK resolves the service URL through the Acme Service Registry and applies service-specific circuit breaker settings.

## Connection Pooling

The HTTP client maintains a persistent connection pool to reduce latency from TCP handshake and TLS negotiation overhead.

```python
client = AcmePlatformClient(
    api_key="your-api-key",
    http_config={
        "max_connections": 100,           # Total connections across all hosts
        "max_connections_per_host": 20,   # Connections per individual host
        "keepalive_expiry": 30.0          # Seconds before idle connections close
    }
)
```

Default pool settings:

| Parameter | Default | Description |
|---|---|---|
| `max_connections` | 100 | Maximum total connections in the pool |
| `max_connections_per_host` | 20 | Maximum connections to a single host |
| `keepalive_expiry` | 30.0 | Seconds before an idle connection is closed |

Connection pooling is managed per `AcmePlatformClient` instance. If you create multiple client instances, each maintains its own pool. For efficient resource usage, reuse a single client instance across your application.

## Retry with Exponential Backoff

The SDK retries failed requests that are safe to retry (network errors, 429, 502, 503, 504 status codes). Retries use exponential backoff with jitter to avoid thundering herd effects.

```python
client = AcmePlatformClient(
    api_key="your-api-key",
    http_config={
        "max_retries": 3,                 # Maximum retry attempts
        "retry_backoff_base": 0.5,        # Base delay in seconds
        "retry_backoff_max": 30.0,        # Maximum delay cap in seconds
        "retry_on_status": [429, 502, 503, 504],  # Status codes to retry
        "retry_on_exceptions": [          # Exception types to retry
            "ConnectionError",
            "TimeoutError"
        ]
    }
)
```

### Backoff Calculation

The delay before retry `n` is calculated as:

```
delay = min(retry_backoff_base * (2 ** n) + random_jitter, retry_backoff_max)
```

For the default settings (`base=0.5`, `max=30.0`):
- Retry 1: ~0.5 - 1.0 seconds
- Retry 2: ~1.0 - 2.0 seconds
- Retry 3: ~2.0 - 4.0 seconds

### Retry-After Header

When a `429 Too Many Requests` response includes a `Retry-After` header, the SDK respects the server's requested delay instead of using the calculated backoff.

### Disabling Retries for Specific Requests

```python
response = client.http.post(
    "/api/v2/payments/charge",
    json={"amount": 99.95},
    retries=0  # No retries for non-idempotent operations
)
```

## Circuit Breaker Integration

The HTTP client integrates with the SDK's circuit breaker to prevent cascading failures. See the [Circuit Breaker Guide](circuit-breaker.md) for a detailed explanation of the pattern.

```python
client = AcmePlatformClient(
    api_key="your-api-key",
    circuit_breaker={
        "failure_threshold": 5,          # Failures before opening
        "recovery_timeout": 30.0,        # Seconds before trying half-open
        "success_threshold": 3,          # Successes in half-open to close
        "monitored_exceptions": [
            "ConnectionError",
            "TimeoutError"
        ]
    }
)
```

When the circuit is open, requests fail immediately with `CircuitOpenError` without hitting the network:

```python
from acme_platform.exceptions import CircuitOpenError

try:
    response = client.http.get("/api/v2/orders", service="order-service")
except CircuitOpenError as e:
    print(f"Circuit open for {e.service}. Opens at: {e.recovery_at}")
    # Use fallback logic
```

## Timeout Configuration

Timeouts can be set globally and overridden per request:

```python
client = AcmePlatformClient(
    api_key="your-api-key",
    http_config={
        "timeout": 30.0,                 # Total request timeout
        "connect_timeout": 5.0,          # TCP connection timeout
        "read_timeout": 25.0,            # Time to wait for response body
        "write_timeout": 10.0            # Time to send request body
    }
)

# Override for a specific request
response = client.http.get(
    "/api/v2/reports/annual",
    timeout=120.0  # Large report, needs more time
)
```

## Request/Response Interceptors

Interceptors allow you to modify requests before they are sent and responses after they are received. This is useful for adding custom headers, logging, metrics collection, or response transformation.

### Request Interceptor

```python
from acme_platform.http import RequestInterceptor

class AuditInterceptor(RequestInterceptor):
    def before_request(self, request):
        request.headers["X-Audit-User"] = get_current_user()
        request.headers["X-Request-Timestamp"] = datetime.utcnow().isoformat()
        return request

client.http.add_interceptor(AuditInterceptor())
```

### Response Interceptor

```python
from acme_platform.http import ResponseInterceptor

class MetricsInterceptor(ResponseInterceptor):
    def after_response(self, request, response):
        duration = response.elapsed.total_seconds()
        metrics.histogram(
            "http_request_duration_seconds",
            duration,
            tags={
                "method": request.method,
                "service": request.headers.get("X-Acme-Target-Service", "unknown"),
                "status": response.status_code
            }
        )
        return response

client.http.add_interceptor(MetricsInterceptor())
```

### Built-in Interceptors

The SDK ships with several built-in interceptors:

| Interceptor | Description |
|---|---|
| `AuthInterceptor` | Injects authentication headers (enabled by default) |
| `CorrelationInterceptor` | Propagates `X-Correlation-Id` (enabled by default) |
| `CompressionInterceptor` | Compresses request bodies with gzip |
| `CacheInterceptor` | Caches GET responses with configurable TTL |
| `RateLimitInterceptor` | Client-side rate limiting per service |

```python
from acme_platform.http.interceptors import CompressionInterceptor, CacheInterceptor

client.http.add_interceptor(CompressionInterceptor(min_size=1024))
client.http.add_interceptor(CacheInterceptor(ttl=60, max_entries=1000))
```

## Custom Headers and Auth Injection

### Global Headers

```python
client = AcmePlatformClient(
    api_key="your-api-key",
    default_headers={
        "X-Client-Version": "3.2.1",
        "X-Deployment-Id": "deploy-abc123",
        "Accept-Language": "en-US"
    }
)
```

### Per-Request Headers

```python
response = client.http.get(
    "/api/v2/orders",
    headers={
        "X-Request-Priority": "high",
        "If-None-Match": '"etag-abc"'
    }
)
```

### Custom Authentication for External Services

When calling services outside the Acme Platform that require different authentication:

```python
from acme_platform.http import ExternalAuth

response = client.http.get(
    "https://external-api.example.com/data",
    auth=ExternalAuth(
        scheme="Bearer",
        token="external-service-token"
    ),
    bypass_circuit_breaker=True  # External services have separate failure domains
)
```

## Resource Cleanup

Always close the client when done to release connection pool resources:

```python
# Explicit close
client.close()

# Or use as a context manager
with AcmePlatformClient(api_key="your-api-key") as client:
    response = client.http.get("/api/v2/orders")
```
