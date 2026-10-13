# Acme Platform Monitoring Setup Guide

## Overview

The Acme Platform monitoring stack provides full observability across all microservices using a combination of metrics, logs, and traces. The stack consists of Prometheus for metrics collection, Grafana for visualization, the ELK stack for log aggregation, and Jaeger for distributed tracing.

## Prometheus Metrics Collection

Every Acme Platform service exposes a `/metrics` endpoint in Prometheus exposition format. The platform Prometheus instance scrapes all services via Kubernetes service discovery.

### Scrape Configuration

```yaml
# prometheus.yml
global:
  scrape_interval: 15s
  evaluation_interval: 15s

scrape_configs:
  - job_name: 'acme-services'
    kubernetes_sd_configs:
      - role: pod
        namespaces:
          names:
            - production
            - staging
    relabel_configs:
      - source_labels: [__meta_kubernetes_pod_annotation_prometheus_io_scrape]
        action: keep
        regex: true
      - source_labels: [__meta_kubernetes_pod_annotation_prometheus_io_path]
        action: replace
        target_label: __metrics_path__
        regex: (.+)
      - source_labels: [__meta_kubernetes_pod_annotation_prometheus_io_port]
        action: replace
        target_label: __address__
        regex: (.+)
        replacement: $1
```

### Standard Metrics

All services must expose the following metrics using the `prometheus_client` library:

| Metric | Type | Description |
|--------|------|-------------|
| `http_requests_total` | Counter | Total HTTP requests by method, path, and status |
| `http_request_duration_seconds` | Histogram | Request latency distribution |
| `http_requests_in_progress` | Gauge | Currently active requests |
| `db_query_duration_seconds` | Histogram | Database query latency |
| `db_connections_active` | Gauge | Active database connections |
| `cache_hits_total` | Counter | Cache hit count |
| `cache_misses_total` | Counter | Cache miss count |

### Custom Metrics Example (Python)

```python
from prometheus_client import Counter, Histogram, Gauge

REQUEST_COUNT = Counter(
    'http_requests_total',
    'Total HTTP requests',
    ['method', 'endpoint', 'status']
)

REQUEST_LATENCY = Histogram(
    'http_request_duration_seconds',
    'HTTP request latency',
    ['method', 'endpoint'],
    buckets=[0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0]
)

ACTIVE_REQUESTS = Gauge(
    'http_requests_in_progress',
    'Number of active HTTP requests'
)
```

## Grafana Dashboard Setup

The Acme Platform maintains a shared Grafana instance at `https://grafana.acmeplatform.io`. Dashboards are provisioned as code and stored in each service's repository under `monitoring/dashboards/`.

### Standard Service Dashboard

Every service gets a pre-built dashboard from the `acme-service-dashboard` template that includes:

- Request rate (RPS) by endpoint
- Error rate percentage
- Latency percentiles (P50, P95, P99)
- Pod CPU and memory utilization
- Database connection pool status
- Cache hit ratio

### Dashboard JSON Snippet

```json
{
  "panels": [
    {
      "title": "Request Rate",
      "type": "timeseries",
      "targets": [
        {
          "expr": "sum(rate(http_requests_total{service=\"$service\"}[5m])) by (status)",
          "legendFormat": "{{status}}"
        }
      ],
      "fieldConfig": {
        "defaults": {
          "unit": "reqps"
        }
      }
    },
    {
      "title": "P99 Latency",
      "type": "timeseries",
      "targets": [
        {
          "expr": "histogram_quantile(0.99, sum(rate(http_request_duration_seconds_bucket{service=\"$service\"}[5m])) by (le))",
          "legendFormat": "P99"
        }
      ],
      "fieldConfig": {
        "defaults": {
          "unit": "s",
          "thresholds": {
            "steps": [
              {"color": "green", "value": null},
              {"color": "yellow", "value": 0.5},
              {"color": "red", "value": 1.0}
            ]
          }
        }
      }
    }
  ]
}
```

## Alert Rules and Notification Channels

Alerts are defined as Prometheus recording and alerting rules. Critical alerts route to PagerDuty; warnings go to Slack.

### Alert Rules

