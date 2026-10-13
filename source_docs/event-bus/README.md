# Acme Platform Event Bus

## Overview

The Acme Platform Event Bus is the central asynchronous messaging backbone for all microservices in the Acme ecosystem. Built on **Apache Kafka 3.6**, it enables decoupled, reliable, and scalable communication between services such as the Order Service, Inventory Service, Notification Service, Billing Service, and Analytics Pipeline.

All inter-service events flow through the event bus unless they require synchronous request-response semantics. See [architecture.md](architecture.md) for guidance on when to use events versus HTTP.

---

## Architecture Overview

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│ Order Service │     │ Inventory    │     │ Notification │
│              │     │ Service      │     │ Service      │
└──────┬───────┘     └──────┬───────┘     └──────┬───────┘
       │                    │                    │
       ▼                    ▼                    ▼
┌─────────────────────────────────────────────────────────┐
│                    Kafka Cluster                        │
│  ┌─────────┐  ┌─────────┐  ┌─────────┐  ┌───────────┐ │
│  │ Broker 1│  │ Broker 2│  │ Broker 3│  │ Schema    │ │
│  │         │  │         │  │         │  │ Registry  │ │
│  └─────────┘  └─────────┘  └─────────┘  └───────────┘ │
└─────────────────────────────────────────────────────────┘
       │                    │                    │
       ▼                    ▼                    ▼
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│ Billing      │     │ Analytics    │     │ Audit Log    │
│ Service      │     │ Pipeline     │     │ Service      │
└──────────────┘     └──────────────┘     └──────────────┘
```

---

## Kafka Cluster Setup

### Broker Configuration

The Acme Platform runs a **3-broker Kafka cluster** in production, deployed via Helm on Kubernetes. Each broker runs on a dedicated node with the following resource profile:

| Resource   | Value          |
|------------|----------------|
| CPU        | 4 cores        |
| Memory     | 16 GB          |
| Disk       | 500 GB SSD     |
| Replication| 3 (min.insync.replicas = 2) |

### Key Broker Settings

```properties
# server.properties (production)
broker.id=1
num.network.threads=8
num.io.threads=16
log.retention.hours=168
log.segment.bytes=1073741824
log.retention.check.interval.ms=300000
num.partitions=12
default.replication.factor=3
min.insync.replicas=2
unclean.leader.election.enable=false
auto.create.topics.enable=false
message.max.bytes=10485760
```

### ZooKeeper Migration

As of Q3 2025, we completed the migration from ZooKeeper to KRaft mode. All metadata is now managed by the Kafka controller quorum. The three brokers double as controllers using the `process.roles=broker,controller` configuration.

---

## Topic Management

### Naming Convention

All topics follow the pattern:

```
acme.<domain>.<entity>.<event-type>
```

Examples:
- `acme.orders.order.created`
- `acme.orders.order.fulfilled`
- `acme.inventory.stock.updated`
- `acme.billing.invoice.generated`
- `acme.notifications.email.sent`

### Topic Registry

Topics are declared in the `topics.yaml` manifest and provisioned via CI/CD:

```yaml
# topics.yaml
topics:
  - name: acme.orders.order.created
    partitions: 12
    replication_factor: 3
    config:
      retention.ms: 604800000        # 7 days
      cleanup.policy: delete
      max.message.bytes: 1048576     # 1 MB

  - name: acme.orders.order.fulfilled
    partitions: 12
    replication_factor: 3
    config:
      retention.ms: 604800000
      cleanup.policy: delete

  - name: acme.inventory.stock.updated
    partitions: 24
    replication_factor: 3
    config:
      retention.ms: 259200000        # 3 days
      cleanup.policy: delete

  - name: acme.billing.invoice.generated
    partitions: 6
    replication_factor: 3
    config:
      retention.ms: 2592000000       # 30 days
      cleanup.policy: compact,delete
