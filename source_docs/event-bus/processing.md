# Event Processing Patterns

## Overview

This document describes the standard patterns for consuming and processing events from the Acme Platform Event Bus. All services that consume Kafka events must follow these patterns to ensure reliability, correctness, and observability.

---

## Consumer Loop Pattern

Every Acme consumer follows the **poll-process-commit** loop with explicit offset management:

```python
import signal
from confluent_kafka import Consumer, KafkaException

class EventProcessor:
    def __init__(self, config: dict, handler):
        self.consumer = Consumer(config)
        self.handler = handler
        self.running = True
        signal.signal(signal.SIGTERM, self._shutdown)

    def _shutdown(self, signum, frame):
        self.running = False

    def run(self, topics: list[str]):
        self.consumer.subscribe(topics)
        try:
            while self.running:
                msg = self.consumer.poll(timeout=1.0)
                if msg is None:
                    continue
                if msg.error():
                    self._handle_kafka_error(msg)
                    continue

                try:
                    self.handler.process(msg)
                    self.consumer.commit(msg)
                except RetryableError as e:
                    self._retry_with_backoff(msg, e)
                except NonRetryableError as e:
                    self._send_to_dead_letter(msg, e)
                    self.consumer.commit(msg)
        finally:
            self.consumer.close()
```

### Key Principles

1. **Never auto-commit offsets.** Offsets are committed only after successful processing.
2. **Distinguish retryable from non-retryable errors.** Network timeouts retry; schema violations go to the dead letter queue.
3. **Always close the consumer on shutdown.** This triggers a clean rebalance so other consumers pick up partitions immediately.

---

## Error Handling Strategy

### Error Classification

| Error Type        | Examples                                  | Action              |
|-------------------|-------------------------------------------|---------------------|
| Retryable         | Database timeout, HTTP 503, lock conflict | Retry with backoff  |
| Non-retryable     | Schema mismatch, validation failure       | Dead letter queue   |
| Poison pill       | Corrupt message, deserialization failure  | Dead letter + alert |
| Infrastructure    | Broker disconnect, disk full              | Pause and alert     |

### Retry with Exponential Backoff

```python
import time

MAX_RETRIES = 5
BASE_DELAY_SECONDS = 0.5

def _retry_with_backoff(self, msg, error):
    for attempt in range(1, MAX_RETRIES + 1):
        delay = BASE_DELAY_SECONDS * (2 ** (attempt - 1))
        time.sleep(min(delay, 30))  # cap at 30 seconds

        try:
            self.handler.process(msg)
            self.consumer.commit(msg)
            metrics.increment('event.retry.success', tags={'attempt': attempt})
            return
        except RetryableError:
            metrics.increment('event.retry.attempt', tags={'attempt': attempt})
            continue

    # All retries exhausted
    self._send_to_dead_letter(msg, error)
    self.consumer.commit(msg)
    metrics.increment('event.retry.exhausted')
```

---

## Dead Letter Queue (DLQ)

When a message cannot be processed after all retries, it is published to a dead letter topic. DLQ topics follow the naming convention:

```
acme.dlq.<original-topic-name>
```

For example, failures from `acme.orders.order.created` land in `acme.dlq.acme.orders.order.created`.

### DLQ Message Envelope

The DLQ message includes the original event plus failure metadata:

```json
{
  "original_topic": "acme.orders.order.created",
  "original_partition": 7,
  "original_offset": 148203,
  "original_key": "ord-29f81a",
  "original_value": "<base64-encoded original message>",
  "original_timestamp": "2025-09-14T08:23:11.442Z",
  "error_message": "Validation failed: customer_id is required",
  "error_class": "NonRetryableError",
  "consumer_group": "billing-order-processor",
  "service_name": "billing-service",
  "service_version": "2.14.0",
  "failure_timestamp": "2025-09-14T08:23:11.887Z",
  "retry_count": 5
}
```

### DLQ Reprocessing

The `dlq-reprocessor` CLI tool allows operators to replay dead-lettered messages:

```bash
# List DLQ messages for a topic
dlq-reprocessor list --topic acme.dlq.acme.orders.order.created --since 2025-09-14

# Replay all messages from the last 24 hours
dlq-reprocessor replay --topic acme.dlq.acme.orders.order.created --since 24h

# Replay a specific message by offset
dlq-reprocessor replay --topic acme.dlq.acme.orders.order.created --offset 42
```

