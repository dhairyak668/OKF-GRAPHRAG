# Event-Driven Architecture at Acme Platform

## Overview

The Acme Platform follows an **event-driven architecture (EDA)** to enable loose coupling, independent scalability, and resilience across its microservices. This document outlines the architectural principles, patterns, and decision frameworks that guide our use of events.

---

## Core Principles

1. **Services own their data.** No service reads another service's database directly. Data is shared through events.
2. **Events are facts, not commands.** An event records something that happened (`OrderCreated`) rather than requesting an action (`CreateInvoice`). The producer does not know or care who consumes its events.
3. **Eventual consistency is the default.** Services achieve consistency over time through event propagation. Strong consistency is reserved for operations within a single service boundary.
4. **Schema is a contract.** Event schemas are versioned, backward-compatible, and enforced by the Schema Registry.

---

## When to Use Events vs. HTTP

Choosing between asynchronous events and synchronous HTTP calls is one of the most common design decisions. Use this decision matrix:

| Criteria                        | Events (Kafka)          | HTTP (REST/gRPC)          |
|---------------------------------|-------------------------|---------------------------|
| Coupling                        | Loose                   | Tight                     |
| Response needed immediately?    | No                      | Yes                       |
| Multiple consumers?             | Yes (fan-out)           | No (point-to-point)       |
| Failure tolerance               | High (buffered)         | Low (synchronous failure) |
| Ordering required?              | Within partition         | Per-request               |
| Payload size                    | Up to 1 MB (standard)  | Flexible                  |
| Latency requirement             | Seconds acceptable      | Sub-second required       |

### Use Events When

- A state change in one service should trigger actions in other services (e.g., order created triggers billing, inventory, and notification)
- You need fan-out to multiple consumers without the producer knowing about them
- The operation can tolerate seconds of delay
- You want resilience against downstream service outages (events buffer in Kafka)

### Use HTTP When

- The caller needs an immediate, synchronous response (e.g., "Is this coupon valid?")
- The interaction is a query that does not change state
- You are integrating with an external API that only supports HTTP
- Sub-second latency is required for the user experience

### Hybrid Pattern

Some workflows use both. For example, the checkout flow:

1. **HTTP**: Frontend calls the Order Service to validate and create an order (synchronous -- the user needs a confirmation)
2. **Event**: Order Service publishes `OrderCreated` to Kafka
3. **Event**: Billing Service consumes the event and generates an invoice asynchronously
4. **Event**: Inventory Service consumes the event and reserves stock
5. **HTTP**: Frontend polls or uses WebSocket to check fulfillment status

---

## EDA Patterns in Use

### 1. Event Notification

The simplest pattern. A service publishes a lightweight event announcing that something happened. Consumers react independently.

```
Order Service --[OrderCreated]--> Kafka
    ├── Billing Service (generate invoice)
    ├── Inventory Service (reserve stock)
    ├── Notification Service (send confirmation email)
    └── Analytics Pipeline (record event)
```

The event contains the entity ID and key fields. If a consumer needs the full entity, it calls back to the source service's API. This keeps events small but adds a runtime dependency.

### 2. Event-Carried State Transfer

The event contains the full entity state, so consumers never need to call back to the source. This is the preferred pattern at Acme for most domain events.

**Advantages**: No callback dependency, consumers can build local read models.
**Disadvantages**: Larger payloads, risk of data duplication, schema evolution is more complex.

### 3. Saga (Choreography)

For multi-step business processes that span services, we use choreography-based sagas. Each service listens for events and emits its own events to advance the workflow.

```
OrderCreated --> Billing: InvoiceGenerated --> Inventory: StockReserved --> Fulfillment: ShipmentInitiated
                     |                              |
                     v                              v
              (on failure)                    (on failure)
          BillingFailed --> Order: OrderCancelled   StockInsufficient --> Billing: InvoiceVoided
```

**Compensating actions** are used for rollback. If stock reservation fails, the billing service voids the invoice, and the order service cancels the order.

### 4. Saga (Orchestration)

For complex workflows with many steps or conditional branching, an orchestrator service coordinates the saga. The Acme Fulfillment Orchestrator manages the end-to-end order fulfillment process.

```
┌─────────────────────────────┐
│  Fulfillment Orchestrator   │
│                             │
│  1. Reserve inventory       │
│  2. Charge payment          │
│  3. Schedule shipment       │
│  4. Send confirmation       │
└─────────────────────────────┘
       │          │
  Commands via    Results via
  Kafka topics    Kafka topics
       │          │
       ▼          ▼
  ┌─────────┐ ┌──────────┐
  │Services │ │ respond   │
  └─────────┘ └──────────┘
```

