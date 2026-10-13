# Notification Engine

The Notification Engine is the Acme Platform's centralized, multi-channel notification delivery system. It provides a unified API for sending messages across email, SMS, push notifications, and webhooks, with built-in template rendering, delivery tracking, retry logic, and per-user channel preferences.

All platform services (Auth Service, Billing, Monitoring, etc.) route their user-facing communications through this engine rather than managing delivery infrastructure independently.

**Current version:** 3.2.0
**Runtime:** Go 1.22 on Alpine Linux
**Message broker:** Apache Kafka 3.6

---

## Architecture

```
  +------------------+     +------------------+     +------------------+
  |  Auth Service    |     |  Billing Service |     |  Monitoring      |
  +--------+---------+     +--------+---------+     +--------+---------+
           |                        |                        |
           +------------------------+------------------------+
                                    |
                          +---------v---------+
                          |   Notification    |
                          |   API (REST/gRPC) |
                          +---------+---------+
                                    |
                     +--------------+--------------+
                     |                             |
           +---------v---------+         +---------v---------+
           |   Kafka Topic     |         |  Template Store   |
           | notifications.send|         |  (PostgreSQL)     |
           +---------+---------+         +-------------------+
                     |
    +----------------+----------------+-----------------+
    |                |                |                  |
+---v--------+ +----v-------+ +------v------+ +--------v-------+
| Email      | | SMS Worker | | Push Worker | | Webhook        |
| Worker     | | (Twilio)   | | (FCM)       | | Dispatcher     |
| (SendGrid) | +----+-------+ +------+------+ +--------+-------+
+---+--------+      |                |                  |
    |                |                |                  |
    +-------+--------+--------+-------+------------------+
            |                 |
   +--------v------+  +------v---------+
   | Delivery Log  |  | Dead-Letter    |
   | (PostgreSQL)  |  | Queue (Kafka)  |
   +---------------+  +----------------+
```

### Component Responsibilities

| Component          | Role                                                        |
|--------------------|-------------------------------------------------------------|
| Notification API   | Validates requests, renders templates, publishes to Kafka   |
| Kafka Topic        | Durable message queue for async, ordered delivery           |
| Template Store     | Versioned Handlebars templates with i18n support            |
| Email Worker       | Consumes email messages and delivers via SendGrid           |
| SMS Worker         | Consumes SMS messages and delivers via Twilio               |
| Push Worker        | Consumes push messages and delivers via FCM                 |
| Webhook Dispatcher | Delivers HTTP callbacks to registered external endpoints    |
| Delivery Log       | Tracks delivery status, timestamps, and error details       |
| Dead-Letter Queue  | Holds messages that exhausted retry attempts                |

---

## Features

### Multi-Channel Delivery
- Email via SendGrid (with SMTP fallback for development)
- SMS via Twilio Programmable Messaging
- Push notifications via Firebase Cloud Messaging (Android, iOS, Web)
- Webhooks to arbitrary HTTPS endpoints with HMAC-SHA256 signing
- See [channels.md](channels.md) for detailed channel configuration

### Template Engine
- Handlebars-based template rendering with dynamic variables
- Localization support (i18n) with per-locale template variants
- Template versioning to prevent breaking changes on active campaigns
- Preview and test-send endpoints for template validation before deployment
- Partials support for shared headers, footers, and reusable blocks

### Delivery Tracking
- Real-time delivery status per notification: `queued`, `processing`, `sent`, `delivered`, `failed`, `bounced`
- Delivery receipts from downstream providers (SendGrid events, Twilio status callbacks, FCM responses)
- Searchable delivery log with filtering by recipient, channel, status, template, and time range
- Webhook event stream for real-time delivery status consumption by other services

### Retry Logic and Delivery Guarantees
- At-least-once delivery semantics via Kafka consumer offsets
- Configurable retry policy: exponential backoff with jitter
- Default: 5 retries over 30 minutes, then dead-letter queue
- Per-channel circuit breakers to avoid cascading failures during provider outages
- Idempotency keys to prevent duplicate deliveries on retry

