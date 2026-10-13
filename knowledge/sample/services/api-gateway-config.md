---
id: api-gateway-config
title: API Gateway Configuration
type: System
status: stable
tags: [api, gateway, configuration]
links:
  - target: api-gateway
    relation: configures
    label: Gateway configuration
  - target: auth-service
    relation: references
    label: Auth provider config
  - target: rate-limiting
    relation: implements
    label: Rate limit rules
sources:
  - id: api-gw-config
    resource: /source_docs/api-gateway/configuration.md
    title: API Gateway Configuration Guide
generated:
  by: "human:author"
  at: "2026-10-01T00:00:00Z"
---

# API Gateway Configuration

This document covers the configuration options for the [API Gateway](../services/api-gateway.md). All configuration is managed declaratively through YAML files and pushed to the gateway fleet via the xDS control plane.

## Route Definitions

Routes map incoming request patterns to upstream service clusters. Each route definition includes a match block, a route action, and optional metadata.

```yaml
virtual_hosts:
  - name: acme-api
    domains: ["api.acme-platform.io"]
    routes:
      - match:
          prefix: "/api/v2/auth"
        route:
          cluster: auth-service
          timeout: 3s
        metadata:
          filter_metadata:
            acme.routing:
              service: "auth"
              version: "v2"

      - match:
          prefix: "/api/v2/events"
        route:
          cluster: event-bus-ingress
          timeout: 5s

      - match:
          prefix: "/api/v2/notifications"
        route:
          cluster: notification-engine
          timeout: 10s
```

Routes are evaluated in order; the first match wins. Place more specific routes before general catch-all routes.

## Upstream Clusters

Upstream clusters define the pool of backend service instances that the gateway routes traffic to. Endpoints are discovered via EDS from the Kubernetes service mesh.

```yaml
clusters:
  - name: auth-service
    type: EDS
    connect_timeout: 1s
    lb_policy: ROUND_ROBIN
    eds_cluster_config:
      service_name: auth-service
      eds_config:
        api_config_source:
          api_type: GRPC
          grpc_services:
            - envoy_grpc:
                cluster_name: xds-cluster

  - name: notification-engine
    type: EDS
    connect_timeout: 2s
    lb_policy: LEAST_REQUEST
    eds_cluster_config:
      service_name: notification-engine
```

## TLS Settings

TLS termination is handled at the edge listener. Certificates are sourced from a Secret Discovery Service (SDS) backed by cert-manager.

```yaml
transport_socket:
  name: envoy.transport_sockets.tls
  typed_config:
    "@type": type.googleapis.com/envoy.extensions.transport_sockets.tls.v3.DownstreamTlsContext
    common_tls_context:
      tls_certificate_sds_secret_configs:
        - name: "acme-platform-cert"
          sds_config:
            api_config_source:
              api_type: GRPC
              grpc_services:
                - envoy_grpc:
                    cluster_name: sds-cluster
      tls_params:
        tls_minimum_protocol_version: TLSv1_3
        cipher_suites:
          - "[ECDHE-ECDSA-AES256-GCM-SHA384|ECDHE-RSA-AES256-GCM-SHA384]"
```

Mutual TLS (mTLS) is enforced for all service-to-service communication within the mesh. Client certificates are validated against the platform CA.

## CORS Policies

Cross-Origin Resource Sharing policies are configured per virtual host or per route:

```yaml
cors:
  allow_origin_string_match:
    - prefix: "https://app.acme-platform.io"
    - exact: "https://admin.acme-platform.io"
  allow_methods: "GET, POST, PUT, DELETE, OPTIONS"
  allow_headers: "Authorization, Content-Type, X-Request-ID"
  expose_headers: "X-Request-ID, X-RateLimit-Remaining"
  max_age: "86400"
  allow_credentials: true
```

## Timeout Configuration

Timeouts are set at multiple levels to provide defense in depth:

| Level | Setting | Default | Description |
|---|---|---|---|
| Global | `stream_idle_timeout` | 60s | Max idle time for any stream |
| Route | `timeout` | 15s | Max time for a complete request/response |
| Route | `idle_timeout` | 30s | Max idle time within a route |
| Upstream | `connect_timeout` | 1s | Max time to establish upstream connection |
| Retry | `per_try_timeout` | 5s | Max time per retry attempt |

## Rate Limiting

Rate limit rules are defined per route and enforced by the rate limit service. See [Rate Limiting](../concepts/rate-limiting.md) for the full conceptual overview.

```yaml
rate_limits:
  - actions:
      - request_headers:
          header_name: "x-api-key"
          descriptor_key: "api_key"
    stage: 0
    limit:
      requests_per_unit: 1000
      unit: MINUTE

  - actions:
      - remote_address: {}
    stage: 1
    limit:
      requests_per_unit: 100
      unit: SECOND
```

Rate limit descriptors are sent to a centralized rate limit service backed by Redis, which returns allow/deny decisions to the gateway.

## Auth Provider Configuration

The gateway delegates authentication to the [Auth Service](../services/auth-service.md) via an external authorization filter:

```yaml
http_filters:
  - name: envoy.filters.http.ext_authz
    typed_config:
      "@type": type.googleapis.com/envoy.extensions.filters.http.ext_authz.v3.ExtAuthz
      grpc_service:
        envoy_grpc:
          cluster_name: auth-service
        timeout: 2s
      failure_mode_allow: false
      with_request_body:
        max_request_bytes: 8192
        allow_partial_message: false
```

Setting `failure_mode_allow: false` ensures that if the auth service is unreachable, requests are denied rather than allowed through.

## Environment-Specific Overrides

Configuration values can be overridden per environment using Kustomize overlays:

```
config/
  base/
    gateway.yaml
  overlays/
    dev/
      patches.yaml
    staging/
      patches.yaml
    production/
      patches.yaml
```

Production overrides typically increase connection limits, tighten timeouts, and enable stricter rate limiting compared to development environments.
