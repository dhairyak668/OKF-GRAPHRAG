# API Gateway Configuration Guide

## Overview

The Acme Platform API Gateway is configured through a set of YAML files that are merged at startup. Configuration supports environment-specific overrides, secret injection from Vault, and hot reload without downtime.

## Configuration File Structure

```
config/
├── base.yaml              # Shared defaults for all environments
├── routes/
│   ├── user-service.yaml
│   ├── billing-service.yaml
│   ├── analytics-service.yaml
│   ├── search-service.yaml
│   └── notification-service.yaml
├── middleware/
│   ├── cors.yaml
│   ├── rate-limiting.yaml
│   ├── auth.yaml
│   └── transform.yaml
└── overrides/
    ├── development.yaml
    ├── staging.yaml
    └── production.yaml
```

Files are merged in order: `base.yaml` first, then route files, then middleware configs, then the environment-specific override. Later values win for scalar fields; arrays are replaced entirely (not appended).

## Base Configuration

The `base.yaml` file defines listener addresses, admin endpoints, and cluster defaults.

```yaml
# base.yaml
gateway:
  listeners:
    - name: public
      address: 0.0.0.0
      port: 8443
      protocol: HTTPS
    - name: internal
      address: 0.0.0.0
      port: 8080
      protocol: HTTP

  admin:
    address: 127.0.0.1
    port: 9901
    access_log_path: /dev/null

  defaults:
    cluster:
      connect_timeout: 5s
      lb_policy: ROUND_ROBIN
      health_check_interval: 10s
      circuit_breaker_max_connections: 1024
    route:
      timeout: 15s
      idle_timeout: 300s
      retry_policy:
        retry_on: "5xx,reset"
        num_retries: 2
        per_try_timeout: 5s
```

## Route Definitions

Each route file defines one or more routes for a single upstream service. Route files are placed in `config/routes/` and automatically discovered at startup.

```yaml
# routes/user-service.yaml
service: user-service
cluster:
  name: user-service-v2
  endpoints:
    - address: user-svc.services:8080
  lb_policy: ROUND_ROBIN
  health_check:
    path: /healthz

routes:
  - name: users-list
    match:
      prefix: /api/v2/users
      methods: [GET]
    timeout: 5s

  - name: users-create
    match:
      prefix: /api/v2/users
      methods: [POST]
    timeout: 10s
    middleware:
      rate_limiting:
        descriptor: user-write
        requests_per_second: 50

  - name: users-detail
    match:
      regex: /api/v2/users/[0-9]+
      methods: [GET, PATCH, DELETE]
    timeout: 5s

  - name: users-bulk-export
    match:
      exact: /api/v2/users/export
      methods: [POST]
    timeout: 120s
    middleware:
      rate_limiting:
        descriptor: bulk-export
        requests_per_second: 2
```

## Middleware Ordering

Middleware is applied in a fixed order. You cannot reorder the chain, but you can disable individual stages per route.

| Order | Middleware | Config File | Required |
|---|---|---|---|
| 1 | CORS | `middleware/cors.yaml` | Yes |
| 2 | Rate Limiting | `middleware/rate-limiting.yaml` | No |
| 3 | Authentication | `middleware/auth.yaml` | No |
| 4 | Authorization | `middleware/auth.yaml` | No |
| 5 | Request Transform | `middleware/transform.yaml` | No |

### Disabling Middleware Per Route

Add a `skip_middleware` list to any route definition:

```yaml
routes:
  - name: public-status
    match:
      exact: /status
    route:
      cluster: passthrough
    skip_middleware:
      - authentication
      - authorization
      - rate_limiting
```

### CORS Configuration

```yaml
# middleware/cors.yaml
cors:
  allow_origins:
    - exact: "https://app.acmeplatform.io"
    - regex: "https://.*\\.preview\\.acmeplatform\\.io"
  allow_methods: [GET, POST, PUT, PATCH, DELETE, OPTIONS]
  allow_headers:
    - Authorization
    - Content-Type
    - X-Request-ID
    - X-Tenant-ID
  expose_headers:
    - X-RateLimit-Limit
    - X-RateLimit-Remaining
    - X-RateLimit-Reset
  max_age: 86400
  allow_credentials: true
```

## Environment-Specific Overrides

Override files in `config/overrides/` let you tune behavior per environment without duplicating the entire config.

```yaml
# overrides/production.yaml
gateway:
  defaults:
    cluster:
      circuit_breaker_max_connections: 4096
      health_check_interval: 5s
    route:
      retry_policy:
        num_retries: 3

  tls:
    cert_path: /etc/envoy/tls/tls.crt
    key_path: /etc/envoy/tls/tls.key
    min_version: TLSv1_2

  rate_limiting:
    global_rps: 10000
    redis:
      cluster_endpoints:
        - redis-cluster.infra:6379
      pool_size: 50
      read_timeout: 5ms
```

```yaml
# overrides/development.yaml
gateway:
  defaults:
    cluster:
      circuit_breaker_max_connections: 128
      health_check_interval: 30s
    route:
      timeout: 30s
      retry_policy:
        num_retries: 0

  tls:
    enabled: false  # plain HTTP in dev

  rate_limiting:
    enabled: false

  logging:
    level: debug
    access_log_format: text
```

## Secret Injection

Secrets are injected from HashiCorp Vault at startup via the `vault-agent` init container. Reference secrets in config using the `vault://` prefix:

```yaml
auth:
  jwt:
    signing_key: "vault://secret/data/gateway/jwt-signing-key#value"
  api_keys:
    lookup_endpoint: "https://auth.internal:8443/v1/keys/validate"
    service_token: "vault://secret/data/gateway/auth-service-token#value"
```

The gateway process reads the rendered files from `/vault/secrets/` after the init container completes.

## Hot Reload

The gateway supports configuration reload without dropping active connections. When a config change is detected, Envoy performs a hot restart using its drain/parent-child process model.

### Triggering a Reload

**Via ConfigMap update (recommended):**

```bash
# Update the ConfigMap
kubectl create configmap gateway-config \
  --from-file=config/ \
  --namespace infra \
  --dry-run=client -o yaml | kubectl apply -f -

# Envoy watches the mounted volume and reloads within 15 seconds
```

**Via admin API (manual):**

```bash
# POST to the admin endpoint inside the pod
kubectl exec -n infra deployment/acme-gateway -- \
  curl -X POST http://127.0.0.1:9901/config_reload
```

### Reload Behavior

- Active connections are drained over a 60-second window
- New connections immediately use the updated config
- If the new config is invalid, the reload is rejected and the previous config remains active
- Reload events are logged and emit the `envoy_server_hot_restart_epoch` metric

### Validation

Always validate configuration before deploying:

```bash
# Local validation
acme-gateway validate --config-dir ./config --env production

# CI pipeline validation (runs in Docker)
docker run --rm -v $(pwd)/config:/config acme/gateway:latest \
  envoy --mode validate -c /config/rendered.yaml
```

## Debugging Configuration

To inspect the active configuration on a running gateway:

```bash
# Dump the full merged config
kubectl exec -n infra deployment/acme-gateway -- \
  curl -s http://127.0.0.1:9901/config_dump | jq .

# Show only route configuration
kubectl exec -n infra deployment/acme-gateway -- \
  curl -s http://127.0.0.1:9901/config_dump?resource=dynamic_route_configs | jq .

# List active clusters and their health
kubectl exec -n infra deployment/acme-gateway -- \
  curl -s http://127.0.0.1:9901/clusters | head -50
```