### Per-User Preferences
- Users can opt in/out of channels and notification categories
- Quiet hours enforcement (suppress non-critical notifications during configured windows)
- Preference API for self-service management via the platform UI
- Category-level granularity (e.g., receive billing by email only, security alerts on all channels)

---

## Getting Started

### Prerequisites

- Docker 24+ and Docker Compose v2
- Apache Kafka 3.6+ (or use the bundled Docker image)
- PostgreSQL 16+ (or use the bundled Docker image)
- SendGrid API key (free tier works for development)
- Access to the Acme Platform container registry

### Quick Start with Docker Compose

```bash
# Clone the repository
git clone https://github.com/acme-platform/notification-engine.git
cd notification-engine

# Copy environment template and fill in your API keys
cp .env.example .env

# Start all dependencies (Kafka, PostgreSQL, Redis)
docker compose up -d

# Run database migrations
docker compose exec notification-engine ./migrate up

# Seed default templates
docker compose exec notification-engine ./seed templates

# Verify the service is healthy
curl http://localhost:5000/health
```

### Local Development

```bash
# Install Go dependencies
go mod download

# Run locally with hot-reload (requires Air)
air

# Run the full test suite
go test ./...

# Run integration tests (requires Docker services running)
go test -tags=integration ./...

# Lint
golangci-lint run
```

---

## API Endpoints

### Notification Operations

| Method | Path                                | Description                           |
|--------|-------------------------------------|---------------------------------------|
| POST   | `/v1/notifications/send`            | Send a notification                   |
| GET    | `/v1/notifications/{id}`            | Get notification details              |
| GET    | `/v1/notifications/{id}/status`     | Get delivery status across channels   |
| POST   | `/v1/notifications/batch`           | Send batch notifications (up to 1000) |
| GET    | `/v1/notifications`                 | List notifications (paginated)        |
| DELETE | `/v1/notifications/{id}`            | Cancel a scheduled notification       |

### Template Management

| Method | Path                                | Description                           |
|--------|-------------------------------------|---------------------------------------|
| GET    | `/v1/templates`                     | List all templates                    |
| POST   | `/v1/templates`                     | Create a new template                 |
| GET    | `/v1/templates/{slug}`              | Get template by slug                  |
| PUT    | `/v1/templates/{slug}`              | Update a template                     |
| DELETE | `/v1/templates/{slug}`              | Archive a template                    |
| POST   | `/v1/templates/{slug}/preview`      | Render a template preview             |
| POST   | `/v1/templates/{slug}/test-send`    | Send a test notification              |
| GET    | `/v1/templates/{slug}/versions`     | List template version history         |

### User Preferences

| Method | Path                                | Description                           |
|--------|-------------------------------------|---------------------------------------|
| GET    | `/v1/preferences/{user_id}`         | Get user notification preferences     |
| PUT    | `/v1/preferences/{user_id}`         | Update user preferences               |
| DELETE | `/v1/preferences/{user_id}`         | Reset to default preferences          |

### Service Endpoints

| Method | Path          | Description          |
|--------|---------------|----------------------|
| GET    | `/health`     | Liveness probe       |
| GET    | `/ready`      | Readiness probe      |
| GET    | `/metrics`    | Prometheus metrics   |

---

## Code Examples

### Send a Notification

```bash
curl -X POST https://notifications.acme-platform.io/v1/notifications/send \
  -H "Authorization: Bearer <service_token>" \
  -H "Content-Type: application/json" \
  -d '{
    "template": "password-reset",
    "recipient": {
      "user_id": "usr_8f3a2c91e4b7",
      "email": "jdoe@example.com",
      "phone": "+15551234567"
    },
    "channels": ["email", "sms"],
    "priority": "high",
    "idempotency_key": "pwd-reset-usr_8f3a2c91-20261013",
    "data": {
      "reset_link": "https://app.acme-platform.io/reset?token=abc123",
      "expiry_minutes": 30,
      "user_name": "Jordan Doe"
    }
  }'
```

