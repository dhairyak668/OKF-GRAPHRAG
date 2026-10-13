---
id: monitoring-setup
title: Monitoring and Alerting Setup
type: Process
status: stable
tags: [monitoring, alerting, observability, metrics]
links:
  - target: api-gateway
    relation: monitors
    label: Gateway monitoring
  - target: logger
    relation: aggregates
    label: Log aggregation
  - target: deployment-process
    relation: supports
    label: Deploy monitoring
sources:
  - id: monitoring-doc
    resource: /source_docs/deploy-tools/monitoring.md
    title: Monitoring Setup Guide
generated:
  by: "human:author"
  at: "2026-10-01T00:00:00Z"
---

# Monitoring and Alerting Setup

The Acme Platform observability stack provides metrics collection, log aggregation, distributed tracing, and alerting. It is built on Prometheus for metrics, Grafana for visualization, and Loki for log aggregation via the [Structured Logger](../libraries/logger.md).

## Architecture Overview

```
Services --> Prometheus (scrape) --> Grafana (dashboards)
    |                                    |
    +--> Loki (logs) ------------------->+
    |                                    |
    +--> Tempo (traces) --------------->+
                                         |
                               Alertmanager --> PagerDuty / Slack
```

Every service exposes a `/metrics` endpoint in Prometheus exposition format. The Prometheus server scrapes these endpoints at a configurable interval.

## Metrics Collection

### Prometheus Configuration

```yaml
# prometheus/prometheus.yml
global:
  scrape_interval: 15s
  evaluation_interval: 15s

scrape_configs:
  - job_name: "acme-services"
    kubernetes_sd_configs:
      - role: pod
    relabel_configs:
      - source_labels: [__meta_kubernetes_pod_annotation_prometheus_io_scrape]
        action: keep
        regex: true
      - source_labels: [__meta_kubernetes_pod_annotation_prometheus_io_port]
        action: replace
        target_label: __address__
        regex: (.+)
        replacement: $1

  - job_name: "api-gateway"
    static_configs:
      - targets: ["api-gateway.production.svc:8080"]
    metrics_path: /metrics
    scrape_interval: 10s
```

The [API Gateway](../services/api-gateway.md) is scraped at a shorter interval (10s) due to its critical role in request routing.

### Standard Metrics

All Acme Platform services expose these standard metrics:

| Metric | Type | Description |
|--------|------|-------------|
| `http_requests_total` | Counter | Total HTTP requests by method, path, status |
| `http_request_duration_seconds` | Histogram | Request latency distribution |
| `http_request_size_bytes` | Histogram | Request payload sizes |
| `http_response_size_bytes` | Histogram | Response payload sizes |
| `process_cpu_seconds_total` | Counter | CPU time consumed |
| `process_resident_memory_bytes` | Gauge | Resident memory usage |
| `go_goroutines` | Gauge | Number of active goroutines |

### Custom Metrics

Services register custom metrics using the Acme SDK:

```python
from acme_sdk.metrics import Counter, Histogram

orders_processed = Counter(
    "orders_processed_total",
    "Total orders processed",
    labels=["status", "region"],
)

processing_time = Histogram(
    "order_processing_duration_seconds",
    "Order processing time",
    buckets=[0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0],
)
```

## Alerting Rules

Alert rules are defined in Prometheus and routed through Alertmanager. Alerts are categorized by severity.

```yaml
# prometheus/alert-rules.yml
groups:
  - name: service-health
    rules:
      - alert: HighErrorRate
        expr: |
          sum(rate(http_requests_total{status=~"5.."}[5m])) by (service)
          /
          sum(rate(http_requests_total[5m])) by (service)
          > 0.05
        for: 5m
        labels:
          severity: critical
        annotations:
          summary: "High error rate on {{ $labels.service }}"
          description: "Error rate is {{ $value | humanizePercentage }} over the last 5 minutes."

      - alert: HighLatency
        expr: |
          histogram_quantile(0.99, sum(rate(http_request_duration_seconds_bucket[5m])) by (le, service))
          > 2.0
        for: 10m
        labels:
          severity: warning
        annotations:
          summary: "High p99 latency on {{ $labels.service }}"

      - alert: PodCrashLooping
        expr: rate(kube_pod_container_status_restarts_total[15m]) > 0
        for: 5m
        labels:
          severity: critical
        annotations:
          summary: "Pod {{ $labels.pod }} is crash-looping"
```

### Alertmanager Routing

```yaml
# alertmanager/alertmanager.yml
route:
  receiver: default-slack
  group_by: [alertname, service]
  group_wait: 30s
  group_interval: 5m
  repeat_interval: 4h
  routes:
    - match:
        severity: critical
      receiver: pagerduty-critical
    - match:
        severity: warning
      receiver: slack-warnings

receivers:
  - name: pagerduty-critical
    pagerduty_configs:
      - service_key: "$PAGERDUTY_KEY"
  - name: slack-warnings
    slack_configs:
      - channel: "#acme-alerts"
        send_resolved: true
  - name: default-slack
    slack_configs:
      - channel: "#acme-monitoring"
```

## Grafana Dashboards

The platform ships with pre-built dashboards for each service layer.

### Service Overview Dashboard

Panels include:
- Request rate (QPS) by service
- Error rate percentage by service
- p50 / p95 / p99 latency
- Active connections
- Pod CPU and memory utilization

### Deployment Dashboard

Tracks deployment health in conjunction with the [Deployment Process](../workflows/deployment-process.md):
- Rollout status per service
- Version distribution across pods
- Restart counts during rollout
- Error rate delta before/after deployment

## SLO Definitions

Service Level Objectives are tracked as Prometheus recording rules and visualized in Grafana.

| Service | SLI | Target | Window |
|---------|-----|--------|--------|
| API Gateway | Availability (non-5xx) | 99.95% | 30 days |
| API Gateway | p99 latency | < 500ms | 30 days |
| Order Service | Processing success rate | 99.9% | 30 days |
| Event Bus | Message delivery latency | < 100ms p95 | 7 days |

```yaml
# prometheus/slo-rules.yml
groups:
  - name: slo-api-gateway
    rules:
      - record: slo:api_gateway:availability:ratio_rate30d
        expr: |
          1 - (
            sum(increase(http_requests_total{service="api-gateway",status=~"5.."}[30d]))
            /
            sum(increase(http_requests_total{service="api-gateway"}[30d]))
          )
```

## Log Aggregation

The [Structured Logger](../libraries/logger.md) outputs JSON logs that are collected by Promtail and shipped to Loki. Logs are queryable in Grafana using LogQL.

```logql
{service="api-gateway"} |= "error" | json | status >= 500
```

## Related

- [API Gateway](../services/api-gateway.md) -- primary service monitored by this setup
- [Structured Logger](../libraries/logger.md) -- produces structured logs aggregated by this stack
- [Deployment Process](../workflows/deployment-process.md) -- deployment health monitored by dashboards and alerts
