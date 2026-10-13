---
id: event-bus
title: Event Bus
type: System
status: stable
tags: [events, messaging, kafka, async]
links:
  - target: api-gateway
    relation: receives-from
    label: Gateway events
  - target: notification-engine
    relation: publishes-to
    label: Notification events
  - target: event-driven-architecture
    relation: implements
    label: EDA implementation
sources:
  - id: eventbus-readme
    resource: /source_docs/event-bus/README.md
    title: Event Bus README
generated:
  by: "human:author"
  at: "2026-10-01T00:00:00Z"
---

# Event Bus

The Event Bus is the central asynchronous messaging backbone of the Acme Platform. Built on Apache Kafka, it enables decoupled, event-driven communication between services. The [API Gateway](../services/api-gateway.md) publishes request events to the bus, and downstream consumers like the [Notification Engine](../services/notification-engine.md) subscribe to relevant topics to react to domain events. The Event Bus is the primary implementation of the platform's [Event-Driven Architecture](../concepts/event-driven-architecture.md).

## Overview

The Event Bus provides:

- **Publish/Subscribe Messaging** -- Producers publish events to named topics; consumers subscribe to topics via consumer groups.
- **Guaranteed Delivery** -- Events are durably stored in Kafka with configurable replication and retention.
- **Ordered Processing** -- Events within a partition are processed in order, enabling consistent state transitions.
- **Schema Enforcement** -- All events conform to schemas registered in the Schema Registry, preventing malformed data from entering the bus.
- **Dead Letter Routing** -- Events that cannot be processed are routed to dead-letter topics for investigation and replay.

## Cluster Architecture

The Kafka cluster runs on dedicated nodes with the following configuration:

| Parameter | Value | Notes |
|---|---|---|
| Brokers | 5 | Spread across 3 availability zones |
| Replication Factor | 3 | Tolerates 2 broker failures |
| Min In-Sync Replicas | 2 | Ensures durability on write |
| Retention | 7 days | Default; configurable per topic |
| Compaction | Enabled selectively | For state-change topics |
| Compression | LZ4 | Balances CPU and throughput |

ZooKeeper has been replaced with KRaft (Kafka Raft) for metadata management, eliminating the ZooKeeper dependency.

## Topics

Topics are organized by domain and follow a hierarchical naming convention:

```
<domain>.<entity>.<version>

Examples:
  platform.deployments.v1
  platform.alerts.v1
  platform.users.v1
  platform.gateway-requests.v1
  platform.notifications.v1
```

Topic creation is managed through Terraform and requires approval via pull request. Each topic definition specifies its partition count, replication, retention, and schema:

```yaml
topics:
  - name: platform.deployments.v1
    partitions: 12
    replication_factor: 3
    retention_ms: 604800000  # 7 days
    cleanup_policy: delete
    schema: "schemas/deployment-event.avsc"

  - name: platform.alerts.v1
    partitions: 6
    replication_factor: 3
    retention_ms: 2592000000  # 30 days
    cleanup_policy: delete
    schema: "schemas/alert-event.avsc"

  - name: platform.users.v1
    partitions: 6
    replication_factor: 3
    retention_ms: -1  # infinite retention
    cleanup_policy: compact
    schema: "schemas/user-event.avsc"
```

## Schema Registry

All events must conform to an Avro schema registered in the Confluent Schema Registry. The registry enforces backward compatibility by default, ensuring that schema evolution does not break existing consumers.

```json
{
  "type": "record",
  "name": "DeploymentEvent",
  "namespace": "io.acme.platform.events",
  "fields": [
    { "name": "event_id", "type": "string" },
    { "name": "event_type", "type": { "type": "enum", "name": "DeploymentEventType", "symbols": ["STARTED", "COMPLETED", "FAILED", "ROLLED_BACK"] } },
    { "name": "timestamp", "type": { "type": "long", "logicalType": "timestamp-millis" } },
    { "name": "service_name", "type": "string" },
    { "name": "environment", "type": "string" },
    { "name": "version", "type": "string" },
    { "name": "initiated_by", "type": "string" },
    { "name": "duration_ms", "type": ["null", "long"], "default": null },
    { "name": "error_message", "type": ["null", "string"], "default": null }
  ]
}
```

Schema compatibility checks are run in CI before merging schema changes.

## Consumer Groups

Consumers are organized into consumer groups for parallel processing. Each partition in a topic is assigned to exactly one consumer in the group, ensuring ordered processing per partition.

```yaml
consumer_groups:
  - name: notification-engine
    topics:
      - platform.deployments.v1
      - platform.alerts.v1
      - platform.users.v1
    auto_offset_reset: earliest
    max_poll_records: 100
    session_timeout_ms: 30000

  - name: audit-logger
    topics:
      - platform.gateway-requests.v1
      - platform.deployments.v1
    auto_offset_reset: earliest
    max_poll_records: 500
    session_timeout_ms: 30000
```

Consumer lag is monitored via Burrow and alerts fire when lag exceeds configured thresholds.

## Producing Events

Services publish events through a shared producer library that handles serialization, schema validation, and delivery confirmation:

```typescript
import { EventProducer } from "@acme/event-bus-client";

const producer = new EventProducer({
  brokers: ["kafka-1:9092", "kafka-2:9092", "kafka-3:9092"],
  schemaRegistry: "http://schema-registry:8081",
  acks: "all",
  idempotent: true,
});

await producer.publish("platform.deployments.v1", {
  event_id: "evt-abc123",
  event_type: "COMPLETED",
  timestamp: Date.now(),
  service_name: "payment-service",
  environment: "production",
  version: "2.4.1",
  initiated_by: "user:u-abc123",
  duration_ms: 142000,
  error_message: null,
});
```

The producer is configured with `acks: all` and `idempotent: true` to guarantee exactly-once delivery semantics.

## Dead Letter Queue

Events that fail processing after the configured retry attempts are routed to a dead-letter topic:

```
<original-topic>.dlq

Example:
  platform.deployments.v1.dlq
```

Dead-letter events include metadata about the failure:

```json
{
  "original_topic": "platform.deployments.v1",
  "original_partition": 3,
  "original_offset": 142857,
  "failure_reason": "Schema validation failed: missing required field 'service_name'",
  "failure_timestamp": "2026-09-15T14:23:01Z",
  "retry_count": 3,
  "original_event": { "..." : "..." }
}
```

Operators can replay dead-letter events after fixing the underlying issue using the `acme-event-replay` CLI tool.

## Monitoring and Operations

The Event Bus exposes metrics via JMX, scraped by Prometheus:

- `kafka_server_broker_topic_metrics_messages_in_total` -- Message ingestion rate per topic.
- `kafka_consumer_group_lag` -- Consumer lag per group and partition.
- `kafka_server_replica_manager_under_replicated_partitions` -- Under-replicated partitions (critical alert).
- `kafka_network_request_metrics_total_time_ms` -- Request latency at the broker.

Operational runbooks cover common scenarios:

- **Broker failure** -- Automatic partition reassignment via cruise-control.
- **Consumer lag spike** -- Scale consumer group horizontally or investigate slow processing.
- **Schema incompatibility** -- Roll back the schema change and fix forward.
- **Dead-letter overflow** -- Investigate root cause, fix, and replay.

All operational alerts route through the [Notification Engine](../services/notification-engine.md) for multi-channel delivery to the on-call team.