**Response:**

```json
{
  "notification_id": "ntf_r4s5t6u7v8w9",
  "status": "queued",
  "channels": [
    { "channel": "email", "status": "queued", "provider": "sendgrid" },
    { "channel": "sms", "status": "queued", "provider": "twilio" }
  ],
  "created_at": "2026-10-13T14:30:00Z"
}
```

### Check Delivery Status

```bash
curl https://notifications.acme-platform.io/v1/notifications/ntf_r4s5t6u7v8w9/status \
  -H "Authorization: Bearer <service_token>"
```

**Response:**

```json
{
  "notification_id": "ntf_r4s5t6u7v8w9",
  "channels": [
    {
      "channel": "email",
      "status": "delivered",
      "provider": "sendgrid",
      "provider_message_id": "sg_abc123def456",
      "sent_at": "2026-10-13T14:30:02Z",
      "delivered_at": "2026-10-13T14:30:04Z",
      "opened_at": "2026-10-13T14:35:12Z"
    },
    {
      "channel": "sms",
      "status": "delivered",
      "provider": "twilio",
      "provider_message_id": "SM1234567890abcdef",
      "sent_at": "2026-10-13T14:30:01Z",
      "delivered_at": "2026-10-13T14:30:03Z"
    }
  ]
}
```

### Send a Batch Notification

```bash
curl -X POST https://notifications.acme-platform.io/v1/notifications/batch \
  -H "Authorization: Bearer <service_token>" \
  -H "Content-Type: application/json" \
  -d '{
    "template": "weekly-digest",
    "channel": "email",
    "recipients": [
      {
        "user_id": "usr_001",
        "email": "alice@example.com",
        "data": { "user_name": "Alice", "unread_count": 5 }
      },
      {
        "user_id": "usr_002",
        "email": "bob@example.com",
        "data": { "user_name": "Bob", "unread_count": 12 }
      }
    ],
    "scheduled_at": "2026-10-14T08:00:00Z"
  }'
```

### Create a Template

```bash
curl -X POST https://notifications.acme-platform.io/v1/templates \
  -H "Authorization: Bearer <admin_token>" \
  -H "Content-Type: application/json" \
  -d '{
    "slug": "deployment-complete",
    "name": "Deployment Complete",
    "category": "system",
    "channels": {
      "email": {
        "subject": "Deployment {{deployment_id}} Complete",
        "body_html": "<h1>Deployment Successful</h1><p>Service <b>{{service_name}}</b> was deployed to <b>{{environment}}</b> at {{formatDate timestamp \"Jan 2, 2006 3:04 PM\"}}.</p><p>Build SHA: <code>{{build_sha}}</code></p>{{#if release_notes}}<h2>Release Notes</h2><p>{{release_notes}}</p>{{/if}}",
        "body_text": "Deployment Successful\n\nService {{service_name}} was deployed to {{environment}} at {{formatDate timestamp \"Jan 2, 2006 3:04 PM\"}}.\n\nBuild SHA: {{build_sha}}"
      },
      "push": {
        "title": "Deployment Complete",
        "body": "{{service_name}} deployed to {{environment}}"
      }
    },
    "variables_schema": {
      "deployment_id": { "type": "string", "required": true },
      "service_name": { "type": "string", "required": true },
      "environment": { "type": "string", "required": true },
      "build_sha": { "type": "string", "required": true },
      "timestamp": { "type": "string", "format": "date-time", "required": true },
      "release_notes": { "type": "string", "required": false }
    }
  }'
```

### Using the Go SDK

