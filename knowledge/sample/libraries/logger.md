---
id: logger
title: Structured Logging Library
type: Tool
status: stable
tags: [logging, observability, structured]
links:
  - target: api-gateway
    relation: used-by
    label: Gateway logging
  - target: monitoring-setup
    relation: feeds-into
    label: Log aggregation
sources:
  - id: logger-doc
    resource: /source_docs/sdk-client/logging.md
    title: Structured Logging Guide
generated:
  by: "human:author"
  at: "2026-10-01T00:00:00Z"
---

# Structured Logging Library

The Acme Platform structured logging library produces machine-readable JSON log output designed for aggregation in the platform's centralized [Monitoring Setup](../workflows/monitoring-setup.md). It is used across all Acme services, including the [API Gateway](../services/api-gateway.md).

## Installation

```bash
pip install acme-logger
```

## Basic Usage

```python
from acme_logger import get_logger

log = get_logger("my-service")

log.info("Request received", method="GET", path="/api/v1/projects")
log.warning("Slow upstream", latency_ms=1240, upstream="user-svc")
log.error("Handler failed", exc_info=True)
```

Each call produces a single JSON line:

```json
{
  "timestamp": "2026-09-15T08:12:44.312Z",
  "level": "INFO",
  "logger": "my-service",
  "message": "Request received",
  "method": "GET",
  "path": "/api/v1/projects",
  "correlation_id": "req-7f3a9b2c"
}
```

## Log Levels

The library supports standard log levels with platform-specific semantics:

| Level    | Numeric | Usage                                           |
|----------|--------:|-------------------------------------------------|
| `DEBUG`  | 10      | Verbose diagnostic detail; disabled in prod     |
| `INFO`   | 20      | Normal operational events                       |
| `WARNING`| 30      | Degraded state; action may be needed soon       |
| `ERROR`  | 40      | Failure requiring investigation                 |
| `CRITICAL`| 50     | System-wide failure; triggers on-call alert     |

## Context Propagation

Attach persistent fields to all log entries within a scope using the context manager:

```python
from acme_logger import log_context

with log_context(request_id="req-7f3a9b2c", tenant="acme-corp"):
    log.info("Processing started")    # includes request_id and tenant
    log.info("Processing complete")   # same context fields
```

Context is propagated across async boundaries when using the Acme async runtime.

## Correlation IDs

Every inbound request to the [API Gateway](../services/api-gateway.md) receives a correlation ID in the `X-Correlation-ID` header. The logger automatically extracts and attaches it to all log entries within that request scope:

```python
from acme_logger.middleware import LoggingMiddleware

app.add_middleware(LoggingMiddleware())
# All logs in the request lifecycle now include correlation_id
```

This allows tracing a single user request across multiple services in the log aggregation system.

## Output Formats

The library supports pluggable output formatters:

```python
from acme_logger import get_logger, JsonFormatter, ConsoleFormatter

log = get_logger("my-service")

# JSON (default in production)
log.set_formatter(JsonFormatter())

# Human-readable (useful for local development)
log.set_formatter(ConsoleFormatter(colorize=True))
```

## Configuration

Configure globally via environment variables or programmatically:

```python
import os
os.environ["ACME_LOG_LEVEL"] = "DEBUG"
os.environ["ACME_LOG_FORMAT"] = "json"

# Or programmatically
from acme_logger import configure
configure(level="DEBUG", format="json", service_name="my-service")
```

## Related Documentation

- [API Gateway](../services/api-gateway.md) — primary consumer of structured logs
- [Monitoring Setup](../workflows/monitoring-setup.md) — log aggregation and alerting pipeline
