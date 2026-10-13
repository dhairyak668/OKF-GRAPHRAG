# Circuit Breaker Guide

## Overview

The Acme Platform SDK includes a circuit breaker implementation that prevents cascading failures when downstream services become unhealthy. Rather than continuing to send requests to a failing service (which wastes resources and increases latency), the circuit breaker short-circuits calls and returns failures immediately, giving the downstream service time to recover.

## Circuit Breaker Pattern

The circuit breaker acts as a proxy between your service and each downstream dependency. It monitors the success and failure rates of outgoing calls and transitions between three states based on configurable thresholds.

## States

### Closed (Normal Operation)

In the **closed** state, all requests pass through to the downstream service normally. The circuit breaker tracks the number of recent failures. When the failure count exceeds the `failure_threshold` within the `failure_window`, the circuit transitions to **open**.

### Open (Failing Fast)

In the **open** state, all requests are rejected immediately with a `CircuitOpenError` without making any network call. This prevents your service from waiting on a downstream that is known to be unhealthy. After the `recovery_timeout` elapses, the circuit transitions to **half-open**.

### Half-Open (Probing)

In the **half-open** state, a limited number of probe requests are allowed through to test whether the downstream service has recovered. If `success_threshold` consecutive probes succeed, the circuit transitions back to **closed**. If any probe fails, the circuit returns to **open** and the recovery timeout resets.

## Configuration

### Global Configuration

```python
from acme_platform import AcmePlatformClient

client = AcmePlatformClient(
    api_key="your-api-key",
    circuit_breaker={
        "failure_threshold": 5,           # Failures before opening
        "failure_window": 60.0,           # Window in seconds for counting failures
        "recovery_timeout": 30.0,         # Seconds in open state before half-open
        "success_threshold": 3,           # Consecutive successes to close from half-open
        "monitored_exceptions": [         # Only these count as circuit failures
            "ConnectionError",
            "TimeoutError"
        ],
        "monitored_status_codes": [502, 503, 504]  # HTTP codes that count as failures
    }
)
```

### Per-Service Configuration

Different downstream services may have different reliability characteristics. You can configure circuit breakers per service:

```python
client = AcmePlatformClient(
    api_key="your-api-key",
    circuit_breaker={
        "default": {
            "failure_threshold": 5,
            "recovery_timeout": 30.0
        },
        "services": {
            "payment-service": {
                "failure_threshold": 3,       # More sensitive for critical path
                "recovery_timeout": 60.0,     # Longer recovery for payment provider
                "success_threshold": 5        # More probes before trusting recovery
            },
            "analytics-service": {
                "failure_threshold": 10,      # More tolerant for non-critical service
                "recovery_timeout": 15.0      # Quick recovery attempts
            }
        }
    }
)
```

### Configuration Parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `failure_threshold` | `int` | `5` | Number of failures within the window to trip the circuit |
| `failure_window` | `float` | `60.0` | Rolling window in seconds for counting failures |
| `recovery_timeout` | `float` | `30.0` | Seconds to wait in open state before allowing probes |
| `success_threshold` | `int` | `3` | Consecutive successful probes to close the circuit |
| `monitored_exceptions` | `list[str]` | `["ConnectionError", "TimeoutError"]` | Exception types that count as circuit failures |
| `monitored_status_codes` | `list[int]` | `[502, 503, 504]` | HTTP status codes that count as circuit failures |
| `exclude_paths` | `list[str]` | `[]` | URL paths excluded from circuit breaker monitoring |

## Fallback Strategies

When the circuit is open, you should provide fallback behavior rather than letting the `CircuitOpenError` propagate to end users.

### Static Fallback

Return a default value when the circuit is open:

```python
from acme_platform.exceptions import CircuitOpenError

def get_order_details(order_id: str) -> dict:
    try:
        response = client.http.get(f"/api/v2/orders/{order_id}", service="order-service")
        return response.json()
    except CircuitOpenError:
        return {
            "order_id": order_id,
            "status": "unknown",
            "message": "Order details temporarily unavailable"
        }
```

### Cache Fallback

Serve stale data from a cache when the circuit is open:

```python
from acme_platform.exceptions import CircuitOpenError

def get_product_catalog() -> list:
    try:
        response = client.http.get("/api/v2/products", service="catalog-service")
        products = response.json()["data"]
        cache.set("product_catalog", products, ttl=3600)
        return products
    except CircuitOpenError:
        cached = cache.get("product_catalog")
        if cached:
            return cached
        raise ServiceUnavailableError("Catalog unavailable and no cached data")
```

### Fallback Service

Route to an alternative service or degraded endpoint:

