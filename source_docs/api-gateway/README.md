# Acme Platform API Gateway

## Overview

The Acme Platform API Gateway is the unified entry point for all client traffic into the Acme microservices mesh. Built on **Envoy Proxy v1.28**, it handles routing, load balancing, authentication, TLS termination, and request transformation for over 40 backend services across three deployment regions.

All external HTTP/gRPC traffic flows through the gateway before reaching any downstream service. Internal service-to-service communication bypasses the gateway and uses the sidecar mesh directly.

```
                          Architecture Overview

  ┌─────────────┐     ┌──────────────────────────────────────────────────┐
  │   Clients   │     │              API Gateway (Envoy)                 │
  │             │     │                                                  │
  │  Web App    │────▶│  ┌──────────┐  ┌────────────┐  ┌─────────────┐  │
  │  Mobile     │     │  │   TLS    │─▶│ Middleware  │─▶│   Router    │  │
  │  3rd Party  │     │  │ Termina- │  │   Chain    │  │             │  │
  │  IoT Edge   │     │  │  tion    │  │            │  │             │  │
  └─────────────┘     │  └──────────┘  └────────────┘  └──────┬──────┘  │
                      │                                       │         │
                      └───────────────────────────────────────┼─────────┘
                                                              │
                    ┌─────────────────────────────────────────┼──────┐
                    │           Upstream Services              │      │
                    │                                          ▼      │
                    │  ┌──────────┐ ┌──────────┐ ┌──────────────────┐│
                    │  │ User     │ │ Billing  │ │ Notification     ││
                    │  │ Service  │ │ Service  │ │ Service          ││
                    │  └──────────┘ └──────────┘ └──────────────────┘│
                    │  ┌──────────┐ ┌──────────┐ ┌──────────────────┐│
                    │  │ Inventory│ │ Analytics│ │ Search           ││
                    │  │ Service  │ │ Service  │ │ Service          ││
                    │  └──────────┘ └──────────┘ └──────────────────┘│
                    └────────────────────────────────────────────────┘
```

## Key Features

| Feature | Description |
|---|---|
| **Routing** | Path-based and header-based routing to 40+ backend services |
| **Load Balancing** | Weighted round-robin, least-request, and ring-hash strategies |
| **TLS Termination** | mTLS at the edge with automatic cert rotation via cert-manager |
| **Rate Limiting** | Token bucket per route with Redis-backed distributed counters |
| **Authentication** | JWT validation, API key lookup, and OAuth2 token introspection |
| **Request Transform** | Header injection, path rewriting, body transformation |
| **Health Checks** | Active and passive health checking with circuit breaker integration |
| **Observability** | Distributed tracing (OpenTelemetry), Prometheus metrics, structured logging |

## Routing Rules

Routes are defined in YAML configuration files and loaded at startup. The gateway supports prefix matching, exact matching, and regex matching on paths, with optional header-based routing conditions.

### Route Priority

Routes are evaluated in the following order:

1. Exact path matches (highest priority)
2. Regex path matches (ordered by specificity)
3. Prefix path matches (longest prefix wins)
4. Default fallback route (returns 404)

### Example Route Configuration

```yaml
routes:
  - name: user-service
    match:
      prefix: /api/v2/users
      headers:
        - name: x-api-version
          exact_match: "2"
    route:
      cluster: user-service-v2
      timeout: 5s
      retry_policy:
        retry_on: "5xx,reset,connect-failure"
        num_retries: 3
        per_try_timeout: 2s

  - name: billing-service
    match:
      prefix: /api/v1/billing
    route:
      cluster: billing-service
      timeout: 10s
      hash_policy:
        - header:
            header_name: x-tenant-id

  - name: analytics-ingest
    match:
      prefix: /api/v1/events
      methods: [POST, PUT]
    route:
      cluster: analytics-ingest
      timeout: 30s
      max_request_bytes: 10485760  # 10MB
```

## Load Balancing

The gateway supports multiple load balancing strategies, configurable per upstream cluster.

### Strategies

**Weighted Round Robin** (default) distributes traffic proportionally across healthy endpoints. Used for most stateless services.

