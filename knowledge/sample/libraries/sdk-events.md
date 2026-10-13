---
id: sdk-events
title: SDK Event Publishing Module
type: Tool
status: stable
tags: [sdk, events, publishing]
links:
  - target: sdk-client
    relation: part-of
    label: SDK module
  - target: event-bus
    relation: publishes-to
    label: Event publishing
  - target: event-driven-architecture
    relation: implements
    label: EDA client
sources:
  - id: sdk-events-doc
    resource: /source_docs/sdk-client/events.md
    title: SDK Events Guide
generated:
  by: "human:author"
  at: "2026-10-01T00:00:00Z"
---

# SDK Event Publishing Module

The events module enables services to publish domain events to the Acme Platform [Event Bus](../services/event-bus.md). It implements the client side of the platform's [Event-Driven Architecture](../concepts/event-driven-architecture.md), handling schema validation, batching, and delivery guarantees.

## Publishing Events

Create and publish events with a single call:

```python
from acme_sdk.events import Event

event = Event(
    type="deployment.completed",
    source="svc-deployer",
    data={
        "project_id": "proj-abc123",
        "environment": "production",
        "version": "2.4.1",
        "duration_seconds": 142,
    },
)

result = client.events.publish(event)
print(f"Event ID: {result.event_id}")
```

## Schema Validation

All events are validated against registered schemas before publishing. The SDK fetches and caches schemas from the Event Bus schema registry:

```python
from acme_sdk.events import SchemaRegistry

# Pre-validate without publishing
registry = client.events.schema_registry
is_valid = registry.validate("deployment.completed", event_data)

if not is_valid:
    print(registry.last_errors)
```

Schema validation ensures that consumers can rely on a consistent event structure. If a payload does not conform to its registered schema, a `SchemaValidationError` is raised.

## Batch Publishing

For high-throughput scenarios, batch multiple events into a single network call:

```python
from acme_sdk.events import EventBatch

batch = EventBatch()
for record in records:
    batch.add(Event(
        type="record.processed",
        source="svc-etl",
        data={"record_id": record.id, "status": "complete"},
    ))

results = client.events.publish_batch(batch)
print(f"Published {results.success_count}/{results.total_count} events")
```

Batches are limited to 500 events or 1 MB of serialized payload, whichever is reached first. The SDK automatically splits oversized batches.

## Delivery Guarantees

The module supports two delivery modes:

| Mode            | Guarantee            | Use Case                       |
|-----------------|----------------------|--------------------------------|
| `at-least-once` | Events may duplicate | Default; suitable for most use |
| `exactly-once`  | Idempotent delivery  | Financial or audit events      |

```python
from acme_sdk.events import DeliveryMode

client.events.publish(event, delivery=DeliveryMode.EXACTLY_ONCE)
```

Exactly-once delivery uses an idempotency key derived from the event's `source`, `type`, and a caller-supplied `dedup_id`.

## Error Handling

```python
from acme_sdk.events.exceptions import (
    SchemaValidationError,
    EventBusUnavailableError,
    QuotaExceededError,
)

try:
    client.events.publish(event)
except SchemaValidationError as e:
    print(f"Invalid payload: {e.errors}")
except QuotaExceededError:
    print("Event publishing quota exceeded — back off and retry.")
except EventBusUnavailableError:
    print("Event Bus is temporarily unavailable.")
```

## Related Documentation

- [SDK Client](../libraries/sdk-client.md) — parent SDK package
- [Event Bus](../services/event-bus.md) — platform event backbone
- [Event-Driven Architecture](../concepts/event-driven-architecture.md) — architectural pattern overview
