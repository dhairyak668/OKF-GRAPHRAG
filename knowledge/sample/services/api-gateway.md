---
id: api-gateway
title: API Gateway
description: Central entry point for all client requests, handling routing, rate limiting, authentication delegation, and request transformation.
type: System
status: stable
tags: [api, gateway, routing, infrastructure]
links:
  - target: auth-service
    relation: delegates-to
    label: Authentication delegation
  - target: event-bus
    relation: publishes-to
    label: Request event publishing
  - target: notification-engine
    relation: triggers
    label: Alert triggering
sources:
  - id: api-gateway-readme
    resource: /source_docs/api-gateway/README.md
    title: API Gateway README
generated:
  by: "human:author"
  at: "2026-10-01T00:00:00Z"
---

# API Gateway

The API Gateway is the single entry point for all client requests into the Acme Platform. It handles routing, authentication delegation, rate limiting, request transformation, and observability for every inbound API call. Built on [Envoy Proxy](https://www.envoyproxy.io/), the gateway provides high-performance L7 traffic management with sub-millisecond routing overhead.

## Overview

All external traffic enters the Acme Platform through the API Gateway. The gateway is responsible for:

- **Routing** inbound requests to the correct upstream service based on path, headers, and query parameters.
- **Delegating authentication** to the [Auth Service](../services/auth-service.md) via a gRPC external authorization filter.
- **Publishing request events** to the [Event Bus](../services/event-bus.md) for audit logging and analytics.
- **Triggering alerts** through the [Notification Engine](../services/notification-engine.md) when anomalous traffic patterns are detected.
- **Enforcing rate limits** on a per-client, per-route, and global basis.
- **Transforming requests and responses** to normalize payloads between client versions and backend services.

The gateway runs as a fleet of stateless Envoy instances behind an L4 load balancer (AWS NLB) and scales horizontally based on connection count and CPU utilization.

## Architecture

The gateway deployment consists of three layers:

1. **Edge Layer** -- TLS termination, DDoS mitigation, and IP allowlisting.
2. **Routing Layer** -- Path-based routing, header matching, and weighted traffic splitting for canary deployments.
3. **Filter Layer** -- Authentication, rate limiting, request transformation, and observability filters applied in a defined order.

```
Client -> NLB -> Envoy (Edge) -> Envoy (Routing) -> Envoy (Filters) -> Upstream Service
```

Each layer is implemented as an Envoy listener with a specific filter chain. Filters are configured via xDS and pushed dynamically through a control plane.

## Routing Rules

Routes are defined declaratively in YAML and pushed to the gateway through the control plane. Each route specifies a match condition, an upstream cluster, and optional per-route filter overrides.

```yaml
routes:
  - match:
      prefix: "/api/v2/users"
      headers:
        - name: "x-api-version"
          exact_match: "2"
    route:
      cluster: user-service-v2
      timeout: 5s
      retry_policy:
        retry_on: "5xx,reset,connect-failure"
        num_retries: 3

  - match:
      prefix: "/api/v1/notifications"
    route:
      cluster: notification-engine
      timeout: 10s
      idle_timeout: 30s
```

Route priority is determined by specificity: exact matches take precedence over prefix matches, and longer prefixes take precedence over shorter ones.

## Load Balancing

The gateway uses a combination of load balancing strategies depending on the upstream cluster:

| Strategy | Use Case | Configuration |
|---|---|---|
| Round Robin | Stateless services | Default for most clusters |
| Least Connections | Long-lived connections | WebSocket endpoints |
| Ring Hash | Session-sticky workloads | Cache-layer routing |
| Random | Low-latency preference | Health-check fallback |

Upstream clusters are discovered via EDS (Endpoint Discovery Service), which integrates with the platform's Kubernetes service mesh.

## Health Checks

The gateway performs both active and passive health checks on all upstream services.

**Active health checks** send periodic HTTP requests to each upstream endpoint:

```yaml
health_checks:
  - timeout: 2s
    interval: 10s
    unhealthy_threshold: 3
    healthy_threshold: 2
    http_health_check:
      path: "/healthz"
      expected_statuses:
        - start: 200
          end: 299
```

**Passive health checks** (outlier detection) monitor real traffic and eject endpoints that exhibit elevated error rates:

```yaml
outlier_detection:
  consecutive_5xx: 5
  interval: 10s
  base_ejection_time: 30s
  max_ejection_percent: 50
```

Ejected endpoints are returned to the rotation after the ejection period, with an exponential backoff for repeated failures.

## Circuit Breaker Integration

Circuit breakers protect upstream services from cascading failures. Each cluster has configurable thresholds:

```yaml
circuit_breakers:
  thresholds:
    - priority: DEFAULT
      max_connections: 1024
      max_pending_requests: 512
      max_requests: 2048
      max_retries: 3
    - priority: HIGH
      max_connections: 2048
      max_pending_requests: 1024
      max_requests: 4096
      max_retries: 5
```

When a circuit breaker trips, the gateway returns a `503 Service Unavailable` response with a `Retry-After` header and publishes a circuit-breaker event to the [Event Bus](../services/event-bus.md).

## Request Transformation

The gateway supports request and response transformation through Lua filters and header manipulation:

```yaml
request_headers_to_add:
  - header:
      key: "x-request-id"
      value: "%REQ(x-request-id)%"
  - header:
      key: "x-forwarded-proto"
      value: "https"

request_headers_to_remove:
  - "x-internal-debug"
```

For more complex transformations, the gateway uses a Lua filter that can modify request bodies, merge headers, and rewrite paths. This is commonly used for API version migration where clients send v1 payloads that need to be translated to v2 format.

## Metrics and Observability

The gateway exposes a comprehensive set of metrics via Prometheus:

- **Request metrics**: `envoy_http_downstream_rq_total`, `envoy_http_downstream_rq_xx` (by status class)
- **Latency histograms**: `envoy_http_downstream_rq_time` (p50, p90, p99)
- **Connection metrics**: `envoy_http_downstream_cx_active`, `envoy_http_downstream_cx_total`
- **Upstream metrics**: `envoy_cluster_upstream_rq_total`, `envoy_cluster_upstream_rq_time`

All requests are tagged with a distributed trace ID (`x-request-id`) that propagates through the entire service mesh, enabling end-to-end request tracing in Jaeger.

Example query to check gateway error rate:

```bash
curl -s http://gateway-admin:9901/stats/prometheus | \
  grep 'envoy_http_downstream_rq_xx{envoy_response_code_class="5"}'
```

Access logs are structured as JSON and shipped to the centralized logging stack:

```json
{
  "timestamp": "2026-09-15T14:23:01.234Z",
  "method": "POST",
  "path": "/api/v2/notifications/send",
  "response_code": 200,
  "duration_ms": 42,
  "upstream_cluster": "notification-engine",
  "request_id": "abc-123-def-456",
  "client_ip": "10.0.1.55"
}
```

## Deployment

The gateway is deployed as a Kubernetes DaemonSet on dedicated gateway nodes. Rolling updates are performed with a max-unavailable of 1 to ensure zero-downtime deployments. Configuration changes are pushed via xDS without requiring a pod restart.

For more details on gateway configuration, see [API Gateway Configuration](../services/api-gateway-config.md).
