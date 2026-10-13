---
id: event-driven-architecture
title: Event-Driven Architecture
type: Pattern
status: stable
tags: [architecture, events, patterns, async]
links:
  - target: event-bus
    relation: implemented-by
    label: Bus implementation
  - target: event-processing
    relation: realized-by
    label: Processing workflow
  - target: notification-engine
    relation: enables
    label: Async notifications
sources:
  - id: eda-doc
    resource: /source_docs/event-bus/architecture.md
    title: Event-Driven Architecture Overview
generated:
  by: "human:author"
  at: "2026-10-01T00:00:00Z"
---

# Event-Driven Architecture

Event-Driven Architecture (EDA) is the foundational integration pattern for the Acme Platform. Rather than services calling each other synchronously, they communicate by publishing and subscribing to events through the [Event Bus](../services/event-bus.md).

## Core Principles

### Loose Coupling

Services do not need to know about each other. A producer publishes an event describing what happened; any number of consumers may react to it independently. This eliminates tight service-to-service dependencies and allows teams to evolve their services without coordinating deployments.

### Temporal Decoupling

Producers and consumers do not need to be running at the same time. Events are durably stored in the bus and consumed when the subscriber is ready. This absorbs traffic spikes and makes the system resilient to partial outages.

### Single Responsibility for Events

Each event represents a single business fact. Events are immutable records of something that has already occurred, not commands to be executed.

## Publish / Subscribe Pattern

The pub/sub pattern is the primary communication model. Producers publish events to named topics; consumers subscribe to topics of interest.

```
┌──────────┐     order.created     ┌───────────┐
│  Order    │ ──────────────────>  │           │
│  Service  │                      │  Event    │
└──────────┘                      │  Bus      │
                                   │ (Kafka)   │
┌──────────┐   payment.completed   │           │
│  Payment  │ ──────────────────>  │           │
│  Service  │                      └─────┬─────┘
└──────────┘                            │
                                         ├──> Inventory Service
                                         ├──> Analytics Service
                                         └──> Notification Engine
```

The [Event Bus](../services/event-bus.md) is implemented on Apache Kafka, providing durable, ordered, partitioned event streams.

## Event Sourcing

For services that require a complete audit trail, the platform supports event sourcing. Instead of storing current state, the service stores the sequence of events that produced the current state.

```python
# Event-sourced order aggregate
class OrderAggregate:
    def __init__(self, order_id: str):
        self.order_id = order_id
        self.status = None
        self.items = []
        self.events = []

    def apply(self, event: dict):
        event_type = event["type"]
        if event_type == "order.created":
            self.status = "created"
            self.items = event["payload"]["items"]
        elif event_type == "order.confirmed":
            self.status = "confirmed"
        elif event_type == "order.shipped":
            self.status = "shipped"
        self.events.append(event)

    def rebuild_from_events(self, events: list[dict]):
        for event in events:
            self.apply(event)
```

### Snapshotting

For aggregates with long event histories, periodic snapshots reduce replay time:

```python
class SnapshotStore:
    async def save_snapshot(self, aggregate_id: str, state: dict, version: int):
        """Save a snapshot at the current version."""
        await self.store.put(f"snapshot:{aggregate_id}", {
            "state": state,
            "version": version,
            "created_at": datetime.utcnow().isoformat(),
        })

    async def load_with_snapshot(self, aggregate_id: str) -> OrderAggregate:
        """Load aggregate from snapshot + subsequent events."""
        snapshot = await self.store.get(f"snapshot:{aggregate_id}")
        events = await self.event_store.get_events(
            aggregate_id, after_version=snapshot["version"]
        )
        aggregate = OrderAggregate(aggregate_id)
        aggregate.restore_from_snapshot(snapshot["state"])
        aggregate.rebuild_from_events(events)
        return aggregate
```

## CQRS (Command Query Responsibility Segregation)

CQRS separates the write model (commands) from the read model (queries). Events bridge the two: commands produce events, and projections consume events to build read-optimized views.

```
Commands ──> Write Model ──> Events ──> Projections ──> Read Model
                                │
                                └──> Event Store (append-only log)
```

### Projections

Projections transform event streams into materialized views optimized for specific query patterns:

```python
from acme_sdk.events import Projection

class OrderDashboardProjection(Projection):
    """Builds a denormalized view for the order dashboard."""

    async def on_order_created(self, event: dict):
        await self.view.upsert("orders", {
            "order_id": event["payload"]["order_id"],
            "customer_name": event["payload"]["customer_name"],
            "status": "created",
            "total": event["payload"]["total"],
            "created_at": event["metadata"]["timestamp"],
        })

    async def on_order_shipped(self, event: dict):
        await self.view.update("orders",
            filter={"order_id": event["payload"]["order_id"]},
            update={"status": "shipped", "shipped_at": event["metadata"]["timestamp"]},
        )
```

## Eventual Consistency

EDA trades strong consistency for availability and partition tolerance. Services must be designed to tolerate eventual consistency.

### Guidelines

1. **Design for idempotency** -- handlers must produce the same result if an event is delivered more than once.
2. **Use correlation IDs** -- every event carries a correlation ID that traces it back to the originating request.
3. **Compensating actions** -- if a downstream step fails, publish a compensating event rather than relying on distributed transactions.
4. **Timeouts and deadlines** -- set explicit deadlines for saga steps; trigger compensation if a step does not complete in time.

## Event Schema

All events follow a standard envelope:

```json
{
  "id": "evt_01HQXYZ",
  "type": "order.created",
  "source": "order-service",
  "correlation_id": "req_01HQABC",
  "timestamp": "2026-09-15T10:30:00Z",
  "version": 1,
  "payload": {
    "order_id": "ord_12345",
    "customer_id": "cust_67890",
    "items": [{ "sku": "WIDGET-A", "quantity": 2 }]
  }
}
```

Schema evolution is managed through versioning. Consumers must be forward-compatible: they ignore unknown fields and handle missing optional fields gracefully.

## Related

- [Event Bus](../services/event-bus.md) -- Kafka-based implementation of the event backbone
- [Event Processing](../workflows/event-processing.md) -- workflow for consuming and acting on events
- [Notification Engine](../services/notification-engine.md) -- asynchronous notification delivery enabled by EDA
