# Structured Logging Guide

## Overview

The Acme Platform SDK includes a structured logging module that produces JSON-formatted log output with automatic correlation ID propagation. This enables centralized log aggregation, cross-service request tracing, and efficient querying in systems like Elasticsearch, Datadog, or Splunk.

## Logger Setup and Configuration

### Basic Setup

```python
from acme_platform.logging import AcmeLogger

logger = AcmeLogger(
    service_name="order-processor",
    environment="production",
    log_level="INFO"
)

logger.info("Order processing started", order_id="ORD-12345")
```

### Integration with the SDK Client

When you initialize an `AcmePlatformClient`, a logger is created automatically. You can access and configure it:

```python
from acme_platform import AcmePlatformClient

client = AcmePlatformClient(
    api_key="your-api-key",
    log_level="DEBUG"
)

# Access the SDK's internal logger
client.logger.info("Client initialized", region=client.region)

# Or create a child logger for your application code
app_logger = client.logger.child(module="order_handler")
app_logger.info("Processing order", order_id="ORD-12345")
```

### Configuration via Environment Variables

```bash
export ACME_LOG_LEVEL="DEBUG"
export ACME_LOG_FORMAT="json"          # "json" or "text"
export ACME_LOG_OUTPUT="stdout"        # "stdout", "stderr", or a file path
export ACME_LOG_INCLUDE_CALLER="true"  # Include file/line in log entries
```

## Log Levels

The SDK uses standard log levels. Choose the appropriate level based on the situation:

| Level | When to Use |
|---|---|
| `DEBUG` | Detailed diagnostic information. Includes request/response bodies, cache hits, token refresh events. Use in development only. |
| `INFO` | Normal operational events. Service started, request completed, event published. The default level for production. |
| `WARNING` | Unexpected but recoverable conditions. Retry triggered, circuit breaker tripped, deprecated API used. |
| `ERROR` | Failures that affect the current operation but not the service. Failed request after retries, schema validation failure. |
| `CRITICAL` | Failures that affect the entire service. Cannot connect to auth provider, out of memory, configuration missing. |

```python
logger.debug("Cache lookup", key="token:svc-order", hit=True)
logger.info("Order created", order_id="ORD-12345", amount=149.99)
logger.warning("Retry triggered", attempt=2, max_retries=3, url="/api/v2/orders")
logger.error("Event publish failed", topic="order.created", error="timeout")
logger.critical("Auth provider unreachable", url="https://auth.acmeplatform.io")
```

## Structured JSON Output Format

In JSON mode (the default for production), each log entry is emitted as a single-line JSON object:

```json
{
  "timestamp": "2025-09-15T14:32:07.123Z",
  "level": "INFO",
  "service": "order-processor",
  "environment": "production",
  "message": "Order created",
  "correlation_id": "req-a1b2c3d4",
  "trace_id": "trace-e5f6g7h8",
  "span_id": "span-i9j0k1l2",
  "module": "order_handler",
  "order_id": "ORD-12345",
  "amount": 149.99,
  "duration_ms": 42
}
```

### Customizing the Output Schema

```python
from acme_platform.logging import AcmeLogger, JsonFormatter

formatter = JsonFormatter(
    extra_fields={
        "app_version": "3.2.1",
        "cluster": "us-east-1a"
    },
    timestamp_format="epoch_ms",    # "iso8601" (default) or "epoch_ms"
    exclude_fields=["span_id"]      # Omit fields you don't need
)

logger = AcmeLogger(
    service_name="order-processor",
    formatter=formatter
)
```

## Correlation ID Propagation

The SDK automatically generates and propagates correlation IDs across service boundaries. When the SDK receives an inbound request with an `X-Correlation-Id` header, it carries that ID through all outgoing HTTP calls, event publishes, and log entries.

```python
# Set correlation ID from an inbound request (e.g., in a Flask middleware)
from acme_platform.logging import set_correlation_id

@app.before_request
def inject_correlation_id():
    correlation_id = request.headers.get("X-Correlation-Id")
    if correlation_id:
        set_correlation_id(correlation_id)
    # The SDK will include this in all subsequent logs and outgoing requests
```

### Manual Correlation ID Management

```python
from acme_platform.logging import get_correlation_id, set_correlation_id

# Set explicitly
set_correlation_id("custom-correlation-id")

# Read current value
current_id = get_correlation_id()

# Context manager for scoped correlation
from acme_platform.logging import correlation_scope

with correlation_scope("batch-job-42"):
    logger.info("Processing batch")  # correlation_id = "batch-job-42"
    client.events.publish(...)       # X-Correlation-Id header set automatically

logger.info("Outside batch")  # Reverts to previous correlation_id
```

## Request/Response Logging

The SDK can log all outgoing HTTP requests and their responses. This is controlled separately from the main log level to avoid noise:

```python
client = AcmePlatformClient(
    api_key="your-api-key",
    http_logging={
        "log_requests": True,        # Log method, URL, headers
        "log_responses": True,       # Log status, headers, duration
        "log_bodies": False,         # Log request/response bodies (careful with PII)
        "max_body_length": 1024,     # Truncate bodies longer than this
        "redact_headers": [          # Headers to redact in logs
            "Authorization",
            "X-Acme-Api-Key"
        ]
    }
)
```

Example output when request/response logging is enabled:

```json
{"level": "DEBUG", "message": "HTTP request", "method": "POST", "url": "https://events.acmeplatform.io/v2/publish", "correlation_id": "req-a1b2c3d4"}
{"level": "DEBUG", "message": "HTTP response", "method": "POST", "url": "https://events.acmeplatform.io/v2/publish", "status": 202, "duration_ms": 87, "correlation_id": "req-a1b2c3d4"}
```

## Performance Logging

The SDK includes a timing utility for measuring code execution:

```python
from acme_platform.logging import timed

@timed(logger, operation="process_order")
def process_order(order_id: str):
    # ... business logic ...
    return result

# Produces: {"message": "process_order completed", "duration_ms": 234, ...}

# Or use the context manager form
with logger.timed("database_query"):
    rows = db.execute("SELECT * FROM orders WHERE status = ?", ["pending"])

# Produces: {"message": "database_query completed", "duration_ms": 12, ...}
```

## Integration with ELK Stack

### Filebeat Configuration

To ship Acme SDK logs to Elasticsearch via Filebeat:

```yaml
# filebeat.yml
filebeat.inputs:
  - type: log
    enabled: true
    paths:
      - /var/log/acme/*.log
    json.keys_under_root: true
    json.add_error_key: true
    json.message_key: message

output.elasticsearch:
  hosts: ["https://elasticsearch.internal:9200"]
  index: "acme-services-%{+yyyy.MM.dd}"
```

### Kibana Index Pattern

Create an index pattern matching `acme-services-*` and use the following key fields for filtering and dashboarding:

- `service` -- Filter by service name
- `correlation_id` -- Trace a request across services
- `level` -- Filter by severity
- `duration_ms` -- Analyze latency distributions
- `environment` -- Separate production from staging logs

## Python `logging` Module Compatibility

The `AcmeLogger` is compatible with Python's standard `logging` module. You can attach it as a handler:

```python
import logging
from acme_platform.logging import AcmeLogHandler

handler = AcmeLogHandler(service_name="order-processor")
logging.getLogger().addHandler(handler)

# Standard library logging now produces structured JSON
logging.info("Standard log message", extra={"order_id": "ORD-12345"})
```
