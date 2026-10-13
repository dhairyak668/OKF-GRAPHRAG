---
id: event-processing
title: Event Processing Workflow
type: Process
status: stable
tags: [events, processing, async, pipeline]
links:
  - target: event-bus
    relation: processes-from
    label: Event consumption
  - target: notification-engine
    relation: triggers
    label: Notification dispatch
  - target: event-driven-architecture
    relation: implements
    label: EDA workflow
sources:
  - id: event-proc-doc
    resource: /source_docs/event-bus/processing.md
    title: Event Processing Guide
generated:
  by: "human:author"
  at: "2026-10-01T00:00:00Z"
---

# Event Processing Workflow

The event processing workflow defines how Acme Platform microservices consume, transform, and act on events published to the [Event Bus](../services/event-bus.md). This workflow implements the [Event-Driven Architecture](../concepts/event-driven-architecture.md) pattern used across the platform.

## Consumer Setup

Each service that processes events registers one or more consumer groups with the Event Bus. Consumer groups ensure that each event is processed by exactly one instance within a service's replica set.

```python
from acme_sdk.events import EventConsumer, ConsumerConfig

config = ConsumerConfig(
    broker_urls=["kafka-0.acme-platform.io:9092"],
    group_id="order-service-consumers",
    topics=["order.created", "order.updated", "order.cancelled"],
    auto_offset_reset="earliest",
    enable_auto_commit=False,
)

consumer = EventConsumer(config)
```

### Consumer Configuration

| Parameter | Description | Default |
|-----------|-------------|---------|
| `group_id` | Consumer group identifier | Required |
| `topics` | List of topics to subscribe to | Required |
| `auto_offset_reset` | Where to start reading if no offset exists | `earliest` |
| `enable_auto_commit` | Whether offsets are committed automatically | `False` |
| `max_poll_records` | Maximum records returned per poll | `500` |
| `session_timeout_ms` | Consumer session timeout | `30000` |

## Event Handlers

Event handlers are registered per event type. The framework routes incoming events to the appropriate handler based on the event's `type` field.

```python
from acme_sdk.events import event_handler, EventContext

@event_handler("order.created")
async def handle_order_created(event: dict, ctx: EventContext):
    """Process a newly created order."""
    order_id = event["payload"]["order_id"]
    customer_id = event["payload"]["customer_id"]

    # Validate inventory
    inventory = await ctx.services.inventory.check(order_id)
    if not inventory.available:
        await ctx.publish("order.rejected", {
            "order_id": order_id,
            "reason": "insufficient_inventory",
        })
        return

    # Reserve inventory and confirm
    await ctx.services.inventory.reserve(order_id)
    await ctx.publish("order.confirmed", {
        "order_id": order_id,
        "customer_id": customer_id,
    })

@event_handler("order.confirmed")
async def handle_order_confirmed(event: dict, ctx: EventContext):
    """Trigger notification on order confirmation."""
    await ctx.services.notification.send(
        recipient=event["payload"]["customer_id"],
        template="order_confirmed",
        data=event["payload"],
    )
```

The [Notification Engine](../services/notification-engine.md) receives the notification dispatch and delivers it through the configured channel (email, SMS, push).

## Error Handling

Failed event processing follows a retry-then-dead-letter strategy. Transient failures (network timeouts, temporary unavailability) are retried with exponential backoff. Permanent failures are routed to the dead letter queue.

```python
from acme_sdk.events import RetryPolicy, DeadLetterConfig

retry_policy = RetryPolicy(
    max_retries=3,
    initial_delay_ms=1000,
    max_delay_ms=30000,
    backoff_multiplier=2.0,
    retryable_exceptions=[TimeoutError, ConnectionError],
)

dlq_config = DeadLetterConfig(
    topic="events.dead-letter",
    include_error_metadata=True,
    alert_on_threshold=100,
)

consumer.configure(retry_policy=retry_policy, dead_letter=dlq_config)
```

### Retry Flow

```
Event received --> Handler executes --> Success --> Commit offset
                        |
                     Failure
                        |
                  Retryable? --No--> Dead Letter Queue
                        |
                       Yes
                        |
                  Retry (backoff) --> Handler executes --> ...
                        |
                  Max retries exceeded --> Dead Letter Queue
```

## Dead Letter Queues

Events that exhaust all retries are published to a dedicated dead letter topic. Each dead letter message includes the original event, the error details, and processing metadata.

```json
{
  "original_event": { "type": "order.created", "payload": { "order_id": "abc-123" } },
  "error": {
    "type": "InventoryServiceUnavailable",
    "message": "Connection refused after 3 retries",
    "stack_trace": "..."
  },
  "metadata": {
    "consumer_group": "order-service-consumers",
    "topic": "order.created",
    "partition": 2,
    "offset": 48291,
    "retry_count": 3,
    "first_attempt_at": "2026-09-15T10:30:00Z",
    "last_attempt_at": "2026-09-15T10:30:31Z"
  }
}
```

A monitoring dashboard tracks dead letter queue depth and alerts when thresholds are exceeded.

## Idempotency

All event handlers must be idempotent. The platform provides an idempotency key store backed by Redis to detect duplicate processing.

```python
from acme_sdk.events import idempotent

@event_handler("payment.completed")
@idempotent(key_field="payload.payment_id", ttl_hours=24)
async def handle_payment_completed(event: dict, ctx: EventContext):
    """Process payment -- safe to call multiple times."""
    payment_id = event["payload"]["payment_id"]
    await ctx.services.ledger.record_payment(payment_id)
```

The `@idempotent` decorator checks whether the event has already been processed using the specified key field. If a duplicate is detected, the handler returns immediately without re-executing.

## Related

- [Event Bus](../services/event-bus.md) -- message broker that delivers events to consumers
- [Notification Engine](../services/notification-engine.md) -- dispatches notifications triggered by event handlers
- [Event-Driven Architecture](../concepts/event-driven-architecture.md) -- architectural pattern this workflow implements
