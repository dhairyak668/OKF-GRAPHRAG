# SDK Events Guide

## Overview

The Events module in the Acme Platform SDK provides a high-level interface for publishing events to the Acme Event Bus. It handles schema validation, serialization, delivery acknowledgment, and retry logic, ensuring reliable event-driven communication between services.

## Event Publishing API

### Basic Publishing

```python
from acme_platform import AcmePlatformClient

client = AcmePlatformClient(api_key="your-api-key")

# Publish a single event
result = client.events.publish(
    topic="order.created",
    payload={
        "order_id": "ORD-12345",
        "customer_id": "CUST-6789",
        "total_amount": 149.99,
        "currency": "USD",
        "line_items": [
            {"sku": "WIDGET-A", "quantity": 2, "price": 49.99},
            {"sku": "GADGET-B", "quantity": 1, "price": 50.01}
        ]
    },
    schema_version="2.1.0"
)

print(f"Event ID: {result.event_id}")
print(f"Published at: {result.timestamp}")
print(f"Partition: {result.partition}")
```

### Specifying a Partition Key

Events with the same partition key are guaranteed to be delivered in order:

```python
client.events.publish(
    topic="order.updated",
    payload={"order_id": "ORD-12345", "status": "shipped"},
    partition_key="ORD-12345",
    schema_version="2.1.0"
)
```

## Event Schema Definition

Every event must reference a registered schema. Schemas are defined using JSON Schema and registered in the Acme Schema Registry.

```python
# Register a schema (typically done during CI/CD, not at runtime)
from acme_platform.events import SchemaRegistry

registry = SchemaRegistry(client)

registry.register(
    topic="order.created",
    version="2.1.0",
    schema={
        "type": "object",
        "required": ["order_id", "customer_id", "total_amount", "currency"],
        "properties": {
            "order_id": {"type": "string", "pattern": "^ORD-[0-9]+$"},
            "customer_id": {"type": "string"},
            "total_amount": {"type": "number", "minimum": 0},
            "currency": {"type": "string", "enum": ["USD", "EUR", "GBP"]},
            "line_items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["sku", "quantity", "price"],
                    "properties": {
                        "sku": {"type": "string"},
                        "quantity": {"type": "integer", "minimum": 1},
                        "price": {"type": "number", "minimum": 0}
                    }
                }
            }
        }
    },
    compatibility="BACKWARD"
)
```

## Batch Publishing

For high-throughput scenarios, batch publishing reduces network overhead by sending multiple events in a single request:

```python
from acme_platform.events import EventBatch

batch = EventBatch()

for order in pending_orders:
    batch.add(
        topic="order.created",
        payload=order.to_dict(),
        partition_key=order.order_id,
        schema_version="2.1.0"
    )

results = client.events.publish_batch(batch)

print(f"Published: {results.success_count}")
print(f"Failed: {results.failure_count}")

for failure in results.failures:
    print(f"  Event {failure.index}: {failure.error}")
```

Batch limits:
- Maximum 500 events per batch
- Maximum 5 MB total payload size per batch
- Events within a batch may span multiple topics

## Delivery Guarantees

The Acme Event Bus provides **at-least-once delivery**. This means:

- Every published event is guaranteed to be delivered to all subscribers at least once.
- Subscribers may receive duplicate events during retries or rebalancing.
- Consumers must implement idempotency using the `event_id` field.

The SDK confirms delivery by waiting for an acknowledgment from the Event Bus broker. If acknowledgment is not received within the configured timeout, the SDK retries the publish operation.

```python
client = AcmePlatformClient(
    api_key="your-api-key",
    events_config={
        "publish_timeout": 10.0,       # Seconds to wait for ack
        "publish_retries": 3,           # Max retry attempts
        "retry_backoff_base": 0.5       # Exponential backoff base in seconds
    }
)
```

## Event Serialization

Events are serialized to JSON by default. The SDK applies the following transformations:

- `datetime` objects are serialized to ISO 8601 strings
- `Decimal` values are serialized as strings to preserve precision
- `UUID` objects are serialized as lowercase hyphenated strings
- `dataclass` and `pydantic.BaseModel` instances are serialized via their `.dict()` or `.__dict__` methods

You can provide a custom serializer:

```python
import orjson

client.events.configure(
    serializer=lambda payload: orjson.dumps(payload).decode("utf-8")
)
```

## Schema Validation Before Publish

By default, the SDK validates every event payload against its registered schema before sending it to the Event Bus. This catches schema violations early and avoids rejected messages.

```python
from acme_platform.exceptions import ValidationError

try:
    client.events.publish(
        topic="order.created",
        payload={"order_id": 12345},  # Should be a string
        schema_version="2.1.0"
    )
except ValidationError as e:
    print(f"Schema violation: {e.message}")
    print(f"Path: {e.json_path}")       # e.g., "$.order_id"
    print(f"Expected: {e.expected}")     # e.g., "string"
    print(f"Got: {e.actual}")            # e.g., "integer"
```

To disable client-side validation (for example, in performance-critical paths where the schema is guaranteed by construction):

```python
client.events.publish(
    topic="order.created",
    payload=data,
    schema_version="2.1.0",
    validate=False
)
```

## Async Event Publishing

The async client provides non-blocking event publishing:

```python
import asyncio
from acme_platform import AcmeAsyncPlatformClient

async def publish_events():
    client = AcmeAsyncPlatformClient(api_key="your-api-key")
    
    # Single async publish
    result = await client.events.publish(
        topic="order.created",
        payload={"order_id": "ORD-12345", "total_amount": 99.95},
        schema_version="2.1.0"
    )
    
    # Fire-and-forget publishing (no ack wait)
    await client.events.publish(
        topic="analytics.page_view",
        payload={"page": "/checkout", "session_id": "sess-abc"},
        schema_version="1.0.0",
        fire_and_forget=True
    )
    
    # Concurrent batch publishing
    tasks = [
        client.events.publish(
            topic="inventory.reserved",
            payload={"sku": sku, "quantity": qty},
            schema_version="1.0.0"
        )
        for sku, qty in [("WIDGET-A", 2), ("GADGET-B", 1)]
    ]
    results = await asyncio.gather(*tasks)
    
    await client.close()

asyncio.run(publish_events())
```

## Event Metadata

Every published event is automatically enriched with metadata:

| Field | Description |
|---|---|
| `event_id` | Unique UUID assigned by the SDK |
| `timestamp` | ISO 8601 publication timestamp |
| `source` | Service identity from client configuration |
| `correlation_id` | Propagated from the current request context |
| `schema_version` | Schema version used for validation |
| `topic` | Target topic name |

You can add custom metadata headers:

```python
client.events.publish(
    topic="order.created",
    payload=data,
    schema_version="2.1.0",
    headers={
        "x-triggered-by": "checkout-service",
        "x-idempotency-key": "idem-abc-123"
    }
)
```
