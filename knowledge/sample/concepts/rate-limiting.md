---
id: rate-limiting
title: Rate Limiting
type: Pattern
status: stable
tags: [rate-limiting, throttling, api, patterns]
links:
  - target: api-gateway
    relation: implemented-by
    label: Gateway rate limiting
  - target: api-gateway-config
    relation: configured-in
    label: Rate limit config
  - target: circuit-breaker
    relation: complements
    label: Complementary pattern
sources:
  - id: rate-limit-doc
    resource: /source_docs/api-gateway/rate-limiting.md
    title: Rate Limiting Guide
generated:
  by: "human:author"
  at: "2026-10-01T00:00:00Z"
---

# Rate Limiting

Rate limiting controls the volume of requests a client can make to Acme Platform APIs within a given time window. It is enforced at the [API Gateway](../services/api-gateway.md) and configured through the [API Gateway Config](../services/api-gateway-config.md).

## Purpose

Rate limiting serves three goals:

1. **Protect services from overload** -- prevent a single client from consuming disproportionate resources.
2. **Ensure fair access** -- distribute available capacity equitably across clients.
3. **Defend against abuse** -- mitigate denial-of-service and credential-stuffing attacks.

## Algorithms

The platform supports three rate limiting algorithms, each suited to different use cases.

### Token Bucket

The token bucket algorithm allows bursts up to a configured capacity while enforcing a sustained rate. Tokens are added to the bucket at a fixed rate; each request consumes one token.

```
Bucket capacity: 100 tokens
Refill rate: 10 tokens/second

t=0   bucket=100  request -> bucket=99
t=0   bucket=99   request -> bucket=98
...
t=0   bucket=1    request -> bucket=0
t=0   bucket=0    request -> REJECTED (429)
t=0.1 bucket=1    refill   -> bucket=1
t=0.1 bucket=1    request -> bucket=0
```

Configuration:

```yaml
# gateway/rate-limits/default.yml
algorithm: token_bucket
capacity: 100
refill_rate: 10          # tokens per second
refill_interval_ms: 100  # refill every 100ms
```

This is the default algorithm for most API endpoints.

### Sliding Window

The sliding window algorithm counts requests in a rolling time window. It provides smoother rate limiting than fixed windows by avoiding boundary spikes.

```python
# Sliding window implementation concept
def is_allowed(client_id: str, window_seconds: int, max_requests: int) -> bool:
    now = time.time()
    window_start = now - window_seconds

    # Count requests in the current window
    request_count = redis.zcount(f"ratelimit:{client_id}", window_start, now)

    if request_count >= max_requests:
        return False

    # Record this request
    redis.zadd(f"ratelimit:{client_id}", {str(uuid4()): now})
    redis.expire(f"ratelimit:{client_id}", window_seconds)
    return True
```

Configuration:

```yaml
algorithm: sliding_window
window_seconds: 60
max_requests: 100
```

### Fixed Window

The fixed window algorithm divides time into discrete intervals and counts requests within each interval. It is the simplest algorithm but can allow double the rate at window boundaries.

```
Window: 1 minute
Limit: 100 requests

[00:00 - 01:00] 95 requests  -> OK
[01:00 - 02:00] 100 requests -> OK
                 101st        -> REJECTED (429)
```

Configuration:

```yaml
algorithm: fixed_window
window_seconds: 60
max_requests: 100
```

## Rate Limit Tiers

Acme Platform defines rate limit tiers based on API plan:

| Tier | Requests/min | Burst capacity | Algorithm |
|------|-------------|----------------|-----------|
| Free | 60 | 10 | Token bucket |
| Standard | 600 | 100 | Token bucket |
| Professional | 3000 | 500 | Sliding window |
| Enterprise | 15000 | 2000 | Sliding window |

## Configuration

Rate limits are configured in the [API Gateway Config](../services/api-gateway-config.md) and can be set at multiple levels:

### Global Rate Limit

Applies to all requests regardless of client identity:

```yaml
# gateway/config/global-rate-limit.yml
global:
  algorithm: token_bucket
  capacity: 10000
  refill_rate: 1000
```

### Per-Client Rate Limit

Identified by API key or OAuth client ID:

```yaml
# gateway/config/client-rate-limits.yml
clients:
  - client_id: "acme-mobile-app"
    tier: professional
    overrides:
      max_requests: 5000  # Custom limit for mobile app
  - client_id: "partner-integration"
    tier: enterprise
```

### Per-Endpoint Rate Limit

Different endpoints can have different limits:

```yaml
# gateway/config/endpoint-rate-limits.yml
endpoints:
  - path: "/api/v1/auth/login"
    method: POST
    algorithm: fixed_window
    window_seconds: 300
    max_requests: 5
    scope: per_ip  # Rate limit by IP, not API key
  - path: "/api/v1/search"
    method: GET
    algorithm: sliding_window
    window_seconds: 60
    max_requests: 30
```

## Response Headers

The gateway includes standard rate limit headers in every response:

```http
HTTP/1.1 200 OK
X-RateLimit-Limit: 600
X-RateLimit-Remaining: 583
X-RateLimit-Reset: 1696118460
Retry-After: 42
```

| Header | Description |
|--------|-------------|
| `X-RateLimit-Limit` | Maximum requests allowed in the window |
| `X-RateLimit-Remaining` | Requests remaining in the current window |
| `X-RateLimit-Reset` | Unix timestamp when the window resets |
| `Retry-After` | Seconds to wait before retrying (only on 429) |

## Rate Limit Exceeded Response

When a client exceeds its rate limit, the gateway returns HTTP 429:

```json
{
  "error": "rate_limit_exceeded",
  "message": "You have exceeded your rate limit of 600 requests per minute.",
  "limit": 600,
  "remaining": 0,
  "reset_at": "2026-09-15T10:31:00Z",
  "retry_after_seconds": 42
}
```

## Distributed Rate Limiting

Rate limits are enforced across all gateway instances using Redis as a shared counter store. This ensures consistent limiting regardless of which gateway instance handles the request.

```python
# Distributed token bucket using Redis + Lua
RATE_LIMIT_SCRIPT = """
local key = KEYS[1]
local capacity = tonumber(ARGV[1])
local refill_rate = tonumber(ARGV[2])
local now = tonumber(ARGV[3])

local bucket = redis.call('HMGET', key, 'tokens', 'last_refill')
local tokens = tonumber(bucket[1]) or capacity
local last_refill = tonumber(bucket[2]) or now

local elapsed = now - last_refill
local new_tokens = math.min(capacity, tokens + elapsed * refill_rate)

if new_tokens >= 1 then
    redis.call('HMSET', key, 'tokens', new_tokens - 1, 'last_refill', now)
    redis.call('EXPIRE', key, math.ceil(capacity / refill_rate) * 2)
    return 1
else
    return 0
end
"""
```

## Relationship with Circuit Breaker

Rate limiting and the [Circuit Breaker](../concepts/circuit-breaker.md) pattern are complementary resilience mechanisms. Rate limiting protects services from excessive inbound traffic, while circuit breakers protect services from unhealthy outbound dependencies. Both are essential for a robust microservices architecture.

## Related

- [API Gateway](../services/api-gateway.md) -- enforces rate limits at the edge
- [API Gateway Config](../services/api-gateway-config.md) -- stores rate limit configuration
- [Circuit Breaker](../concepts/circuit-breaker.md) -- complementary outbound resilience pattern
