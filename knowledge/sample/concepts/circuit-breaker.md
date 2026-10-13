---
id: circuit-breaker
title: Circuit Breaker Pattern
type: Pattern
status: stable
tags: [resilience, patterns, fault-tolerance]
links:
  - target: http-client
    relation: implemented-by
    label: HTTP client implementation
  - target: api-gateway
    relation: used-by
    label: Gateway circuit breaking
  - target: rate-limiting
    relation: complements
    label: Complementary pattern
sources:
  - id: circuit-breaker-doc
    resource: /source_docs/sdk-client/circuit-breaker.md
    title: Circuit Breaker Pattern Guide
generated:
  by: "human:author"
  at: "2026-10-01T00:00:00Z"
---

# Circuit Breaker Pattern

The circuit breaker pattern prevents cascading failures in the Acme Platform by detecting unhealthy downstream dependencies and short-circuiting requests to them. It is implemented in the [HTTP Client](../libraries/http-client.md) library and enforced at the [API Gateway](../services/api-gateway.md) for all inter-service calls.

## How It Works

A circuit breaker wraps outgoing calls and tracks their success and failure rates. When failures exceed a threshold, the breaker "opens" and immediately rejects subsequent requests without attempting the call. After a cooldown period, it enters a "half-open" state and allows a limited number of probe requests through to test recovery.

## State Machine

```
         success
    ┌─────────────┐
    │             v
 CLOSED ──────> OPEN ──── timeout ──── HALF-OPEN
    ^            │                         │
    │            │ failure threshold        │
    │            │ exceeded                 │
    │            └─────────────────────────>│
    │                                       │
    └───── probe succeeds ─────────────────┘
                                            │
              probe fails ─────> OPEN <─────┘
```

### States

| State | Behavior |
|-------|----------|
| **Closed** | Requests pass through normally. Failures are counted. |
| **Open** | Requests are immediately rejected with a fallback response. No calls are made to the downstream. |
| **Half-Open** | A limited number of probe requests are allowed through. If they succeed, the breaker closes. If they fail, it reopens. |

## Configuration

The circuit breaker is configured per downstream dependency. Default values are tuned for typical Acme Platform inter-service latencies.

```python
from acme_sdk.http import HttpClient, CircuitBreakerConfig

client = HttpClient(
    base_url="https://order-service.internal",
    circuit_breaker=CircuitBreakerConfig(
        failure_threshold=5,        # Open after 5 consecutive failures
        success_threshold=3,        # Close after 3 consecutive successes in half-open
        timeout_seconds=30,         # Time to wait before transitioning to half-open
        half_open_max_calls=3,      # Max concurrent calls in half-open state
        failure_rate_threshold=0.5, # Open if failure rate exceeds 50% in window
        window_size_seconds=60,     # Sliding window for failure rate calculation
    ),
)
```

### Configuration Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `failure_threshold` | int | 5 | Consecutive failures before opening |
| `success_threshold` | int | 3 | Consecutive successes to close from half-open |
| `timeout_seconds` | int | 30 | Cooldown before half-open |
| `half_open_max_calls` | int | 3 | Probe calls allowed in half-open |
| `failure_rate_threshold` | float | 0.5 | Failure rate to trigger opening |
| `window_size_seconds` | int | 60 | Sliding window for rate calculation |

## Fallback Strategies

When the circuit is open, the client returns a fallback response instead of propagating the failure.

### Static Fallback

Return a predefined response:

```python
@client.fallback("GET", "/api/recommendations")
def recommendations_fallback(request):
    return {
        "recommendations": [],
        "source": "fallback",
        "message": "Recommendation service temporarily unavailable",
    }
```

### Cached Fallback

Return the last successful response from cache:

```python
from acme_sdk.http import CachedFallback

client = HttpClient(
    base_url="https://catalog-service.internal",
    circuit_breaker=CircuitBreakerConfig(failure_threshold=5),
    fallback=CachedFallback(
        cache_backend="redis",
        ttl_seconds=300,
        stale_ttl_seconds=3600,  # Serve stale cache up to 1 hour
    ),
)
```

### Degraded Response

Return a simplified version of the response:

```python
@client.fallback("GET", "/api/user/{user_id}/profile")
async def profile_fallback(request):
    # Return basic profile from local cache without enrichment
    basic = await local_cache.get(f"user:{request.path_params['user_id']}")
    return {"profile": basic, "enriched": False}
```

## Gateway-Level Circuit Breaking

The [API Gateway](../services/api-gateway.md) applies circuit breakers to all upstream service routes. This provides a platform-wide safety net independent of individual client configurations.

```yaml
# gateway/routes/order-service.yml
upstream:
  name: order-service
  targets:
    - host: order-service.production.svc
      port: 8080
  circuit_breaker:
    failure_threshold: 10
    timeout_seconds: 60
    failure_rate_threshold: 0.3
    window_size_seconds: 120
  fallback:
    status: 503
    body:
      error: "service_unavailable"
      message: "Order service is temporarily unavailable. Please retry."
      retry_after: 60
```

## Monitoring

Circuit breaker state changes emit metrics that feed into the observability stack:

```python
# Metrics emitted automatically by the SDK
acme_circuit_breaker_state{service="order-service", state="open"}     1
acme_circuit_breaker_state{service="order-service", state="closed"}   0
acme_circuit_breaker_transitions_total{service="order-service", from="closed", to="open"} 3
acme_circuit_breaker_rejected_total{service="order-service"} 1247
```

Alerting rules trigger when a breaker stays open beyond a threshold:

```yaml
- alert: CircuitBreakerOpen
  expr: acme_circuit_breaker_state{state="open"} == 1
  for: 5m
  labels:
    severity: warning
  annotations:
    summary: "Circuit breaker open for {{ $labels.service }}"
```

## Relationship with Rate Limiting

The circuit breaker pattern complements [Rate Limiting](../concepts/rate-limiting.md). While rate limiting protects a service from being overwhelmed by inbound traffic, the circuit breaker protects a service from being harmed by unhealthy outbound dependencies. Together they form the resilience layer of the platform.

## Related

- [HTTP Client](../libraries/http-client.md) -- library that implements the circuit breaker
- [API Gateway](../services/api-gateway.md) -- applies circuit breaking at the gateway level
- [Rate Limiting](../concepts/rate-limiting.md) -- complementary pattern for inbound traffic protection
