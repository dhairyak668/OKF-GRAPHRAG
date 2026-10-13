---
id: http-client
title: HTTP Client Library
type: Tool
status: stable
tags: [http, client, retry, resilience]
links:
  - target: api-gateway
    relation: communicates-with
    label: Gateway communication
  - target: circuit-breaker
    relation: implements
    label: Circuit breaker pattern
  - target: sdk-client
    relation: used-by
    label: SDK HTTP layer
sources:
  - id: http-client-doc
    resource: /source_docs/sdk-client/http-client.md
    title: HTTP Client Guide
generated:
  by: "human:author"
  at: "2026-10-01T00:00:00Z"
---

# HTTP Client Library

The Acme Platform HTTP client is a resilient HTTP layer used internally by the [SDK Client](../libraries/sdk-client.md) and available as a standalone library for service-to-service communication through the [API Gateway](../services/api-gateway.md). It provides automatic retries, circuit breaking, connection pooling, and comprehensive timeout management.

## Installation

```bash
pip install acme-http-client
```

## Basic Usage

```python
from acme_http import HttpClient

http = HttpClient(base_url="https://api.acmeplatform.io")

response = http.get("/v1/projects")
print(response.status_code, response.json())
```

## Retry with Exponential Backoff

Failed requests are retried automatically with exponential backoff and jitter. Only idempotent methods (`GET`, `PUT`, `DELETE`) and specific status codes are retried by default:

```python
from acme_http import HttpClient, RetryConfig

http = HttpClient(
    base_url="https://api.acmeplatform.io",
    retry=RetryConfig(
        max_retries=3,
        backoff_factor=0.5,       # 0.5s, 1s, 2s
        retryable_status=[429, 502, 503, 504],
        retry_on_timeout=True,
    ),
)
```

| Parameter           | Type        | Default              | Description                       |
|---------------------|-------------|----------------------|-----------------------------------|
| `max_retries`       | `int`       | `3`                  | Maximum retry attempts            |
| `backoff_factor`    | `float`     | `0.5`                | Base delay multiplier in seconds  |
| `retryable_status`  | `list[int]` | `[429, 502, 503, 504]` | Status codes that trigger retry |
| `retry_on_timeout`  | `bool`      | `True`               | Retry on connection timeout       |

## Circuit Breaker

The library implements the [Circuit Breaker](../concepts/circuit-breaker.md) pattern to prevent cascading failures when an upstream service is unhealthy:

```python
from acme_http import HttpClient, CircuitBreakerConfig

http = HttpClient(
    base_url="https://api.acmeplatform.io",
    circuit_breaker=CircuitBreakerConfig(
        failure_threshold=5,     # open after 5 consecutive failures
        recovery_timeout=30,     # try half-open after 30 seconds
        success_threshold=2,     # close after 2 successes in half-open
    ),
)
```

Circuit states:

- **Closed** -- requests flow normally; failures are counted.
- **Open** -- requests fail immediately with `CircuitOpenError`; no network calls are made.
- **Half-Open** -- a limited number of probe requests are allowed through to test recovery.

```python
from acme_http.exceptions import CircuitOpenError

try:
    response = http.get("/v1/health")
except CircuitOpenError as e:
    print(f"Circuit open for {e.upstream}. Recovery in {e.retry_after}s.")
```

## Connection Pooling

The client maintains a connection pool to reduce TLS handshake overhead and improve throughput:

```python
http = HttpClient(
    base_url="https://api.acmeplatform.io",
    pool_connections=20,   # max connections per host
    pool_maxsize=100,      # total pool size
    keep_alive=True,
)
```

Connection pools are thread-safe and can be shared across multiple calling threads within a service.

## Timeout Management

Configure granular timeouts for different phases of the request lifecycle:

```python
from acme_http import HttpClient, TimeoutConfig

http = HttpClient(
    base_url="https://api.acmeplatform.io",
    timeout=TimeoutConfig(
        connect=5,      # seconds to establish connection
        read=30,        # seconds to wait for response body
        total=60,       # overall request deadline
    ),
)
```

If the `total` deadline is reached, the request is cancelled regardless of which phase it is in. This prevents indefinite hangs when an upstream stalls after accepting the connection.

## Request Interceptors

Add custom logic to every outbound request and inbound response:

```python
from acme_http import HttpClient, Interceptor

class AuthInterceptor(Interceptor):
    def before_request(self, request):
        request.headers["Authorization"] = f"Bearer {self.get_token()}"
        return request

    def after_response(self, response):
        if response.status_code == 401:
            self.refresh_token()
        return response

http = HttpClient(
    base_url="https://api.acmeplatform.io",
    interceptors=[AuthInterceptor()],
)
```

## Related Documentation

- [API Gateway](../services/api-gateway.md) -- request routing and load balancing
- [Circuit Breaker](../concepts/circuit-breaker.md) -- resilience pattern details
- [SDK Client](../libraries/sdk-client.md) -- high-level SDK that wraps this library