```

### Creating a New Topic

1. Add the topic definition to `topics.yaml`
2. Open a pull request and get approval from the Platform team
3. Merge triggers the `kafka-topic-provisioner` pipeline
4. The provisioner runs `kafka-topics.sh --create` with the declared settings

**Never create topics manually in production.** The `auto.create.topics.enable=false` setting enforces this.

---

## Consumer Groups

Each consuming service registers a consumer group named after its service and purpose:

| Consumer Group                     | Topics Consumed                         | Service          |
|------------------------------------|-----------------------------------------|------------------|
| `billing-order-processor`          | `acme.orders.order.created`             | Billing Service  |
| `inventory-order-handler`          | `acme.orders.order.created`             | Inventory Service|
| `notification-dispatcher`          | `acme.orders.order.fulfilled`           | Notification Svc |
| `analytics-event-ingester`         | `acme.orders.*`, `acme.inventory.*`     | Analytics Pipeline|
| `audit-log-writer`                 | `acme.*.*.created`, `acme.*.*.deleted`  | Audit Log Service|

### Consumer Configuration

```python
from confluent_kafka import Consumer

consumer_config = {
    'bootstrap.servers': 'kafka-broker-1:9092,kafka-broker-2:9092,kafka-broker-3:9092',
    'group.id': 'billing-order-processor',
    'auto.offset.reset': 'earliest',
    'enable.auto.commit': False,
    'max.poll.interval.ms': 300000,
    'session.timeout.ms': 45000,
    'heartbeat.interval.ms': 15000,
    'max.poll.records': 500,
    'isolation.level': 'read_committed',
}

consumer = Consumer(consumer_config)
consumer.subscribe(['acme.orders.order.created'])
```

### Offset Management

All services use **manual offset commits** (`enable.auto.commit=False`). Offsets are committed only after successful processing to ensure at-least-once delivery. See [processing.md](processing.md) for details on idempotency.

---

## Schema Registry

The Acme Platform uses the **Confluent Schema Registry** to manage event schemas. All events are serialized using **Avro**.

### Schema Evolution Rules

- **BACKWARD** compatibility is the default for all topics
- New fields must have default values
- Fields cannot be removed without a deprecation cycle (minimum 2 release cycles)
- Renaming fields is not allowed; add a new field and deprecate the old one

### Example Schema

```json
{
  "type": "record",
  "name": "OrderCreated",
  "namespace": "com.acme.orders.events",
  "fields": [
    {"name": "event_id", "type": "string"},
    {"name": "event_timestamp", "type": "long", "logicalType": "timestamp-millis"},
    {"name": "order_id", "type": "string"},
    {"name": "customer_id", "type": "string"},
    {"name": "items", "type": {"type": "array", "items": {
      "type": "record",
      "name": "OrderItem",
      "fields": [
        {"name": "product_id", "type": "string"},
        {"name": "quantity", "type": "int"},
        {"name": "unit_price_cents", "type": "long"}
      ]
    }}},
    {"name": "total_amount_cents", "type": "long"},
    {"name": "currency", "type": "string", "default": "USD"},
    {"name": "metadata", "type": ["null", {"type": "map", "values": "string"}], "default": null}
  ]
}
```

### Registering a Schema

```bash
# Register schema via CI pipeline
curl -X POST -H "Content-Type: application/vnd.schemaregistry.v1+json" \
  --data @schemas/order-created.avsc \
  http://schema-registry:8081/subjects/acme.orders.order.created-value/versions
```

---

## Producing Events

### Standard Producer Configuration

```python
from confluent_kafka import Producer
from confluent_kafka.serialization import SerializationContext, MessageField
from confluent_kafka.schema_registry.avro import AvroSerializer

producer_config = {
    'bootstrap.servers': 'kafka-broker-1:9092,kafka-broker-2:9092,kafka-broker-3:9092',
    'acks': 'all',
    'retries': 5,
    'retry.backoff.ms': 100,
    'max.in.flight.requests.per.connection': 5,
    'enable.idempotence': True,
    'compression.type': 'lz4',
    'linger.ms': 10,
    'batch.size': 65536,
}

