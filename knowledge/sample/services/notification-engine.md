---
id: notification-engine
title: Notification Engine
type: System
status: stable
tags: [notifications, messaging, async]
links:
  - target: event-bus
    relation: consumes-from
    label: Event consumption
  - target: notification-channels
    relation: dispatches-to
    label: Channel dispatch
  - target: sdk-client
    relation: used-by
    label: SDK notification access
  - target: api-gateway
    relation: registered-with
    label: API registration
sources:
  - id: notif-readme
    resource: /source_docs/notification-engine/README.md
    title: Notification Engine README
generated:
  by: "human:author"
  at: "2026-10-01T00:00:00Z"
---

# Notification Engine

The Notification Engine is the central notification delivery system for the Acme Platform. It consumes events from the [Event Bus](../services/event-bus.md), resolves recipient preferences, renders notification templates, and dispatches messages through the appropriate [Notification Channels](../services/notification-channels.md). The engine is accessible to clients via the [SDK Client](../libraries/sdk-client.md) and registered as an upstream service with the [API Gateway](../services/api-gateway.md).

## Overview

The Notification Engine handles all outbound communication from the platform:

- **Event-Driven Triggering** -- Notifications are triggered by domain events consumed from the Event Bus (e.g., `deployment.completed`, `alert.fired`, `user.invited`).
- **Multi-Channel Delivery** -- Messages are dispatched to email, SMS, push notifications, webhooks, and in-app channels based on recipient preferences.
- **Template Rendering** -- Notification content is rendered from Handlebars templates with dynamic data binding.
- **Delivery Tracking** -- Every notification is tracked through its full lifecycle: queued, sent, delivered, failed, bounced.
- **Retry Logic** -- Failed deliveries are retried with exponential backoff and dead-letter routing for permanent failures.

## Architecture

The engine is implemented as a set of Kafka consumer workers written in Node.js (TypeScript), backed by PostgreSQL for notification state and Redis for deduplication and rate limiting.

```
Event Bus (Kafka) -> Consumer Workers -> Template Renderer -> Channel Router -> Channels
                                                                                  |
                                                                           [Email, SMS,
                                                                            Push, Webhook]
```

The engine scales horizontally by adding consumer workers to the Kafka consumer group. Partitioning is based on recipient ID, ensuring ordered delivery per recipient.

## Event Consumption

The engine subscribes to a set of Kafka topics defined in its configuration:

```yaml
subscriptions:
  - topic: "platform.deployments"
    events:
      - "deployment.completed"
      - "deployment.failed"
    notification_type: "deployment-status"

  - topic: "platform.alerts"
    events:
      - "alert.fired"
      - "alert.resolved"
    notification_type: "alert-notification"

  - topic: "platform.users"
    events:
      - "user.invited"
      - "user.role-changed"
    notification_type: "user-lifecycle"
```

Each event is matched to a notification type, which determines the template, default channels, and priority.

## Template Engine

Notification templates are written in Handlebars and stored in a versioned template registry:

```handlebars
<!-- templates/deployment-status/email.hbs -->
<h2>Deployment {{status}}: {{service_name}}</h2>

<p>Hi {{recipient.first_name}},</p>

<p>The deployment of <strong>{{service_name}}</strong> to
<code>{{environment}}</code> has {{status}}.</p>

{{#if (eq status "failed")}}
<p>Error details:</p>
<pre>{{error_message}}</pre>
<p><a href="{{dashboard_url}}">View deployment logs</a></p>
{{/if}}

<p>-- The Acme Platform Team</p>
```

Templates support conditional logic, iteration, and custom helpers. Each channel (email, SMS, push) has its own template variant to optimize content for the delivery medium.

## Recipient Resolution

When a notification is triggered, the engine resolves the recipients:

1. **Direct recipients** -- Specified explicitly in the event payload.
2. **Role-based recipients** -- Resolved from RBAC roles (e.g., all `team:ops:admin` members).
3. **Subscription-based recipients** -- Users who have subscribed to specific notification types.

Recipient preferences are checked to determine which channels to use:

```json
{
  "user_id": "u-abc123",
  "preferences": {
    "deployment-status": {
      "channels": ["email", "push"],
      "quiet_hours": { "start": "22:00", "end": "07:00", "timezone": "America/New_York" }
    },
    "alert-notification": {
      "channels": ["email", "sms", "push"],
      "quiet_hours": null
    }
  }
}
```

Quiet hours are respected for non-critical notifications. Critical alerts (P1/P2) override quiet hours.

## Delivery Pipeline

The delivery pipeline processes each notification through the following stages:

1. **Deduplication** -- Check Redis for duplicate event IDs within a 24-hour window.
2. **Recipient Resolution** -- Resolve and expand the recipient list.
3. **Preference Check** -- Filter channels based on recipient preferences.
4. **Template Rendering** -- Render the notification content for each channel.
5. **Rate Limiting** -- Apply per-recipient, per-channel rate limits.
6. **Dispatch** -- Send the notification to the [Notification Channels](../services/notification-channels.md) subsystem.
7. **Tracking** -- Record the delivery attempt in PostgreSQL.

## Retry Logic

Failed deliveries are retried with exponential backoff:

```yaml
retry_policy:
  max_attempts: 5
  initial_delay: 30s
  max_delay: 1h
  backoff_multiplier: 2
  retryable_errors:
    - "TIMEOUT"
    - "RATE_LIMITED"
    - "TEMPORARY_FAILURE"
  non_retryable_errors:
    - "INVALID_RECIPIENT"
    - "PERMANENT_BOUNCE"
    - "UNSUBSCRIBED"
```

After exhausting all retry attempts, the notification is routed to a dead-letter queue for manual review. Operators receive an alert via a separate high-priority notification path.

## REST API

The engine exposes a REST API through the [API Gateway](../services/api-gateway.md) for programmatic notification management:

```bash
# Send a notification directly
POST /api/v2/notifications/send
Authorization: Bearer <token>
Content-Type: application/json

{
  "type": "deployment-status",
  "recipients": ["u-abc123", "team:ops"],
  "data": {
    "service_name": "payment-service",
    "environment": "production",
    "status": "completed"
  },
  "priority": "normal"
}

# Query notification history
GET /api/v2/notifications?recipient=u-abc123&status=delivered&limit=50
Authorization: Bearer <token>
```

## Metrics and Monitoring

The engine exports Prometheus metrics for operational visibility:

- `notifications_processed_total{type, channel, status}` -- Total notifications by type, channel, and delivery status.
- `notifications_delivery_duration_seconds{channel}` -- End-to-end delivery latency by channel.
- `notifications_retry_total{channel, attempt}` -- Retry attempts by channel.
- `notifications_dlq_total{type}` -- Dead-letter queue entries by notification type.
- `notifications_template_render_duration_seconds` -- Template rendering latency.

A Grafana dashboard provides real-time visibility into delivery rates, failure rates, and channel health.