```yaml
# alert-rules.yml
groups:
  - name: acme-service-alerts
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
          summary: "High P99 latency on {{ $labels.service }}"
          description: "P99 latency is {{ $value }}s."

      - alert: PodCrashLooping
        expr: rate(kube_pod_container_status_restarts_total[15m]) * 60 * 15 > 3
        for: 5m
        labels:
          severity: critical
        annotations:
          summary: "Pod {{ $labels.pod }} is crash looping"

      - alert: DiskSpaceLow
        expr: kubelet_volume_stats_available_bytes / kubelet_volume_stats_capacity_bytes < 0.1
        for: 10m
        labels:
          severity: warning
        annotations:
          summary: "Disk space below 10% on {{ $labels.persistentvolumeclaim }}"
```

### Notification Channels

| Severity | Channel | Response Time |
|----------|---------|---------------|
| Critical | PagerDuty (on-call rotation) | 15 minutes |
| Warning | Slack `#platform-alerts` | 1 hour |
| Info | Slack `#platform-monitoring` | Next business day |

## SLI/SLO Definitions

Each service defines Service Level Indicators (SLIs) and Service Level Objectives (SLOs) in a `slo.yaml` file.

### Standard SLOs

| SLI | Measurement | SLO Target |
|-----|-------------|------------|
| Availability | Percentage of successful requests (non-5xx) | 99.9% |
| Latency | P99 request duration | < 500ms |
| Throughput | Sustained requests per second | Per service capacity plan |
| Error budget | Remaining allowed failures in a 30-day window | Burn rate < 1.0 |

Error budget consumption is tracked on the platform-wide SLO dashboard. When a service exhausts 80% of its error budget, a freeze on non-critical deployments is triggered automatically.

## Log Aggregation with ELK

All services emit structured JSON logs to stdout. Filebeat collects logs from Kubernetes pods and ships them to Elasticsearch.

### Log Format Standard

```json
{
  "timestamp": "2026-10-13T14:30:00.123Z",
  "level": "ERROR",
  "service": "order-service",
  "trace_id": "abc123def456",
  "span_id": "789ghi",
  "message": "Failed to process order",
  "error": "ConnectionRefusedError: Cannot connect to payment-service",
  "context": {
    "order_id": "ORD-98765",
    "user_id": "USR-12345",
    "retry_count": 3
  }
}
```

### Kibana Index Patterns

- `acme-services-*` -- All service logs
- `acme-audit-*` -- Audit trail logs
- `acme-access-*` -- HTTP access logs

Log retention policy: 30 days in hot storage, 90 days in warm, 1 year in cold (S3 archive).

## Distributed Tracing with Jaeger

Jaeger provides end-to-end distributed tracing across the Acme Platform microservices. Traces are propagated via the W3C Trace Context standard using the `traceparent` and `tracestate` HTTP headers.

### Instrumentation

Services use OpenTelemetry SDK for automatic and manual instrumentation:

```python
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.exporter.jaeger.thrift import JaegerExporter
from opentelemetry.sdk.trace.export import BatchSpanProcessor

jaeger_exporter = JaegerExporter(
    agent_host_name="jaeger-agent.observability.svc.cluster.local",
    agent_port=6831,
)

provider = TracerProvider()
provider.add_span_processor(BatchSpanProcessor(jaeger_exporter))
trace.set_tracer_provider(provider)

tracer = trace.get_tracer("order-service")

# Manual span creation
with tracer.start_as_current_span("process_order") as span:
    span.set_attribute("order.id", order_id)
    span.set_attribute("order.total", total_amount)
    result = process_payment(order_id)
```

Sampling rate is set to 1% in production and 100% in staging. The Jaeger UI is available at `https://jaeger.acmeplatform.io`.

## Health Check Endpoints

Every service must implement the following health endpoints:

| Endpoint | Purpose | Expected Response |
|----------|---------|-------------------|
| `/health` | Liveness probe | `{"status": "ok"}` |
| `/ready` | Readiness probe | `{"status": "ready", "checks": {...}}` |
| `/metrics` | Prometheus metrics | Prometheus text format |

The readiness endpoint must verify connectivity to all critical dependencies (database, cache, message broker). If any dependency is unreachable, the endpoint returns HTTP 503 and the pod is removed from the service load balancer.

```yaml
# Kubernetes probe configuration
livenessProbe:
  httpGet:
    path: /health
    port: 8080
  initialDelaySeconds: 10
  periodSeconds: 15
  failureThreshold: 3

readinessProbe:
  httpGet:
    path: /ready
    port: 8080
  initialDelaySeconds: 5
  periodSeconds: 10
  failureThreshold: 2
```