producer = Producer(producer_config)

def publish_order_created(order: dict):
    """Publish an OrderCreated event to the event bus."""
    producer.produce(
        topic='acme.orders.order.created',
        key=order['order_id'],
        value=avro_serializer(order, SerializationContext('acme.orders.order.created', MessageField.VALUE)),
        on_delivery=delivery_callback,
    )
    producer.flush()

def delivery_callback(err, msg):
    if err:
        logger.error(f"Event delivery failed: {err}")
        metrics.increment('event_bus.publish.failure', tags={'topic': msg.topic()})
    else:
        logger.info(f"Event delivered to {msg.topic()} [{msg.partition()}] @ offset {msg.offset()}")
        metrics.increment('event_bus.publish.success', tags={'topic': msg.topic()})
```

### Partitioning Strategy

Events are partitioned by their **entity ID** (e.g., `order_id`, `customer_id`). This guarantees ordering for all events related to the same entity. Custom partitioners are discouraged; use the default murmur2 hash on the message key.

---

## Monitoring and Alerting

### Key Metrics

All Kafka metrics are exported via JMX to Prometheus and visualized in Grafana:

| Metric                                    | Alert Threshold     | Description                           |
|-------------------------------------------|---------------------|---------------------------------------|
| `kafka_consumer_group_lag`                | > 10,000            | Consumer lag per partition             |
| `kafka_broker_under_replicated_partitions`| > 0                 | Under-replicated partitions            |
| `kafka_broker_active_controller_count`    | != 1                | Active controller count                |
| `kafka_producer_request_latency_avg`      | > 500ms             | Average producer request latency       |
| `kafka_consumer_fetch_latency_avg`        | > 1000ms            | Average consumer fetch latency         |
| `kafka_topic_partition_log_size`          | > 400 GB            | Disk usage per topic                   |

### Grafana Dashboards

- **Event Bus Overview**: Cluster health, throughput, consumer lag summary
- **Per-Service Consumer Health**: Lag, commit rate, rebalance frequency per consumer group
- **Schema Registry Health**: Schema count, compatibility check failures, registry latency
- **Dead Letter Queue Monitor**: DLQ message rate, oldest unprocessed message age

### Alerting Rules

```yaml
# prometheus-rules.yaml
groups:
  - name: kafka-alerts
    rules:
      - alert: HighConsumerLag
        expr: kafka_consumer_group_lag > 10000
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "Consumer group {{ $labels.group }} has high lag on {{ $labels.topic }}"

      - alert: UnderReplicatedPartitions
        expr: kafka_broker_under_replicated_partitions > 0
        for: 2m
        labels:
          severity: critical
        annotations:
          summary: "Broker {{ $labels.broker_id }} has under-replicated partitions"
```

---

## Local Development

For local development, use the Docker Compose setup in `infra/docker/`:

```bash
cd infra/docker
docker-compose -f docker-compose.kafka.yml up -d
```

This starts a single-broker Kafka instance with Schema Registry and a Kafka UI at `http://localhost:8080`.

### Useful CLI Commands

```bash
# List topics
kafka-topics.sh --bootstrap-server localhost:9092 --list

# Describe a topic
kafka-topics.sh --bootstrap-server localhost:9092 --describe --topic acme.orders.order.created

# Consume from beginning
kafka-console-consumer.sh --bootstrap-server localhost:9092 \
  --topic acme.orders.order.created --from-beginning

# View consumer group lag
kafka-consumer-groups.sh --bootstrap-server localhost:9092 \
  --describe --group billing-order-processor
```

---

## Related Documentation

- [Event Processing Patterns](processing.md) -- consumer patterns, error handling, dead letters
- [Event-Driven Architecture](architecture.md) -- EDA overview, patterns, event sourcing, CQRS
- [Acme Platform Service Catalog](../service-catalog/README.md) -- all microservices
- [Infrastructure Runbooks](../runbooks/kafka-ops.md) -- operational procedures