```go
package main

import (
    "context"
    "log"

    notif "github.com/acme-platform/notification-engine/sdk-go"
)

func main() {
    client := notif.NewClient(
        notif.WithBaseURL("https://notifications.acme-platform.io"),
        notif.WithToken("svc_token_abc123"),
    )

    resp, err := client.Send(context.Background(), &notif.SendRequest{
        Template: "order-confirmation",
        Recipient: notif.Recipient{
            UserID: "usr_8f3a2c91e4b7",
            Email:  "jdoe@example.com",
        },
        Channels: []string{"email"},
        Data: map[string]interface{}{
            "order_id":    "ORD-20261013-001",
            "total":       "$149.99",
            "items_count": 3,
        },
    })
    if err != nil {
        log.Fatalf("failed to send notification: %v", err)
    }

    log.Printf("Notification queued: %s", resp.NotificationID)
}
```

---

## Template System

Templates use [Handlebars](https://handlebarsjs.com/) syntax for variable interpolation and conditional logic:

```handlebars
<h1>Hello, {{user_name}}!</h1>

{{#if items}}
  <table>
    <tr><th>Item</th><th>Price</th></tr>
    {{#each items}}
      <tr><td>{{this.name}}</td><td>${{this.price}}</td></tr>
    {{/each}}
  </table>
  <p><strong>Total: ${{total}}</strong></p>
{{else}}
  <p>No items to display.</p>
{{/if}}

{{> email_footer locale=locale unsubscribe_url=unsubscribe_url}}
```

### Built-in Helpers

| Helper         | Example                              | Output                  |
|----------------|--------------------------------------|-------------------------|
| `formatDate`   | `{{formatDate timestamp "MMM D"}}`   | `Oct 13`                |
| `uppercase`    | `{{uppercase status}}`               | `ACTIVE`                |
| `lowercase`    | `{{lowercase channel}}`              | `email`                 |
| `pluralize`    | `{{pluralize count "item" "items"}}` | `3 items`               |
| `truncate`     | `{{truncate description 100}}`       | First 100 chars + ...   |
| `currency`     | `{{currency amount "USD"}}`          | `$1,234.56`             |
| `relativeTime` | `{{relativeTime timestamp}}`         | `2 hours ago`           |

### Template Directory Structure

```
templates/
  password-reset/
    meta.yaml           # slug, category, variables schema
    email/
      en.html           # English HTML version
      en.txt            # English plain text fallback
      es.html           # Spanish HTML version
      es.txt
    sms/
      en.txt
      es.txt
    push/
      en.json
      es.json
```

---

## Configuration Reference

### Environment Variables

| Variable                        | Default              | Description                                |
|---------------------------------|----------------------|--------------------------------------------|
| `NOTIF_PORT`                    | `5000`               | HTTP listen port                           |
| `NOTIF_GRPC_PORT`              | `5001`               | gRPC listen port                           |
| `NOTIF_DB_URL`                  | (required)           | PostgreSQL connection string               |
| `NOTIF_KAFKA_BROKERS`           | (required)           | Kafka broker addresses                     |
| `NOTIF_KAFKA_TOPIC`             | `notifications.send` | Primary notification topic                 |
| `NOTIF_KAFKA_DLQ_TOPIC`        | `notifications.dlq`  | Dead-letter queue topic                    |
| `NOTIF_RETRY_MAX`              | `5`                  | Maximum delivery retries                   |
| `NOTIF_RETRY_BACKOFF_BASE_MS`  | `1000`               | Base backoff interval in milliseconds      |
| `NOTIF_RETRY_BACKOFF_MAX_MS`   | `1800000`            | Maximum backoff interval (30 min)          |
| `NOTIF_EMAIL_PROVIDER`          | `sendgrid`           | Email provider: `sendgrid` or `smtp`       |
| `NOTIF_SMS_PROVIDER`            | `twilio`             | SMS provider                               |
| `NOTIF_PUSH_PROVIDER`           | `fcm`                | Push provider                              |
| `SENDGRID_API_KEY`              | (required)           | SendGrid API key                           |
| `TWILIO_ACCOUNT_SID`            | (required)           | Twilio account SID                         |
| `TWILIO_AUTH_TOKEN`             | (required)           | Twilio auth token                          |
| `NOTIF_QUIET_HOURS_ENABLED`     | `true`               | Enable quiet hours enforcement             |
| `NOTIF_TEMPLATE_CACHE_TTL`      | `300`                | Template cache TTL in seconds              |
| `NOTIF_LOG_LEVEL`               | `info`               | Log level: debug, info, warn, error        |
| `NOTIF_METRICS_ENABLED`         | `true`               | Expose Prometheus metrics endpoint         |

---

## Delivery Guarantees and Retry Logic

The Notification Engine guarantees **at-least-once delivery** to the downstream provider (SendGrid, Twilio, FCM). End-to-end delivery to the final recipient depends on the provider's own guarantees and the recipient's device/mailbox.

### Retry Policy

```
Attempt 1: immediate
Attempt 2: 1s   + jitter (0-500ms)
Attempt 3: 4s   + jitter (0-2s)
Attempt 4: 16s  + jitter (0-8s)
Attempt 5: 64s  + jitter (0-30s)
--> Dead-Letter Queue
```

After all retries are exhausted, the message is published to the dead-letter queue topic (`notifications.dlq`) for manual investigation or automated reprocessing.

### Dead-Letter Queue Operations

```bash
# List DLQ entries
curl https://notifications.acme-platform.io/v1/dlq?channel=email&limit=50 \
  -H "Authorization: Bearer <admin_token>"

# Replay a single entry
curl -X POST https://notifications.acme-platform.io/v1/dlq/dlq_abc123/replay \
  -H "Authorization: Bearer <admin_token>"

# Replay all entries matching a filter
curl -X POST https://notifications.acme-platform.io/v1/dlq/replay-batch \
  -H "Authorization: Bearer <admin_token>" \
  -H "Content-Type: application/json" \
  -d '{ "channel": "email", "error_code": "provider_timeout", "since": "2026-10-12T00:00:00Z" }'
```

### Circuit Breaker

Each channel worker implements a circuit breaker pattern:

| State      | Behavior                                                                 |
|------------|--------------------------------------------------------------------------|
| **Closed** | Normal operation; requests flow through to the provider                  |
| **Open**   | After 5 consecutive failures in 60s, all requests short-circuit to DLQ   |
| **Half-Open** | After 30s cooldown, one probe request is allowed to test recovery    |

Circuit breaker state is shared across worker replicas via Redis.

---

## Observability

### Prometheus Metrics

| Metric                                  | Type      | Labels                          |
|-----------------------------------------|-----------|---------------------------------|
| `notif_deliveries_total`                | Counter   | channel, status, provider       |
| `notif_delivery_duration_seconds`       | Histogram | channel, provider               |
| `notif_retry_attempts_total`            | Counter   | channel, attempt_number         |
| `notif_dlq_messages_total`              | Counter   | channel, error_code             |
| `notif_template_render_duration_seconds`| Histogram | template_slug                   |
| `notif_circuit_breaker_state`           | Gauge     | channel (0=closed, 1=open, 2=half) |

### Structured Logging

All logs are JSON-formatted and include correlation IDs for distributed tracing:

```json
{
  "level": "info",
  "msg": "notification delivered",
  "notification_id": "ntf_r4s5t6u7v8w9",
  "channel": "email",
  "provider": "sendgrid",
  "duration_ms": 342,
  "trace_id": "abc123def456",
  "timestamp": "2026-10-13T14:30:04Z"
}
```

---

## Related Documentation

- [Channel Configuration](channels.md) -- email, SMS, push, webhook setup
- [Auth Service](../auth-service/README.md) -- service token authentication
- [Event Bus](../event-bus/README.md) -- Kafka topic configuration
- [API Gateway](../api-gateway/README.md) -- external request routing
- [SDK Client](../sdk-client/README.md) -- client library documentation
