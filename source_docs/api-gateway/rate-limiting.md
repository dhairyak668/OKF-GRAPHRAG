# API Gateway Rate Limiting

## Overview

The Acme Platform API Gateway uses a **token bucket** algorithm to enforce rate limits on incoming requests. Rate limits are evaluated per route and can be scoped by client identity, tenant, or API key. A shared **Redis** backend ensures consistent enforcement across all gateway replicas.

## Token Bucket Algorithm

Each rate limit descriptor maintains a token bucket with two parameters:

- **Bucket capacity** (`max_tokens`): the maximum number of tokens (burst allowance)
- **Fill rate** (`tokens_per_second`): how many tokens are added per second

When a request arrives, the gateway attempts to consume one token from the bucket. If the bucket is empty, the request is rejected with HTTP `429 Too Many Requests`.

```
  Tokens added at fill_rate
         │
         ▼
  ┌──────────────┐
  │              │  ◄── max_tokens (capacity)
  │  ■ ■ ■ ■ ■  │
  │  ■ ■ ■ ■    │  ◄── current token count
  │              │
  └──────┬───────┘
         │
         ▼
  Request consumes 1 token
  (rejected if 0 tokens remain)
```

This approach allows short bursts of traffic up to the bucket capacity while enforcing a sustained rate equal to the fill rate.

## Configuration

Rate limits are defined in `config/middleware/rate-limiting.yaml` and can be overridden per route.

### Global Configuration

```yaml
# middleware/rate-limiting.yaml
rate_limiting:
  enabled: true
  failure_mode: allow  # "allow" or "deny" when Redis is unreachable
  timeout: 5ms         # max time to wait for Redis response

  redis:
    cluster_endpoints:
      - redis-rate-limit-0.infra:6379
      - redis-rate-limit-1.infra:6379
      - redis-rate-limit-2.infra:6379
    pool_size: 25
    read_timeout: 3ms
    write_timeout: 3ms
    tls_enabled: true

  default_limits:
    requests_per_second: 100
    burst: 200
```

### Per-Route Limits

Routes can define custom rate limit descriptors that override the defaults:

```yaml
# In a route definition file
routes:
  - name: users-create
    match:
      prefix: /api/v2/users
      methods: [POST]
    middleware:
      rate_limiting:
        descriptor: user-write
        requests_per_second: 50
        burst: 75
        scope: api_key  # bucket per API key

  - name: search-query
    match:
      prefix: /api/v2/search
      methods: [GET]
    middleware:
      rate_limiting:
        descriptor: search-read
        requests_per_second: 200
        burst: 500
        scope: tenant  # bucket per tenant

  - name: bulk-export
    match:
      exact: /api/v2/users/export
      methods: [POST]
    middleware:
      rate_limiting:
        descriptor: bulk-export
        requests_per_second: 2
        burst: 5
        scope: api_key
```

### Scope Options

The `scope` field determines how buckets are partitioned:

| Scope | Bucket Key | Use Case |
|---|---|---|
| `global` | Route descriptor only | Shared limit across all clients |
| `api_key` | Descriptor + API key | Per-client fairness |
| `tenant` | Descriptor + tenant ID | Multi-tenant isolation |
| `ip` | Descriptor + client IP | Public/unauthenticated endpoints |
| `user` | Descriptor + user ID | Per-user limits for authenticated routes |

## Response Headers

The gateway injects standard rate limit headers into every response so clients can track their usage:

| Header | Description | Example |
|---|---|---|
| `X-RateLimit-Limit` | Bucket capacity (burst) | `200` |
| `X-RateLimit-Remaining` | Tokens remaining in the bucket | `142` |
| `X-RateLimit-Reset` | Unix timestamp when the bucket fully refills | `1727453400` |
| `Retry-After` | Seconds to wait (only on 429 responses) | `2` |

### Example 200 Response Headers

```http
HTTP/2 200 OK
X-RateLimit-Limit: 200
X-RateLimit-Remaining: 142
X-RateLimit-Reset: 1727453400
Content-Type: application/json
```

### Example 429 Response