---

## Idempotency

Because the event bus guarantees **at-least-once delivery**, consumers must be idempotent. A message may be delivered more than once due to consumer rebalances, network issues, or retries.

### Idempotency via Deduplication Table

The standard approach is to track processed event IDs in a deduplication table:

```sql
CREATE TABLE processed_events (
    event_id      VARCHAR(64) PRIMARY KEY,
    topic         VARCHAR(255) NOT NULL,
    processed_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    consumer_group VARCHAR(128) NOT NULL
);

-- TTL cleanup: remove entries older than 7 days
-- Runs nightly via pg_cron
DELETE FROM processed_events WHERE processed_at < NOW() - INTERVAL '7 days';
```

```python
def process(self, msg):
    event = deserialize(msg)
    event_id = event['event_id']

    if self.db.exists('processed_events', event_id=event_id):
        logger.info(f"Duplicate event {event_id}, skipping")
        metrics.increment('event.duplicate.skipped')
        return

    with self.db.transaction() as tx:
        # Business logic
        self._apply_business_logic(event)
        # Record as processed in the same transaction
        tx.insert('processed_events', event_id=event_id, topic=msg.topic(),
                   consumer_group=self.group_id)
```

### Alternative: Database Upserts

For simpler cases, use database upserts (INSERT ON CONFLICT) so that reprocessing the same event produces the same result naturally, without a separate deduplication table.

---

## Ordering Guarantees

Kafka guarantees ordering **within a partition**. Since Acme events are keyed by entity ID, all events for a given entity arrive in order to the same partition.

### When Ordering Matters

- **Order lifecycle events**: `order.created` -> `order.paid` -> `order.shipped` -> `order.delivered` must be processed in sequence for a given order.
- **Inventory adjustments**: Stock increments and decrements for the same SKU must be applied in order.

### When Ordering Does Not Matter

- **Notifications**: Sending an email for order confirmation and a separate email for shipment are independent.
- **Analytics ingestion**: Events are aggregated in batch windows; arrival order within a window is irrelevant.

### Handling Out-of-Order Events

If a consumer receives an event that seems out of order (e.g., `order.shipped` before `order.paid`), use the following strategy:

1. Check the event timestamp against the entity's last known state.
2. If the event is older than the current state, discard it (idempotent skip).
3. If the entity is in an unexpected state, enqueue the event in a **retry buffer** with a short delay and reprocess after the expected preceding event arrives.
4. If the preceding event does not arrive within the timeout (default: 60 seconds), send to the DLQ with an `out-of-order` error classification.

---

## Batch Processing

For high-throughput consumers like the Analytics Pipeline, batch processing is preferred:

```python
def run_batch(self, topics: list[str], batch_size: int = 500):
    self.consumer.subscribe(topics)
    while self.running:
        messages = self.consumer.consume(num_messages=batch_size, timeout=5.0)
        if not messages:
            continue

        successful = []
        for msg in messages:
            if msg.error():
                continue
            try:
                self.handler.process(msg)
                successful.append(msg)
            except Exception as e:
                self._send_to_dead_letter(msg, e)

        if successful:
            self.consumer.commit(message=successful[-1])
            metrics.gauge('event.batch.size', len(successful))
```

---

## Observability

Every consumer emits the following standard metrics:

| Metric                          | Type      | Description                              |
|---------------------------------|-----------|------------------------------------------|
| `event.processed.total`         | Counter   | Total events successfully processed      |
| `event.processing.duration_ms`  | Histogram | Time to process a single event           |
| `event.retry.attempt`           | Counter   | Retry attempts by attempt number         |
| `event.retry.exhausted`         | Counter   | Events that exhausted all retries        |
| `event.duplicate.skipped`       | Counter   | Duplicate events detected and skipped    |
| `event.dlq.sent`                | Counter   | Events sent to dead letter queue         |
| `event.batch.size`              | Gauge     | Batch size for batch consumers           |

Structured logs follow the format:

```json
{
  "level": "INFO",
  "service": "billing-service",
  "consumer_group": "billing-order-processor",
  "event_id": "evt-9a3c1f",
  "topic": "acme.orders.order.created",
  "partition": 7,
  "offset": 148203,
  "processing_time_ms": 42,
  "message": "Event processed successfully"
}
```