```yaml
clusters:
  - name: user-service-v2
    lb_policy: ROUND_ROBIN
    endpoints:
      - address: user-svc-1.internal:8080
        weight: 3
      - address: user-svc-2.internal:8080
        weight: 3
      - address: user-svc-3.internal:8080
        weight: 1  # canary instance
```

**Least Request** sends traffic to the endpoint with the fewest active requests. Recommended for services with variable response times.

```yaml
clusters:
  - name: search-service
    lb_policy: LEAST_REQUEST
    least_request_lb_config:
      choice_count: 5  # power-of-N-choices
```

**Ring Hash** provides consistent hashing for session affinity. Used by the caching layer and tenant-isolated services.

```yaml
clusters:
  - name: billing-service
    lb_policy: RING_HASH
    ring_hash_lb_config:
      minimum_ring_size: 1024
      hash_function: XX_HASH
```

## Health Checks

The gateway performs both **active** and **passive** health checking on upstream services.

### Active Health Checks

Active checks send periodic probe requests to each upstream endpoint.

```yaml
health_checks:
  - timeout: 3s
    interval: 10s
    unhealthy_threshold: 3
    healthy_threshold: 2
    http_health_check:
      path: /healthz
      expected_statuses:
        - start: 200
          end: 299
```

### Passive Health Checks (Outlier Detection)

Outlier detection automatically ejects endpoints that exhibit abnormal behavior.

```yaml
outlier_detection:
  consecutive_5xx: 5
  interval: 10s
  base_ejection_time: 30s
  max_ejection_percent: 50
  enforcing_consecutive_5xx: 100
  success_rate_minimum_hosts: 3
  success_rate_stdev_factor: 1900
```

When an endpoint is ejected, the gateway logs the event and increments the `envoy_cluster_outlier_detection_ejections_total` Prometheus counter.

### Circuit Breaker Integration

Each cluster has circuit breaker thresholds to prevent cascading failures:

```yaml
circuit_breakers:
  thresholds:
    - priority: DEFAULT
      max_connections: 1024
      max_pending_requests: 512
      max_requests: 2048
      max_retries: 10
    - priority: HIGH
      max_connections: 2048
      max_pending_requests: 1024
      max_requests: 4096
      max_retries: 20
```

## Middleware Chain

Requests pass through an ordered middleware chain before reaching the router. The chain is applied in the order listed and cannot be reordered at runtime.

```
  Request ──▶ TLS ──▶ CORS ──▶ RateLimit ──▶ AuthN ──▶ AuthZ ──▶ Transform ──▶ Router
```

### Middleware Descriptions

| Order | Middleware | Description |
|---|---|---|
| 1 | **CORS** | Cross-origin resource sharing headers and preflight handling |
| 2 | **Rate Limiting** | Token bucket enforcement per route; see [rate-limiting.md](rate-limiting.md) |
| 3 | **Authentication** | JWT validation or API key lookup against the auth service |
| 4 | **Authorization** | Policy evaluation via OPA (Open Policy Agent) sidecar |
| 5 | **Request Transform** | Header injection, path rewrite, body mapping |
| 6 | **Request Logging** | Structured access log with trace context |

### Bypassing Middleware

Specific routes can skip middleware stages using the `skip_middleware` annotation:

```yaml
routes:
  - name: public-health
    match:
      exact: /healthz
    route:
      cluster: passthrough
    metadata:
      skip_middleware:
        - authentication
        - authorization
        - rate_limiting
```

## TLS Termination

The gateway terminates TLS at the edge. All external connections require TLS 1.2 or higher.

### Certificate Management

Certificates are provisioned automatically via **cert-manager** with Let's Encrypt (staging and production issuers). Rotation occurs 30 days before expiry.

```yaml
tls_context:
  common_tls_context:
    tls_certificates:
      - certificate_chain:
          filename: /etc/envoy/tls/tls.crt
        private_key:
          filename: /etc/envoy/tls/tls.key
    tls_params:
      tls_minimum_protocol_version: TLSv1_2
      cipher_suites:
        - ECDHE-ECDSA-AES256-GCM-SHA384
        - ECDHE-RSA-AES256-GCM-SHA384
        - ECDHE-ECDSA-AES128-GCM-SHA256
```