```http
HTTP/2 429 Too Many Requests
X-RateLimit-Limit: 200
X-RateLimit-Remaining: 0
X-RateLimit-Reset: 1727453400
Retry-After: 2
Content-Type: application/json

{
  "error": "rate_limit_exceeded",
  "message": "Request rate limit exceeded. Try again in 2 seconds.",
  "descriptor": "user-write",
  "limit": 200,
  "retry_after": 2
}
```

## Redis Backend

Rate limit state is stored in Redis using a sliding window counter combined with token bucket metadata. Each bucket key has a TTL equal to twice the refill period to prevent stale data accumulation.

### Key Format

```
ratelimit:{descriptor}:{scope_value}
```

Example keys:
```
ratelimit:user-write:apikey_abc123
ratelimit:search-read:tenant_42
ratelimit:bulk-export:apikey_xyz789
```

### Redis Data Structure

Each key stores a hash with three fields:

| Field | Type | Description |
|---|---|---|
| `tokens` | float | Current token count |
| `last_refill` | int | Unix timestamp (ms) of last refill calculation |
| `version` | int | Optimistic concurrency control version |

The refill is computed lazily on each request:

```
elapsed = now - last_refill
new_tokens = min(max_tokens, tokens + elapsed * tokens_per_second)
```

### Failure Mode

When Redis is unreachable, the gateway falls back to the configured `failure_mode`:

- **`allow`** (default): Requests pass through without rate limiting. This prevents Redis outages from causing a full service outage. A metric `gateway_ratelimit_redis_failures_total` is incremented.
- **`deny`**: All requests are rejected with 503. Use this only for endpoints where over-admission is worse than downtime.

### Redis Cluster Requirements

| Parameter | Recommended Value |
|---|---|
| Nodes | 3 (minimum) with 1 replica each |
| Memory | 512MB per node (handles ~1M active buckets) |
| Max latency (p99) | < 5ms |
| Persistence | RDB snapshots every 60s (AOF not required) |
| Eviction policy | `volatile-lru` |

## Monitoring

### Key Metrics

| Metric | Type | Description |
|---|---|---|
| `gateway_ratelimit_requests_total` | Counter | Requests evaluated, by descriptor and decision |
| `gateway_ratelimit_rejected_total` | Counter | Requests rejected (429), by descriptor |
| `gateway_ratelimit_redis_latency` | Histogram | Redis round-trip time in milliseconds |
| `gateway_ratelimit_redis_failures_total` | Counter | Redis connection/timeout failures |
| `gateway_ratelimit_bucket_utilization` | Gauge | Current token usage as fraction of capacity |

### Alerting Rules

```yaml
# Prometheus alerting rules
groups:
  - name: rate-limiting
    rules:
      - alert: HighRateLimitRejections
        expr: |
          rate(gateway_ratelimit_rejected_total[5m]) > 100
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "High rate of 429 responses"
          description: "{{ $labels.descriptor }} is rejecting {{ $value }} req/s"

      - alert: RedisRateLimitUnavailable
        expr: |
          gateway_ratelimit_redis_failures_total > 0
          and rate(gateway_ratelimit_redis_failures_total[1m]) > 5
        for: 2m
        labels:
          severity: critical
        annotations:
          summary: "Rate limit Redis backend is failing"
```

## Tuning Guidelines

1. **Set burst to 1.5-2x the sustained rate.** This absorbs natural traffic spikes without false positives.
2. **Use `api_key` scope for partner integrations** so one noisy client does not starve others.
3. **Use `tenant` scope for multi-tenant SaaS routes** to ensure fair resource allocation.
4. **Monitor `bucket_utilization` over 7 days** before tightening limits. Aim for p95 utilization below 0.7.
5. **Start with `failure_mode: allow`** and switch to `deny` only for security-critical endpoints (e.g., login, password reset).
6. **Test limits in staging** using a load generator before rolling out to production:

```bash
# Simulate 500 req/s against the users endpoint
hey -z 30s -q 500 -H "Authorization: Bearer $TOKEN" \
  https://staging-gateway.acmeplatform.io/api/v2/users
```