The orchestrator sends **commands** (not events) to specific topics and waits for **result events**. This provides centralized visibility into the workflow state.

---

## Event Sourcing

### Concept

Instead of storing the current state of an entity, event sourcing stores the sequence of state-changing events. The current state is derived by replaying all events.

### Where We Use It

Event sourcing is used selectively at Acme, not as a platform-wide default:

- **Audit Log Service**: All state changes across the platform are captured as an immutable event log. This is the canonical audit trail.
- **Order Service**: Order state is derived from the sequence of order events (`Created`, `Paid`, `Shipped`, `Delivered`, `Returned`). This provides a complete history and enables replaying orders for debugging.

### Where We Do Not Use It

- **User Profile Service**: Profiles are CRUD entities with simple updates. Event sourcing adds unnecessary complexity.
- **Notification Service**: Notifications are fire-and-forget. There is no business value in replaying notification events.

### Implementation Notes

```python
class OrderAggregate:
    def __init__(self, order_id: str):
        self.order_id = order_id
        self.status = None
        self.items = []
        self.total = 0
        self.events = []

    def apply(self, event: dict):
        handler = getattr(self, f"_apply_{event['type']}", None)
        if handler:
            handler(event)
        self.events.append(event)

    def _apply_order_created(self, event):
        self.status = "created"
        self.items = event["items"]
        self.total = event["total_amount_cents"]

    def _apply_order_paid(self, event):
        self.status = "paid"

    def _apply_order_shipped(self, event):
        self.status = "shipped"

    @classmethod
    def load(cls, order_id: str, event_store):
        aggregate = cls(order_id)
        for event in event_store.get_events(order_id):
            aggregate.apply(event)
        return aggregate
```

---

## CQRS (Command Query Responsibility Segregation)

### Concept

CQRS separates the write model (commands that change state) from the read model (queries that return data). At Acme, services that use event sourcing also use CQRS to provide efficient read paths.

### Architecture

```
  Commands                              Queries
     │                                     │
     ▼                                     ▼
┌──────────┐   events    ┌──────────────────────┐
│ Write    │ ──────────> │ Read Model Projector  │
│ Model    │             │                       │
│ (Event   │             │ Builds materialized   │
│  Store)  │             │ views from events     │
└──────────┘             └───────────┬───────────┘
                                     │
                                     ▼
                              ┌──────────────┐
                              │ Read Database │
                              │ (PostgreSQL / │
                              │  Elasticsearch│
                              │  / Redis)     │
                              └──────────────┘
```

### Example: Order Dashboard

The Order Service writes events to its event store. A **projector** consumes those events and maintains a denormalized `order_summary` table in PostgreSQL, optimized for the dashboard queries:

```sql
CREATE TABLE order_summary (
    order_id        VARCHAR(64) PRIMARY KEY,
    customer_id     VARCHAR(64) NOT NULL,
    customer_name   VARCHAR(255),
    status          VARCHAR(32) NOT NULL,
    item_count      INT NOT NULL,
    total_cents     BIGINT NOT NULL,
    created_at      TIMESTAMP NOT NULL,
    updated_at      TIMESTAMP NOT NULL
);

CREATE INDEX idx_order_summary_customer ON order_summary(customer_id);
CREATE INDEX idx_order_summary_status ON order_summary(status);
CREATE INDEX idx_order_summary_created ON order_summary(created_at);
```

The dashboard API reads from this table directly, avoiding expensive joins or event replay.

### Trade-offs

| Benefit                              | Cost                                      |
|--------------------------------------|-------------------------------------------|
| Optimized read and write paths       | Increased system complexity               |
| Independent scaling of reads/writes  | Eventual consistency between models       |
| Flexible read model schemas          | Need to rebuild projections on changes    |
| Clear separation of concerns         | More infrastructure to operate            |

---

## Further Reading

- [Event Bus README](README.md) -- Kafka setup, topics, consumer groups
- [Event Processing Patterns](processing.md) -- consumer implementation, error handling, DLQ
- Martin Fowler, [Event-Driven Architecture](https://martinfowler.com/articles/201701-event-driven.html)
- Chris Richardson, [Microservices Patterns](https://microservices.io/patterns/)