### mTLS for Internal Traffic

Backend connections from the gateway to upstream services use mTLS with SPIFFE-issued identities:

```yaml
upstream_tls_context:
  common_tls_context:
    tls_certificate_sds_secret_configs:
      - name: "spiffe://acme.internal/gateway"
    validation_context_sds_secret_config:
      name: "spiffe://acme.internal/trust-bundle"
```

## Request/Response Transformation

The gateway can modify requests and responses in flight using Lua filters or the built-in header manipulation API.

### Header Injection

```yaml
request_headers_to_add:
  - header:
      key: x-request-id
      value: "%REQ(x-request-id)%"
  - header:
      key: x-forwarded-proto
      value: https
  - header:
      key: x-gateway-region
      value: us-east-1

response_headers_to_add:
  - header:
      key: x-response-time
      value: "%DURATION%"
  - header:
      key: strict-transport-security
      value: "max-age=31536000; includeSubDomains"
```

### Path Rewriting

```yaml
routes:
  - name: legacy-redirect
    match:
      prefix: /api/v1/accounts
    route:
      cluster: user-service-v2
      prefix_rewrite: /api/v2/users
```

### Lua-Based Body Transformation

For complex transformations, inline Lua scripts can modify request or response bodies:

```lua
function envoy_on_request(request_handle)
  local body = request_handle:body():getBytes(0, request_handle:body():length())
  local decoded = json.decode(body)

  -- Inject tenant context from JWT claims
  decoded["tenant_id"] = request_handle:headers():get("x-tenant-id")
  decoded["request_ts"] = os.time()

  request_handle:body():setBytes(json.encode(decoded))
end
```

## Observability

### Metrics

The gateway exposes Prometheus metrics on port `9901` at `/stats/prometheus`. Key metrics include:

- `envoy_http_downstream_rq_total` -- total requests by response code
- `envoy_cluster_upstream_rq_time` -- upstream latency histogram
- `envoy_cluster_lb_healthy_panic` -- load balancer panic mode activations
- `envoy_http_downstream_cx_active` -- active connections

### Distributed Tracing

All requests are tagged with OpenTelemetry trace context. The gateway propagates `traceparent` and `tracestate` headers and exports spans to the collector at `otel-collector.observability:4317`.

### Access Logging

Structured JSON access logs are emitted to stdout and collected by Fluentd:

```json
{
  "timestamp": "2026-09-15T14:32:01.234Z",
  "method": "POST",
  "path": "/api/v2/users/123/orders",
  "response_code": 201,
  "duration_ms": 47,
  "upstream_cluster": "user-service-v2",
  "trace_id": "abc123def456",
  "user_agent": "AcmeMobile/3.2.1",
  "client_ip": "203.0.113.42"
}
```

## Deployment

The gateway runs as a Kubernetes `Deployment` with 3 replicas (minimum) behind a `LoadBalancer` service. It is deployed via Helm chart `acme-gateway` in the `infra` namespace.

```bash
# Deploy or upgrade the gateway
helm upgrade --install acme-gateway ./charts/acme-gateway \
  --namespace infra \
  --values values-production.yaml \
  --set image.tag=v1.28.2-acme.4

# Verify rollout
kubectl rollout status deployment/acme-gateway -n infra
```

### Resource Requirements

| Resource | Request | Limit |
|---|---|---|
| CPU | 500m | 2000m |
| Memory | 256Mi | 1Gi |
| Ephemeral Storage | 100Mi | 500Mi |

## Related Documentation

- [Configuration Guide](configuration.md) -- detailed YAML config reference
- [Rate Limiting](rate-limiting.md) -- rate limiting setup and tuning
- [Runbook: Gateway Incidents](../runbooks/gateway-incidents.md) -- operational procedures
- [ADR-017: Gateway Selection](../adrs/017-gateway-selection.md) -- why we chose Envoy
