---
id: notification-channels
title: Notification Channels
type: System
status: stable
tags: [notifications, email, sms, push]
links:
  - target: notification-engine
    relation: part-of
    label: Engine subsystem
  - target: event-bus
    relation: receives-from
    label: Channel events
sources:
  - id: notif-channels
    resource: /source_docs/notification-engine/channels.md
    title: Notification Channels Guide
generated:
  by: "human:author"
  at: "2026-10-01T00:00:00Z"
---

# Notification Channels

Notification Channels are the delivery mechanisms used by the [Notification Engine](../services/notification-engine.md) to send messages to recipients. Each channel is implemented as an independent adapter with its own configuration, rate limits, and error handling. Channel status events are published back to the [Event Bus](../services/event-bus.md) for tracking and analytics.

## Channel Overview

The platform supports four delivery channels:

| Channel | Provider | Use Case | Latency (p95) |
|---|---|---|---|
| Email | AWS SES | Detailed notifications, reports | < 5s |
| SMS | Twilio | Urgent alerts, MFA codes | < 3s |
| Push | FCM (Android), APNs (iOS) | Real-time mobile alerts | < 1s |
| Webhook | HTTP POST | System integrations, automation | < 2s |

## Email Channel

Email delivery is handled through AWS Simple Email Service (SES). The channel supports HTML and plain-text content with inline images and attachments.

```yaml
channels:
  email:
    provider: aws-ses
    region: us-east-1
    configuration:
      from_address: "notifications@acme-platform.io"
      reply_to: "support@acme-platform.io"
      configuration_set: "acme-tracking"
      max_send_rate: 50  # per second
    bounce_handling:
      sns_topic: "arn:aws:sns:us-east-1:123456789:ses-bounces"
      hard_bounce_action: "suppress"
      complaint_action: "unsubscribe"
```

SES event notifications (delivery, bounce, complaint) are ingested via SNS and used to update recipient suppression lists and delivery metrics.

## SMS Channel

SMS delivery is handled through the Twilio Messaging API. Messages are sent from a pool of phone numbers to distribute sending load.

```yaml
channels:
  sms:
    provider: twilio
    configuration:
      account_sid: "${TWILIO_ACCOUNT_SID}"
      messaging_service_sid: "${TWILIO_MESSAGING_SERVICE_SID}"
      max_message_length: 160
      rate_limit:
        per_recipient: 5  # per hour
        global: 1000  # per minute
    fallback:
      provider: aws-sns
      region: us-east-1
```

The SMS channel includes a fallback provider (AWS SNS) that activates if the primary provider experiences an outage.

## Push Channel

Push notifications are delivered via Firebase Cloud Messaging (FCM) for Android and Apple Push Notification service (APNs) for iOS.

```yaml
channels:
  push:
    providers:
      android:
        type: fcm
        credentials: "${FCM_SERVICE_ACCOUNT_JSON}"
        ttl: 3600
      ios:
        type: apns
        key_id: "${APNS_KEY_ID}"
        team_id: "${APNS_TEAM_ID}"
        bundle_id: "io.acme-platform.app"
        environment: production
    configuration:
      collapse_key_enabled: true
      priority: high
      badge_count_enabled: true
```

Push notifications support rich content (images, action buttons) and collapse keys to prevent notification flooding on the client device.

## Webhook Channel

Webhooks deliver notifications as HTTP POST requests to user-configured endpoints. This channel is used for integrating with external systems like Slack, PagerDuty, and custom automation.

```yaml
channels:
  webhook:
    configuration:
      timeout: 10s
      max_retries: 3
      retry_backoff: exponential
      signature_algorithm: hmac-sha256
      signature_header: "X-Acme-Signature"
      max_payload_size: 64KB
```

Every webhook request includes an HMAC-SHA256 signature in the `X-Acme-Signature` header, computed over the request body using the endpoint's secret key. Recipients should verify this signature to authenticate the webhook.

Example webhook payload:

```json
{
  "event_id": "evt-abc123",
  "event_type": "deployment.completed",
  "timestamp": "2026-09-15T14:23:01Z",
  "data": {
    "service_name": "payment-service",
    "environment": "production",
    "status": "completed",
    "duration_seconds": 142
  }
}
```

## Channel Health Monitoring

Each channel reports its health status via circuit breakers. If a channel exceeds its error threshold, the circuit opens and the Notification Engine falls back to alternative channels or queues messages for later delivery.

```yaml
circuit_breaker:
  error_threshold: 0.1   # 10% error rate
  window_size: 60s
  cooldown: 120s
  half_open_requests: 5
```

Channel health metrics are published to the [Event Bus](../services/event-bus.md) and displayed on the operations dashboard.