```python
from acme_platform.exceptions import CircuitOpenError

def get_shipping_estimate(order_id: str) -> dict:
    try:
        response = client.http.get(
            f"/api/v2/shipping/estimate/{order_id}",
            service="shipping-service"
        )
        return response.json()
    except CircuitOpenError:
        # Fall back to a simpler estimation service
        response = client.http.get(
            f"/api/v2/shipping/estimate-basic/{order_id}",
            service="shipping-fallback"
        )
        result = response.json()
        result["approximate"] = True
        return result
```

### Decorator-Based Fallback

The SDK provides a convenience decorator for defining fallbacks:

```python
from acme_platform.resilience import circuit_fallback

@circuit_fallback(service="recommendation-service", fallback=lambda user_id: [])
def get_recommendations(user_id: str) -> list:
    response = client.http.get(
        f"/api/v2/recommendations/{user_id}",
        service="recommendation-service"
    )
    return response.json()["items"]

# If the circuit is open, returns [] instead of raising
recommendations = get_recommendations("USER-123")
```

## Monitoring Circuit State

### Querying State Programmatically

```python
from acme_platform.resilience import CircuitState

state = client.circuit_breaker.get_state("order-service")

print(f"State: {state.status}")             # "closed", "open", or "half_open"
print(f"Failure count: {state.failure_count}")
print(f"Success count: {state.success_count}")
print(f"Last failure: {state.last_failure_at}")
print(f"Opens at: {state.recovery_at}")     # None if closed

# Get all circuit states
all_states = client.circuit_breaker.get_all_states()
for service, state in all_states.items():
    print(f"{service}: {state.status}")
```

### Event Callbacks

Register callbacks to react to circuit state changes:

```python
def on_circuit_open(service: str, failure_count: int):
    alert_ops(f"Circuit opened for {service} after {failure_count} failures")

def on_circuit_close(service: str):
    logger.info(f"Circuit closed for {service}, service recovered")

def on_circuit_half_open(service: str):
    logger.info(f"Circuit half-open for {service}, sending probes")

client.circuit_breaker.on("open", on_circuit_open)
client.circuit_breaker.on("close", on_circuit_close)
client.circuit_breaker.on("half_open", on_circuit_half_open)
```

### Metrics Export

The circuit breaker exposes Prometheus-compatible metrics:

```python
from acme_platform.resilience import circuit_breaker_metrics

# Returns a dict of metric families
metrics = circuit_breaker_metrics(client)

# Example metrics:
# acme_circuit_breaker_state{service="order-service"} 0  (0=closed, 1=open, 2=half_open)
# acme_circuit_breaker_failures_total{service="order-service"} 12
# acme_circuit_breaker_trips_total{service="order-service"} 2
# acme_circuit_breaker_rejected_total{service="order-service"} 47
```

## Integration with HTTP Client

The circuit breaker is integrated into the HTTP client by default. Every request made through `client.http` is routed through the circuit breaker for the target service.

```python
# The circuit breaker is applied automatically
response = client.http.get("/api/v2/orders", service="order-service")

# Bypass the circuit breaker for a specific request (use with caution)
response = client.http.get(
    "/api/v2/health",
    service="order-service",
    bypass_circuit_breaker=True
)

# Disable the circuit breaker entirely (not recommended for production)
client = AcmePlatformClient(
    api_key="your-api-key",
    enable_circuit_breaker=False
)
```

### Manual Circuit Control

In rare cases, you may need to manually control the circuit state:

```python
# Force the circuit open (e.g., during planned maintenance)
client.circuit_breaker.force_open("payment-service")

# Reset the circuit to closed
client.circuit_breaker.reset("payment-service")

# Reset all circuits
client.circuit_breaker.reset_all()
```

## Best Practices

1. **Set appropriate thresholds per service.** Critical-path services (payments, auth) should have lower failure thresholds and longer recovery timeouts. Non-critical services (analytics, recommendations) can be more tolerant.

2. **Always implement fallbacks.** A circuit breaker without a fallback just converts a slow failure into a fast failure. The real value comes from graceful degradation.

3. **Do not circuit-break health checks.** Health check endpoints should bypass the circuit breaker so that monitoring systems get accurate status information even when the circuit is open.

4. **Monitor circuit state transitions.** Circuit open events are early indicators of downstream problems. Wire them into your alerting system.

5. **Tune failure windows carefully.** A window that is too short may trip the circuit on transient blips. A window that is too long may allow sustained degradation before the circuit trips.

6. **Separate read and write circuits.** If your service makes both read and write calls to a downstream, consider using separate circuit breakers so that a write-path failure does not block reads.

7. **Test circuit breaker behavior.** Use `force_open()` in integration tests to verify that your fallback logic works correctly.

8. **Avoid retry storms after recovery.** When a circuit closes, all queued callers may retry simultaneously. Combine the circuit breaker with rate limiting and backoff to smooth recovery traffic.
